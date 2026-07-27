"""사업 개요 파이프라인 — 공시(DART) → DB(BusinessReportRaw) → Cache(BusinessOverviewCache).

가장 최근 사업보고서(annual)의 '사업의 내용'을 베이스로, 그 이후 발행된 반기·분기보고서의
'회사의 개황'(최근 경영사항)을 오버레이해 투자자 관점(테이블 중심)으로 정리정돈한다. 원문 그대로
옮기지 않고 LLM 이 정리한다(synthesize + review 루프 — 딥다이브 review_loop 재사용).

흐름:
1. extract_sections — 정기보고서 document.xml 에서 조악한 섹션 본문 추출 → BusinessReportRaw.
2. assemble_overview — annual 베이스 + half/quarter 오버레이 → LLM 정리 → BusinessOverviewCache.
3. backfill_progressive — 유니버스 점진 백필(SyncState 마커·DART quota/budget 가드·재개 가능).
4. refresh_if_new_report — 캐시 source_reports 대비 새 rcept 감지 시 재조립(배치 갱신).
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from datetime import UTC, date, datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters import dart
from app.adapters.dart import report_parser as dart_report_parser
from app.adapters.dart import throttle as dart_throttle
from app.adapters.llm.factory import get_llm
from app.config import Settings, get_settings
from app.db.models import (
    BusinessOverviewCache,
    BusinessReportRaw,
    CorpCodeMap,
    SegmentSales,
    SyncState,
    UniverseSnapshot,
)
from app.domain import business_overview as bo
from app.ports.llm import LLMError, LLMPort
from app.services import company_service
from app.services.deepdive import review_loop
from app.services.sentiment import _extract_json

logger = logging.getLogger(__name__)

_BACKFILL_DOMAIN = "business_overview"
_PER_RUN = 50  # 보고서당 document.xml 다운로드라 무거움 → 하룻밤 소수(report_ingest 와 같은 기준)

# 정기보고서 원문에서 찾을 섹션 앵커(정규화=공백 제거 후 매칭). annual/half/quarter 용.
# '사업의 내용' 은 사업보고서 II장 전체. '회사의 개황' 은 분기/반기 I장(당해 기간 경영사항).
_ANCHORS = {
    bo.SECTION_BUSINESS_CONTENT: ("사업의내용",),
    bo.SECTION_COMPANY_OVERVIEW: ("회사의개황", "개황"),
}
# 섹션 종료 앵커(다음 대장). 사업의 내용 → '임원등에관한사항'·'자본금' 등. 개황 → '주주구성'·'경영의견'.
_END_ANCHORS = (
    "임원등에관한사항",
    "임원등에관한",
    "자본금및주식",
    "자본금에관한",
    "주식의총수",
    "주주구성",
    "경영에관한사항",
    "경영의견",
    "이사·감사",
    "이사·감사의",
    "감사의의견",
)


# ── 원문 텍스트 추출 ──────────────────────────────────────────────────────
def _strip_tags(raw: bytes) -> str:
    """XML 태그를 제거해 텍스트로. 셀·문단 구분을 위해 <br>·</TR> 은 개행으로 보존."""
    try:
        text = raw.decode("utf-8", errors="ignore")
    except Exception:
        text = raw.decode("utf-8", errors="ignore")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(TR|tr|P|p|TD|td|TE|te)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z]+;|&#\d+;", " ", text)
    return re.sub(r"[ \t]+", " ", text)


def _full_text(zip_bytes: bytes) -> str:
    """document.xml zip → 전체 본문 텍스트. 연결(_00761) 우선, 파일순 합치기."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = [n for n in zf.namelist() if n.endswith(".xml")]
            if not names:
                return ""
            # 연결 재무제표 파일(_00761.xml)을 앞으로(사업의 내용은 연결 기준이 주).
            names.sort(key=lambda n: (0 if n.endswith("_00761.xml") else 1, n))
            parts = [_strip_tags(zf.read(n)) for n in names]
    except (zipfile.BadZipFile, KeyError):
        return ""
    return "\n".join(p for p in parts if p)


def _find_anchor_positions(full: str, anchors: tuple[str, ...]) -> list[int]:
    """정규화된 전문에서 앵커가 나타나는 원문 오프셋 목록. TOC(목차) 항목은 건너뛴다.

    TOC 판정: 앵커 '같은 줄 나머지'가 점리더(....)·페이지번호(숫자)만으로 끝나면 목차.
    본문 헤딩은 같은 줄에 서술이 오거나 줄이 바로 끝나므로 목치 패턴과 구별된다.
    ATOCID(목차 XML 마커)가 앞 80자에 있어도 목차로 본다.
    """
    out: list[int] = []
    for anchor in anchors:
        # 띄어쓰기 무시 패턴: 각 글자 사이에 \s* 허용(Korean heading 은 띄어쓰기 편차가 큼).
        pattern = r"\s*".join(re.escape(ch) for ch in anchor)
        for m in re.finditer(pattern, full):
            pos = m.start()
            pre = full[max(0, pos - 80) : pos]
            if "ATOCID" in pre:
                continue
            # 같은 줄 나머지 — 점리더/숫자만이면 목차 항목.
            line_rest = full[m.end() :]
            nl = line_rest.find("\n")
            rest = line_rest[:nl] if nl >= 0 else line_rest
            if rest.strip() and re.fullmatch(r"\s*[.\s…·]*\d*\s*", rest):
                continue
            out.append(pos)
    return sorted(set(out))


def _slice_section(full: str, start_pos: int) -> str:
    """start_pos(섹션 헤딩) 부터 다음 종료 앵커(또는 문서 끝)까지 슬라이스. 30k 자로 절단.

    헤딩 라인 자체는 건너뛰고 본문부터 종료 앵커를 탐색(헤딩 내 토큰 오탐 방지).
    """
    tail = full[start_pos:]
    nl = tail.find("\n")
    body = tail[nl + 1 :] if nl >= 0 else tail
    end_pos = len(body)
    for anchor in _END_ANCHORS:
        pattern = r"\s*".join(re.escape(ch) for ch in anchor)
        m = re.search(pattern, body)
        if m and m.start() < end_pos:
            end_pos = m.start()
    chunk = body[: min(end_pos, 30_000)]
    return re.sub(r"\n{3,}", "\n\n", chunk).strip()


def extract_sections(
    settings: Settings, corp_code: str, rcept_no: str, kind: str, session: requests.Session
) -> dict[str, str]:
    """정기보고서 document.xml 에서 종류별 조악 섹션 본문 추출 → {section_id: text}.

    annual → '사업의 내용' 전체. half/quarter → '회사의 개황'. 추출 실패 시 빈 dict.
    DART 한도초과는 삼키지 않고 전파(불완전 데이터로 조립 강행 방지).
    """
    section_id = bo.section_id_for_kind(kind)
    zip_bytes = dart_report_parser.fetch_report_zip(settings.dart_api_key, rcept_no, session)
    if not zip_bytes:
        return {}
    full = _full_text(zip_bytes)
    if not full:
        return {}
    anchors = _ANCHORS[section_id]
    positions = _find_anchor_positions(full, anchors)
    if not positions:
        # 앵커 못 찾으면 전문 앞부분 폴백(LLM 이 판단). 최대 12k 자.
        logger.info("business section anchor not found %s %s — fallback head", kind, rcept_no)
        return {section_id: full[:12_000].strip()} if full else {}
    text = _slice_section(full, positions[0])
    return {section_id: text} if text else {}


# ── DB 원문 적재 ──────────────────────────────────────────────────────────
_REPORT_PERIOD_MONTH = {"annual": 12, "half": 6, "quarter": 3}


def _period_str(year: int, kind: str) -> str:
    return f"{year}.{_REPORT_PERIOD_MONTH[kind]:02d}"


def _store_raw(
    db: Session, code: str, rcept_no: str, kind: str, period: str, sections: dict[str, str]
) -> None:
    """추출 섹션을 BusinessReportRaw 에 upsert. 빈 텍스트는 건너뛴다(빈 저장 방지)."""
    for section_id, text in sections.items():
        if not text:
            continue
        stmt = insert(BusinessReportRaw).values(
            stock_code=code,
            rcept_no=rcept_no,
            report_kind=kind,
            period=period,
            section_id=section_id,
            text=text,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_business_raw",
            set_={"text": text, "report_kind": kind, "period": period, "fetched_at": func.now()},
        )
        db.execute(stmt)
    db.commit()


def _load_raw(db: Session, code: str, rcept_no: str) -> dict[str, str]:
    """저장된 원문 섹션 조회 → {section_id: text}."""
    rows = db.execute(
        select(BusinessReportRaw.section_id, BusinessReportRaw.text).where(
            BusinessReportRaw.stock_code == code, BusinessReportRaw.rcept_no == rcept_no
        )
    ).all()
    return {sid: txt for sid, txt in rows if txt}


# ── 정기보고서 발견 ────────────────────────────────────────────────────────
def _candidate_reports(
    settings: Settings, corp_code: str, session: requests.Session
) -> list[tuple[str, str, int]]:
    """최근 2년(올해·작년)의 사업/반기/분기 정기보고서 → [(rcept_no, kind, year)].

    사업보고서는 다음 해 제출이라 올해 연도분은 아직 없을 수 있어 작년 연도부터 잡는다.
    rcept_no 시간순 증가 — 정렬 기준. DART 한도초과 전파.
    """
    today = date.today()
    found: list[tuple[str, str, int]] = []
    for year in (today.year, today.year - 1, today.year - 2):
        for kind in bo.PERIODIC_KINDS:
            rcept = dart.find_periodic_report(settings.dart_api_key, corp_code, year, kind, session)
            if rcept:
                found.append((rcept, kind, year))
    return found


def _gather_for_assembly(
    settings: Settings, corp_code: str, session: requests.Session
) -> list[tuple[str, str, int]]:
    """조립 대상 정기보고서: 베이스 사업보고서(최신 annual) + 그 이후 half/quarter.

    반환: [(rcept_no, kind, year)] 오름차순(annual 이 첫, 이후 half/quarter 시간순).
    annual 이 없으면 빈 리스트(조립 불가 — 사업보고서가 베이스).
    """
    cands = _candidate_reports(settings, corp_code, session)
    if not cands:
        return []
    annuals = [(r, y) for (r, k, y) in cands if k == bo.ANNUAL]
    if not annuals:
        return []
    base_rcept, base_year = max(annuals, key=lambda t: t[0])
    # 베이스 사업보고서 이후(같은 rcept_no 이후)의 half/quarter 만 오버레이.
    later = [(r, k, y) for (r, k, y) in cands if k != bo.ANNUAL and r > base_rcept]
    later.sort(key=lambda t: t[0])
    return [(base_rcept, bo.ANNUAL, base_year), *later]


# ── 조립(LLM 정리정돈) ───────────────────────────────────────────────────
_ASSEMBLE_SYSTEM = (
    "너는 한국 상장사 사업보고서를 투자자 관점으로 정리하는 애널리스트다. 주어진 정기보고서 원문을 "
    "원문 그대로 옮기지 말고, 투자자가 회사 사업을 빠르게 파악할 수 있도록 정리정돈한다. **테이블을 "
    "적극 활용**한다(제품·매출비중·고객·원재료·위험·주주구성 등). 서술은 핵심만 간결한 마크다운. "
    "원문에 없는 내용은 추측하지 말고 '정보 없음'으로 둔다. 출처 원문에 근거한다.\n\n"
    "출력은 반드시 아래 JSON 스키마만(다른 텍스트 금지):\n"
    '{"sections": [{"id": "<INVESTOR_SECTIONS 중 하나>", "title": "섹션 제목", '
    '"narrative": "마크다운 서술(핵심만)", "tables": [{"title": "표 제목", "headers": ["..."], '
    '"rows": [["...", ...]]}], "updated_by_kind": "annual|half|quarter|null"}]}\n\n'
    "반드시 포함할 섹션 id: " + ", ".join(bo.INVESTOR_SECTIONS) + ". "
    "데이터가 없는 섹션도 빈 narrative/tables 로 포함(누락 금지). 'recent_updates' 섹션은 "
    "반기·분기 원문(updates)에서만 채우고, 갱신분이 없으면 빈 값. updated_by_kind 는 해당 섹션이 "
    "어떤 종류 보고서에서 왔는지(annual 베이스면 'annual', 반기·분기 갱신이면 그 kind)."
)

_ASSEMBLE_REVIEW = (
    "너는 사업 개요 조립 단계의 절차 감사자다. 다음 절차를 점검한다:\n"
    "1) 각 INVESTOR_SECTIONS(business_summary, main_products, market_risk, raw_materials, "
    "production, sales, ownership, recent_updates) 가 모두 포함됐나 — 누락 없이.\n"
    "2) 표가 적극 활용됐나 — 서술만 있는 섹션이 아닌가(제품·고객·위험·주주는 표 우선).\n"
    "3) 원문에 없는 내용을 추측·일반론으로 채우지 않았나 — '정보 없음' 명시 여부.\n"
    "4) recent_updates 가 반기·분기 갱신분(updates 원문)에 근거하나 — annual 베이스를 반복하지 않았나."
)


def _build_context(db: Session, code: str, reports: list[tuple[str, str, int]]) -> dict:
    """조립 컨텍스트: annual 베이스 원문 + half/quarter 갱신 원문 목록. 원문은 DB 에서."""
    base_rcept, base_kind, base_year = reports[0]
    base_text = _load_raw(db, code, base_rcept).get(bo.SECTION_BUSINESS_CONTENT, "")
    updates = []
    for rcept, kind, year in reports[1:]:
        text = _load_raw(db, code, rcept).get(bo.SECTION_COMPANY_OVERVIEW, "")
        if text:
            updates.append(
                {
                    "rcept_no": rcept,
                    "kind": kind,
                    "period": _period_str(year, kind),
                    "text": text[:8000],
                }
            )
    name = (
        company_service.report_stock_name(db, code)
        or company_service.resolve_stock_name(db, code)
        or ""
    )
    return {
        "stock_code": code,
        "stock_name": name,
        "base": {
            "rcept_no": base_rcept,
            "kind": base_kind,
            "period": _period_str(base_year, base_kind),
            "text": base_text[:16000],
        },
        "updates": updates,
    }


def _produce(llm: LLMPort, model: str, ctx: dict, feedback: str | None) -> dict:
    """producer(feedback) → LLM synthesize. review_loop 가 feedback(절차 지적)을 주입해 재작업."""
    user = (
        f"[종목] {ctx['stock_code']} {ctx['stock_name']}\n\n"
        f"[베이스 사업보고서 원문 — 사업의 내용]\n{ctx['base']['text']}\n\n"
        f"[반기·분기 갱신 원문 — 회사의 개황]\n"
        + "\n---\n".join(f"({u['kind']} {u['period']})\n{u['text']}" for u in ctx["updates"])
    )
    if feedback:
        user += f"\n\n**[이전 검토 절차 지적 — 보완하라]**\n{feedback}"
    try:
        raw = llm.chat(model, _ASSEMBLE_SYSTEM, user, temperature=0.3)
    except LLMError as e:
        logger.warning("business assemble LLM failed %s: %s", ctx["stock_code"], e)
        return {"_error": f"LLM 실패: {e}", "_partial": True}
    data = _extract_json(raw)
    if not data:
        return {"_note": "비정형 응답", "_text": raw[:2000]}
    return data


def _map_sections(result: dict, reports: list[tuple[str, str, int]]) -> list[dict]:
    """LLM 결과 sections → 표준 섹션 리스트(누락 id 는 빈 값으로 채운다). updated_by_rcept 보정."""
    by_id = {s.get("id"): s for s in result.get("sections", []) if isinstance(s, dict)}
    # updated_by_rcept 매핑: kind → 그 kind 의 rcept_no(reports 에서).
    kind_to_rcept = {kind: rcept for rcept, kind, _y in reports}
    out = []
    for sid in bo.INVESTOR_SECTIONS:
        s = by_id.get(sid, {})
        ubk = s.get("updated_by_kind")
        out.append(
            {
                "id": sid,
                "title": s.get("title", sid),
                "narrative": s.get("narrative", ""),
                "tables": s.get("tables", []) or [],
                "updated_by_rcept": kind_to_rcept.get(ubk) if ubk else None,
                "updated_by_kind": ubk or None,
            }
        )
    return out


def _inputs_hash(reports: list[tuple[str, str, int]]) -> str:
    """원문 집합(rcept_no 모음) 해시 — 바뀌면 재조립."""
    payload = "|".join(r for r, _k, _y in reports)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# 사업보고서(annual) DART 보고서 코드 — iotHom3MdQe reprt_code 파라미터.
_ANNUAL_REPORT_CODE = "11011"


def fetch_segment_sales_for(
    db: Session,
    settings: Settings,
    code: str,
    corp_code: str,
    base_year: int,
    base_rcept: str,
    session: requests.Session,
) -> int:
    """iotHom3MdQe(부문별 매출) 조회 → segment_sales 테이블 영속. 저장 행 수 반환.

    사업보고서(11011) 연간 기준. 별도 DART 호출(연간 +1) — 종목당 할당량 소비 감수(사용자 결정).
    실패·데이터없음·한도초과 시 0(조립 중단 아님 — 부문 매출은 보강 정보). 이미 저장된 연도는 스킵.
    """
    if not settings.dart_api_key:
        return 0
    # 같은 (stock_code, bsns_year, report_code) 이미 있으면 재조회 스킵(연간 1회).
    exists = db.scalar(
        select(func.count())
        .select_from(SegmentSales)
        .where(
            SegmentSales.stock_code == code,
            SegmentSales.bsns_year == str(base_year),
            (SegmentSales.report_code == base_rcept) if base_rcept else True,
        )
    )
    if exists:
        return 0
    rows = dart.fetch_segment_sales(
        settings.dart_api_key, corp_code, base_year, _ANNUAL_REPORT_CODE, session
    )
    if not rows:
        return 0
    for r in rows:
        stmt = insert(SegmentSales).values(
            stock_code=code,
            bsns_year=r.bsns_year,
            report_code=r.report_code,
            segment_type=r.segment_type,
            segment_name=r.segment_name,
            revenue=r.revenue,
            ratio_pct=r.ratio_pct,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_segment_sales",
            set_={
                "revenue": stmt.excluded.revenue,
                "ratio_pct": stmt.excluded.ratio_pct,
            },
        )
        db.execute(stmt)
    db.commit()
    logger.info("segment_sales %s %s: %d rows", code, base_year, len(rows))
    return len(rows)


def assemble_overview(db: Session, settings: Settings, code: str) -> dict | None:
    """종목 사업 개요 조립 → BusinessOverviewCache 저장 + 페이로드 반환. 사업보고서 없으면 None.

    1) 대상 정기보고서 발견 → 2) 누락 원문 추출·적재 → 3) LLM 정리정돈(review 루프) → 4) 캐시 저장.
    LLM 미설정 시 원문만 적재하고 None 반환(조립 불가 — 캐시 미생성).
    """
    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
    if not corp_code or not settings.dart_api_key:
        return None
    llm = get_llm(settings)
    if llm is None:
        logger.info("business assemble %s: LLM 미설정 — 원문만 적재 가능", code)

    with requests.Session() as session:
        reports = _gather_for_assembly(settings, corp_code, session)
    if not reports:
        return None

    # 누락 원문 추출·적재(DART 호출). 실패(한도초과)는 전파해 조립 중단.
    with requests.Session() as session:
        for rcept, kind, year in reports:
            if _load_raw(db, code, rcept):
                continue
            sections = extract_sections(settings, corp_code, rcept, kind, session)
            if sections:
                _store_raw(db, code, rcept, kind, _period_str(year, kind), sections)

        # 부문별 매출(iotHom3MdQe) — 베이스 사업보고서 연간. 구조화 데이터라 LLM 과 무관하게 영속.
        # 실패·한도초과는 조립 중단 아님(보강 정보). DART 할당량 소비 — 연간 1회/종목.
        base_rcept_pre, _base_kind_pre, base_year_pre = reports[0]
        try:
            fetch_segment_sales_for(
                db, settings, code, corp_code, base_year_pre, base_rcept_pre, session
            )
        except Exception as e:
            logger.warning("segment_sales %s skipped: %s", code, e)

    if llm is None:
        return None  # 원문만 적재, 조립은 LLM 필요

    ctx = _build_context(db, code, reports)
    if not ctx["base"]["text"]:
        logger.info("business assemble %s: 베이스 원문 없음 — 조립 생략", code)
        return None

    model = settings.insight_model
    result = review_loop.run_with_review(
        llm,
        model,
        lambda fb: _produce(llm, model, ctx, fb),
        _ASSEMBLE_REVIEW,
        label=f"business:{code}",
    )
    if review_loop.result_is_error(result):
        logger.warning("business assemble %s: LLM 미완 — 캐시 생략", code)
        return None

    base_rcept, _base_kind, _base_year = reports[0]
    name = ctx["stock_name"]
    payload = {
        "stock_code": code,
        "stock_name": name,
        "as_of_annual_rcept": base_rcept,
        "source_reports": [
            {"rcept_no": r, "kind": k, "period": _period_str(y, k), "is_base": (r == base_rcept)}
            for r, k, y in reports
        ],
        "sections": _map_sections(result, reports),
        "research_summary": None,
    }
    _store_cache(
        db, code, name, base_rcept, payload["source_reports"], _inputs_hash(reports), payload
    )
    logger.info(
        "business assemble %s: %d 섹션, base=%s, %d updates",
        code,
        len(payload["sections"]),
        base_rcept,
        len(reports) - 1,
    )
    return payload


# ── 캐시(cache-aside) ─────────────────────────────────────────────────────
_BUSINESS_CACHE_TTL = timedelta(hours=12)


def get_cached_overview(db: Session, code: str) -> dict | None:
    """BusinessOverviewCache 조회. TTL 만료 또는 없으면 None."""
    row = db.scalar(select(BusinessOverviewCache).where(BusinessOverviewCache.stock_code == code))
    if row is None or row.cached_at is None:
        return None
    # SQLite 등 일부 방언은 tz 없이(naive) 저장하므로 비교 전 UTC 로 정규화(postgres 는 tz-aware 그대로).
    cached_at = row.cached_at if row.cached_at.tzinfo else row.cached_at.replace(tzinfo=UTC)
    if datetime.now(UTC) - cached_at >= _BUSINESS_CACHE_TTL:
        return None
    return row.payload


def _store_cache(
    db: Session,
    code: str,
    name: str,
    base_rcept: str,
    source_reports: list,
    inputs_hash: str,
    payload: dict,
) -> None:
    """BusinessOverviewCache upsert — (code) 당 1행 유지."""
    stmt = insert(BusinessOverviewCache).values(
        stock_code=code,
        stock_name=name,
        as_of_annual_rcept=base_rcept,
        source_reports=source_reports,
        inputs_hash=inputs_hash,
        payload=payload,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_business_overview_code",
        set_={
            "stock_name": name,
            "as_of_annual_rcept": base_rcept,
            "source_reports": source_reports,
            "inputs_hash": inputs_hash,
            "payload": payload,
            "cached_at": func.now(),
        },
    )
    db.execute(stmt)
    db.commit()


def invalidate_cache(db: Session, code: str) -> None:
    """캐시 무효화 — code 행 삭제."""
    db.execute(
        BusinessOverviewCache.__table__.delete().where(BusinessOverviewCache.stock_code == code)
    )
    db.commit()


def _merge_research_into_cache(db: Session, code: str, research_summary: dict) -> None:
    """리서치 결과를 BusinessOverviewCache.payload["research_summary"]에 병합.

    기존 overview가 있으면 그 payload의 research_summary만 갱신(sections/source_reports/inputs_hash
    보존). 없으면 빈 overview 스텁을 생성(빈 sections는 프론트가 숨김 → 리서치만 표시).

    이 함수는 assemble_overview를 경유하지 않아야 함 — assemble_overview는 research_summary를
    None으로 리셋하므로, 경유하면 리서치가 사라짐.
    """
    row = db.scalar(select(BusinessOverviewCache).where(BusinessOverviewCache.stock_code == code))
    if row is None:
        # 스텁 생성: 빈 sections/source_reports, stock_name은 추후 채워짐.
        stub_payload = {
            "stock_code": code,
            "stock_name": "",
            "as_of_annual_rcept": "",
            "source_reports": [],
            "sections": [],
            "research_summary": research_summary,
        }
        _store_cache(
            db,
            code,
            "",  # stock_name (비어도 OK)
            "",  # as_of_annual_rcept
            [],  # source_reports
            "",  # inputs_hash (없음)
            stub_payload,
        )
        return

    # 기존 payload에 research_summary만 덮어쓰기.
    payload = row.payload or {}
    payload["research_summary"] = research_summary

    # 기존 inputs_hash/source_reports/as_of_annual_rcept를 보존한 upsert (refresh_if_new_report 오트리거 방지).
    _store_cache(
        db,
        code,
        row.stock_name or "",
        row.as_of_annual_rcept or "",
        row.source_reports or [],
        row.inputs_hash or "",
        payload,
    )


# ── 백필 ─────────────────────────────────────────────────────────────────
def _universe_codes(db: Session) -> list[str]:
    """유니버스 최신 스냅샷의 보통주 종목코드(우선주 제외). report_ingest._universe_codes 와 동일."""
    as_of = db.scalar(
        select(UniverseSnapshot.snapshot_date)
        .where(UniverseSnapshot.stock_type == "stock")
        .order_by(UniverseSnapshot.snapshot_date.desc())
        .limit(1)
    )
    if as_of is None:
        return []
    return list(
        db.scalars(
            select(UniverseSnapshot.stock_code).where(
                UniverseSnapshot.snapshot_date == as_of,
                UniverseSnapshot.stock_type == "stock",
                ~UniverseSnapshot.stock_name.op("~")(r"우[A-C]?$"),
            )
        ).all()
    )


def _done_codes(db: Session) -> set[str]:
    return set(
        db.scalars(select(SyncState.stock_code).where(SyncState.domain == _BACKFILL_DOMAIN)).all()
    )


def backfill_stock(db: Session, settings: Settings, code: str) -> bool:
    """한 종목 사업 개요 조립(원문 추출+LLM 정리) → 캐시. 성공(또는 데이터없음 확정) 시 True."""
    try:
        payload = assemble_overview(db, settings, code)
    except dart.DartQuotaExceeded:
        raise  # 상위 배치가 중단
    if payload is None:
        # 사업보고서 없거나 LLM 미설정. 원문이라도 적재했을 수 있음 — 완료로 본다(재조회 방지).
        return True
    return True


def run_backfill_progressive(
    db: Session, settings: Settings | None = None, per_run: int = _PER_RUN
) -> dict:
    """유니버스 종목 사업 개요 점진 백필(하룻밤 per_run 개, 재개 가능). report_ingest 패턴."""
    settings = settings or get_settings()
    if not settings.dart_api_key:
        return {"done": 0, "failed": 0, "remaining": 0}
    codes = _universe_codes(db)
    if not codes:
        return {"done": 0, "failed": 0, "remaining": 0}
    done = _done_codes(db)
    pending = [c for c in codes if c not in done]
    batch = pending[:per_run]
    done_n = failed_n = 0
    quota_hit = budget_hit = False
    for code in batch:
        if dart_throttle.backfill_budget_exhausted():
            budget_hit = True
            logger.info("business backfill: 예산 소진 — 조기 중단(%d 종목处理后)", done_n)
            break
        try:
            backfill_stock(db, settings, code)
            _mark_done(db, code)
            done_n += 1
        except dart.DartQuotaExceeded:
            db.rollback()
            quota_hit = True
            logger.warning("business backfill: DART 한도초과 — 중단(%d 종목处理后)", done_n)
            break
        except Exception as e:
            db.rollback()
            failed_n += 1
            logger.warning("business backfill failed %s: %s", code, e)
    remaining = len(pending) - done_n
    logger.info(
        "business backfill: done=%d failed=%d remaining=%d quota_hit=%s budget_hit=%s",
        done_n,
        failed_n,
        remaining,
        quota_hit,
        budget_hit,
    )
    return {
        "done": done_n,
        "failed": failed_n,
        "remaining": remaining,
        "quota_hit": quota_hit,
        "budget_hit": budget_hit,
    }


def _mark_done(db: Session, code: str) -> None:
    """SyncState 도메인 business_overview 완료 마커."""
    stmt = insert(SyncState).values(domain=_BACKFILL_DOMAIN, stock_code=code)
    stmt = stmt.on_conflict_do_update(constraint="uq_sync_state", set_={"synced_at": func.now()})
    db.execute(stmt)
    db.commit()


# ── 배치 갱신(새 정기보고서 감지) ──────────────────────────────────────────
def refresh_if_new_report(db: Session, settings: Settings, code: str) -> bool:
    """캐시 source_reports 대비 새 rcept_no 감지 시 재조립. 새 것 없으면 False(갱신 없음).

    배치 갱신의 단건 진입점. 새 정기보고서가 나오면 사업→분기→반기→분기 흐름으로 재조립.
    """
    cached = db.scalar(
        select(BusinessOverviewCache).where(BusinessOverviewCache.stock_code == code)
    )
    corp_code = db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
    if not corp_code or not settings.dart_api_key:
        return False
    with requests.Session() as session:
        reports = _gather_for_assembly(settings, corp_code, session)
    if not reports:
        return False
    new_hash = _inputs_hash(reports)
    if cached is not None and cached.inputs_hash == new_hash:
        return False  # 동일 원문 집합 — 갱신 불필요
    # 새 rcept 있거나 최초 → 재조립. 원문 추출은 assemble_overview 가 누락분만 채운다.
    try:
        payload = assemble_overview(db, settings, code)
    except dart.DartQuotaExceeded:
        raise
    return payload is not None


def run_refresh_batch(db: Session, settings: Settings | None = None) -> dict:
    """유니버스 순회하며 새 정기보고서 감지·재조립(배치 갱신). 캐시 없는 종목도 채운다."""
    settings = settings or get_settings()
    if not settings.dart_api_key:
        return {"refreshed": 0, "skipped": 0, "failed": 0}
    codes = _universe_codes(db)
    refreshed = skipped = failed = 0
    quota_hit = False
    for code in codes:
        try:
            if refresh_if_new_report(db, settings, code):
                refreshed += 1
            else:
                skipped += 1
        except dart.DartQuotaExceeded:
            db.rollback()
            quota_hit = True
            logger.warning("business refresh: DART 한도초과 — 중단(%d 갱신后)", refreshed)
            break
        except Exception as e:
            db.rollback()
            failed += 1
            logger.warning("business refresh failed %s: %s", code, e)
    logger.info(
        "business refresh: refreshed=%d skipped=%d failed=%d quota_hit=%s",
        refreshed,
        skipped,
        failed,
        quota_hit,
    )
    return {"refreshed": refreshed, "skipped": skipped, "failed": failed, "quota_hit": quota_hit}
