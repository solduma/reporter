"""사업 개요 파이프라인 — 공시(DART) → DB(BusinessReportRaw) → Cache(BusinessOverviewCache).

가장 최근 사업보고서(annual)의 '사업의 내용'을 베이스로, 그 이후 발행된 반기·분기보고서의
'회사의 개황'(최근 경영사항)을 오버레이해 투자자 관점(테이블 중심)으로 정리정돈한다. 원문 그대로
옮기지 않고 LLM 이 정리한다(synthesize + review 루프 — 딥다이브 review_loop 재사용).

흐름:
1. extract_sections — 정기보고서 document.xml 에서 조악한 섹션 본문 추출 → BusinessReportRaw.
2. assemble_overview — map(청크 사실추출) → reduce(주제별 카탈로그) → 섹션별 생성 → 절차 리뷰
   (gap 시 해당 섹션만 재생성) → BusinessOverviewCache. 원문 절단 없이 전체를 소형 호출로 처리.
3. backfill_progressive — 유니버스 점진 백필(SyncState 마커·DART quota/budget 가드·재개 가능).
4. refresh_if_new_report — 캐시 source_reports 대비 새 rcept 감지 시 재조립(배치 갱신).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
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
    BusinessOntologyEdge,
    BusinessOntologyNode,
    BusinessOverviewCache,
    BusinessReportRaw,
    CorpCodeMap,
    SegmentSales,
    SyncState,
    UniverseSnapshot,
)
from app.domain import business_overview as bo
from app.domain.business_research import OntologyMention
from app.ports.business_ontology import BusinessNodeType
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


# ── 조립(Map-Reduce — 소형 다중 호출) ─────────────────────────────────────
# 단일 거대 프롬프트(원문 24K자 주입 → 8섹션 동시 출력)는 무료 reasoning 모델에서 첫바이트
# 장기 침묵→엣지 끊김(RemoteDisconnected)·빈 응답·8섹션 중 일부만 출력이 잦았고, 애초에
# 원문을 16K/8K로 절단해 정보 손실도 컸다. 원문 전체를 청크 사실추출(map) → 주제별 카탈로그
# 통합(reduce) → 섹션별 개별 생성(synthesize)으로 쪼개 각 호출을 작게 유지한다.
_CHUNK_CHARS = 6000  # 청크 크기(문자) — 문단 경계 분할(~3K 토큰 수준 입력)
_FACTS_CAP = 40  # 청크당 사실 추출 상한
_CATALOG_CHAR_CAP = 14000  # reduce 1회 입력 상한 — 초과 시 그룹 병합으로 단계적 reduce

_MAP_SYSTEM = (
    "너는 한국 상장사 정기보고서 발췌문에서 투자자 관점의 '사실'만 뽑아내는 애널리스트다.\n"
    "규칙:\n"
    "1) 서술·해석·평가 금지 — 원문에 근거한 사실만 한 줄씩. 수치·비율·고유명사·날짜를 정확히 보존.\n"
    "2) 대상: 제품·서비스, 매출구성, 고객·경쟁·시장위치, 설비·운영 KPI, 재무수치, 주주·지배구조, "
    "리스크·촉매, 공급망·파트너십, R&D — 무엇이든 사실이면 추출한다.\n"
    "3) 중복은 하나로 합친다. 최대 40개. 근거 없는 추측 금지.\n"
    "출력은 반드시 아래 JSON만:\n"
    '{"facts": ["...", "..."]}'
)

_REDUCE_TOPICS = (
    "company",
    "revenue",
    "market",
    "value_chain",
    "drivers",
    "financial",
    "ownership",
    "catalyst_risk",
)

_REDUCE_SYSTEM = (
    "너는 추출된 사실 목록을 주제별 카탈로그로 통합한다.\n"
    "규칙:\n"
    "1) 중복·유사 항목은 병합하고, 모순 시 최신 보고서 우선(항목 접두 표기 [quarter]>[half]>[annual]).\n"
    "2) 사실 문자열은 가능한 그대로 보존하고 주제 8개에 분류한다. 어느 주제에도 안 맞으면 company 에.\n"
    "3) 접두 [annual]/[half]/[quarter] 출처 표기는 문자열에 그대로 유지한다.\n"
    "출력은 반드시 아래 JSON만(8키 전부 포함):\n"
    '{"company": ["..."], "revenue": ["..."], "market": ["..."], "value_chain": ["..."], '
    '"drivers": ["..."], "financial": ["..."], "ownership": ["..."], "catalyst_risk": ["..."]}'
)

# 섹션별 생성 지시(기존 단일 프롬프트의 8개 섹션 정의를 그대로 승계).
_SECTION_BRIEFS = {
    "company_profile": "회사 개요: 법적지위/설립일/상장일/본점/결산월/주요 사업부문/신용등급/ESG 주요 자격",
    "revenue_model": "수익 모델: 매출 구성(제품/서비스/상품별), 전년 대비 성장률, 수익 메커니즘(구독/일회성/수수료 등)",
    "market_position": "시장 포지션: 시장 규모/점유율/경쟁사 비교/주요 고객사 및 집중도/시장 트렌드",
    "value_chain": "밸류체인·파트너십: 핵심 공급자/원재료/기술 파트너, 주요 판매·유통·고객 채널, 계열사·JV·파트너십",
    "operating_drivers": (
        "핵심 운영 드라이버: 산업별 2~4개 핵심 KPI. 제조업=생산능력/가동률/설비투자, "
        "IT/플랫폼=MAU/ARPU/R&D, 금융=AUM/대출잔액/NPL/스프레드, 바이오=파이프라인/임상단계, "
        "물류=차량/물류센터/운송량. 매출총이익률·판관비율 등 재무 대체 지표도 OK"
    ),
    "financial_highlights": "재무 하이라이트: 최근 3~5개년 매출액/영업이익/당기순이익/영업이익률/ROE/부채비율 추이",
    "ownership_governance": "지배구조·주주: 최대주주 및 특수관계인 지분율/주주5대/배당·배당성향/종속회사/이사회 구성",
    "catalysts_and_risks": (
        "향후 촉매·리스크: 최근 경영사항 및 공시/예정된 사업 이벤트/"
        "산업별 핵심 리스크(원자재·기술·규제·금리 등)와 대응책"
    ),
}

# 섹션 작성 공통 원칙(테이블 우선·추측 금지·산업별 치환) — 기존 단일 프롬프트 원칙 승계.
_SECTION_PRINCIPLES = (
    "너는 한국 상장사 사업보고서를 투자자 관점으로 정리하는 애널리스트다. 원문을 그대로 옮기지 말고 "
    "정리정돈하며 **테이블을 적극 활용**한다. 서술은 핵심만 간결한 마크다운. "
    "카탈로그에 근거가 없는 내용은 추측하지 말고 narrative='정보 없음', tables=[] 로 명시한다.\n\n"
    "**산업 치환 원칙** — 제조업이 아닌 기업(IT/금융/서비스/바이오 등)은 제조업 전용 항목 대신 "
    "해당 산업의 실질 정보를 채운다(금융은 '원재료' 대신 주요 상품별 수신/여신 비율, IT는 '생산' "
    "대신 플랫폼 규모/R&D 투자, 바이오는 '원재료' 대신 파이프라인 단계).\n\n"
)

_ASSEMBLE_REVIEW = (
    "너는 사업 개요 조립 단계의 절차 감사자다. 다음 절차를 점검한다:\n"
    "1) 8개 섹션(company_profile, revenue_model, market_position, value_chain, "
    "operating_drivers, financial_highlights, ownership_governance, catalysts_and_risks) 모두 포함됐나 — 누락 없이.\n"
    "2) 표가 적극 활용됐나 — 서술만 있는 섹션이 아닌가(제품·고객·위험·재무·주주는 표 우선).\n"
    "3) 원문에 없는 내용을 추측·일반론으로 채우지 않았나 — '정보 없음' 명시 여부.\n"
    "4) manufacturing industries have operating_drivers with production KPIs, IT/platforms have MAU/R&D, "
    "finance has AUM/NPL — sector-appropriate content in operating_drivers.\n"
    "5) catalysts_and_risks covers future catalysts AND sector-specific risks (raw materials for "
    "manufacturing, tech change for IT, regulation for finance)."
)


class AssemblyError(RuntimeError):
    """사업 개요 조립 실패(LLM 미완·섹션 과반 미달 등). 호출측(job/배치)이 로깅·실패 처리."""


# 조립 파이프라인 개별 LLM 호출의 전체 deadline(초). muse 지연 스파이크 시 어댑터 재시도 3회
# × read-timeout 300s 가 한 호출 최악 15분까지 증폭되는 것을 호출 단위로 절단한다.
_LLM_CALL_DEADLINE_S = 240


def _chat_json(
    llm: LLMPort,
    model: str,
    system: str,
    user: str,
    temperature: float,
    *,
    timeout: int | None = _LLM_CALL_DEADLINE_S,
) -> dict:
    """LLM 호출 + JSON 파싱(2회 시도). 파싱 실패는 provider 의 일시적 이상일 수 있어 재요청한다.

    어댑터의 재시도는 전송·빈응답 오류만 커버 — '성공했지만 JSON이 아닌 응답'은 여기서 걷어낸다.
    """
    last_raw = ""
    for _attempt in range(2):
        raw = llm.chat(model, system, user, temperature=temperature, timeout=timeout)
        data = _extract_json(raw)
        if isinstance(data, dict):
            return data
        last_raw = raw
    raise LLMError(f"비정형 응답(2회 시도): {last_raw[:120]}")


def _chunk_text(text: str, size: int = _CHUNK_CHARS) -> list[str]:
    """문단(\n) 경계로 size 문자 이하 청크 분할. 단일 문단 초과 시 강제 분할.

    기존 16K 절단과 달리 전체 원문을 다루므로 정보 손실이 없다.
    """
    chunks: list[str] = []
    buf: list[str] = []
    total = 0
    for para in text.split("\n"):
        p = para.strip()
        if not p:
            continue
        while len(p) > size:  # 경계 없는 초장 문단 강제 분할
            chunks.append(p[:size])
            p = p[size:]
        if total + len(p) > size and buf:
            chunks.append("\n".join(buf))
            buf, total = [], 0
        buf.append(p)
        total += len(p)
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def _map_facts(llm: LLMPort, model: str, kind: str, chunk: str) -> list[str]:
    """청크 1개 → 사실 목록. 출처 보고서 kind 를 프로그램적으로 접두(모델 의존 제거)."""
    user = f"[출처 보고서] {kind}\n\n[발췌문]\n{chunk}"
    data = _chat_json(llm, model, _MAP_SYSTEM, user, temperature=0.1)
    facts = [str(f).strip() for f in (data.get("facts") or []) if str(f).strip()]
    if not facts:
        raise LLMError("map 빈 응답")
    return [f"[{kind}] {f}" for f in facts[:_FACTS_CAP]]


def _merge_catalog(llm: LLMPort, model: str, fact_group: list[str]) -> dict:
    """사실 문자열 그룹 1개 → 주제별 카탈로그 1세트."""
    user = "[사실 목록]\n" + "\n".join(fact_group)
    data = _chat_json(llm, model, _REDUCE_SYSTEM, user, temperature=0.1)
    return {
        t: [str(x).strip() for x in (data.get(t) or []) if str(x).strip()] for t in _REDUCE_TOPICS
    }


def _flatten_catalog(catalog: dict) -> list[str]:
    flat: list[str] = []
    for t in _REDUCE_TOPICS:
        flat.extend(f"[{t}] {x}" for x in catalog.get(t, []))
    return flat


def _reduce_catalog(llm: LLMPort, model: str, facts: list[str]) -> dict:
    """사실 목록 → 주제별 카탈로그. 입력이 크면 문자 상한 그룹으로 나눠 단계적 병합.

    그룹 수가 2 이상이면 카탈로그끼리 평탄화해 재병합한다(반복 수렴 — 각 병합 입력은
    항상 상한 이하로 잘라 무한 증가를 막는다).
    """

    def _group_by_chars(items: list[str], cap: int) -> list[list[str]]:
        groups: list[list[str]] = []
        cur: list[str] = []
        cur_len = 0
        for item in items:
            if cur and cur_len + len(item) > cap:
                groups.append(cur)
                cur, cur_len = [], 0
            cur.append(item)
            cur_len += len(item)
        if cur:
            groups.append(cur)
        return groups

    catalogs = [_merge_catalog(llm, model, g) for g in _group_by_chars(facts, _CATALOG_CHAR_CAP)]
    for _round in range(6):  # 병합 수렴 안전 상한 — 실제 2~15 그룹 수준에서 1~2회면 끝난다
        if len(catalogs) <= 1:
            break
        nxt: list[dict] = []
        for i in range(0, len(catalogs), 2):
            pair = catalogs[i : i + 2]
            if len(pair) == 1:
                nxt.append(pair[0])
                continue
            flat = _flatten_catalog(pair[0]) + _flatten_catalog(pair[1])
            nxt.extend(
                _merge_catalog(llm, model, g) for g in _group_by_chars(flat, _CATALOG_CHAR_CAP)
            )
        catalogs = nxt
    return catalogs[0]


def _synthesize_section(
    llm: LLMPort, model: str, sid: str, stock_name: str, catalog: dict, feedback: str | None
) -> dict:
    """카탈로그 → 섹션 1개 생성. 작은 입출력으로 엣지 끊김·누락 폭발반경을 섹션 1개로 제한."""
    system = (
        f"{_SECTION_PRINCIPLES}"
        f"지금은 **단 하나의 섹션만** 작성한다.\n"
        f"- id: {sid}\n- 지시: {_SECTION_BRIEFS[sid]}\n\n"
        "updated_by_kind: 섹션 내용의 출처가 최신 half/quarter 보고서뿐이면 그 kind, "
        "annual 사업보고서 근거가 조금이라도 있으면 'annual'.\n"
        "출력은 반드시 아래 JSON만:\n"
        '{"id": "' + sid + '", "title": "섹션 제목", "narrative": "마크다운 서술(핵심만)", '
        '"tables": [{"title": "표 제목", "headers": ["..."], "rows": [["...", ...]]}], '
        '"updated_by_kind": "annual|half|quarter|null"}'
    )
    user = f"[종목] {stock_name}\n\n[사실 카탈로그]\n{json.dumps(catalog, ensure_ascii=False)}"
    if feedback:
        user += f"\n\n**[이전 검토 절차 지적 — 이 섹션만 보완하라]**\n{feedback}"
    data = _chat_json(llm, model, system, user, temperature=0.3)
    if "narrative" not in data and not data.get("tables"):
        raise LLMError(f"{sid} 비정형 응답")
    data["id"] = sid  # 모델이 어긋나도 표준 id 강제
    return data


def _sections_from_gaps(gaps: list) -> list[str]:
    """reviewer gaps 텍스트에서 언급된 섹션 id 추출. 못 찾으면 [](호출측이 전체 재생성)."""
    found: list[str] = []
    for g in gaps:
        blob = json.dumps(g, ensure_ascii=False) if isinstance(g, dict) else str(g)
        for sid in bo.INVESTOR_SECTIONS:
            if sid in blob and sid not in found:
                found.append(sid)
    return found


def _review_and_fix_sections(
    llm: LLMPort,
    model: str,
    sections: dict[str, dict],
    catalog: dict,
    stock_name: str,
    *,
    max_rounds: int = 2,
) -> tuple[dict[str, dict], bool]:
    """조립 결과에 절차 리뷰를 돌리고, gap 지적 섹션만 재생성한다(전체 재생성 아님).

    반환: (섹션 dict, procedure_sound). reviewer 자체 실패는 sound 처리(기존 룰 계승).
    """
    payload = {"sections": [sections[sid] for sid in bo.INVESTOR_SECTIONS]}
    for _round in range(max_rounds):
        review = review_loop.review_result(
            llm, model, _ASSEMBLE_REVIEW, payload, timeout=_LLM_CALL_DEADLINE_S
        )
        if review.get("procedure_sound"):
            return sections, True
        gaps = review.get("gaps") or []
        feedback = review_loop.gaps_to_feedback(gaps)
        if not feedback:
            break
        targets = _sections_from_gaps(gaps) or list(bo.INVESTOR_SECTIONS)
        logger.info("business assemble: review round 지적 %s → 해당 섹션만 재생성", targets)
        for sid in targets:
            try:
                sections[sid] = _synthesize_section(llm, model, sid, stock_name, catalog, feedback)
            except LLMError as e:
                logger.warning("section redo failed %s: %s", sid, e)
        payload = {"sections": [sections[sid] for sid in bo.INVESTOR_SECTIONS]}
    return sections, False


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


# ── 비즈니스 온톨로지 추출(LLM NER + 결정론적 정규화) ──────────────────────
# LLM/normalizer 분리: LLM 은 raw mention + source_quote(원문 verbatim) 만 내고, 결정론적
# normalizer 가 정준 canonical_id 를 부여. confidence < 0.85 → pending_review(자동 병합 금지).
_ONTOLOGY_EXTRACT_SYSTEM = (
    "너는 한국 상장사 사업보고서 원문에서 비즈니스 온톨로지 엔티티를 추출하는 NER+관계 태거다. "
    "원문에 언급된 제품·원재료·산업·고객·공급자·경쟁사·사업 부문을 식별해 **회사(주체)와의 관계**로 태깅한다. "
    "**원문에 없는 내용은 추출하지 않는다** — 추측·일반론 금지. 각 mention 은 반드시 원문에서 발췌한 "
    "source_quote(verbatim span)를 포함한다(감사증적). 정준화·ID 부여는 하지 않는다 — raw name 만 내라.\n\n"
    "node_type(대상 노드): company|industry|product|raw_material|segment.\n"
    "edge_type(회사→대상 관계): manufactures(제품 생산), uses_material(원재료 사용), operates_in(산업 영위), "
    "competes_with(경쟁사), supplies_to(주요 고객/납품처), supplies(공급자), has_segment(사업 부문), "
    "exports_to(수출 대상 — node_type=segment), parent_of, subsidiary_of.\n"
    "share: 0~1 매출 비중(원문에 명시된 경우만). period: 'YYYY.MM' 또는 연도(원문에 명시된 경우만).\n"
    "confidence: LLM 자체 추출 신뢰도 0~1.\n\n"
    "출력은 반드시 아래 JSON 스키마만(다른 텍스트 금지):\n"
    '{"mentions": [{"node_type": "product", "name": "DRAM", "edge_type": "manufactures", '
    '"share": 0.42, "period": "2024.12", "source_quote": "원문 발췌 span", "confidence": 0.9}]}\n'
    "mention 이 0개여도 빈 배열로 반환. 원문에 비즈니스 엔티티가 없으면 mentions=[] ."
)

# node_type → 회사-대상 기본 관계(LLM 이 edge_type 누락시 폴백).
_DEFAULT_EDGE_FOR_TYPE: dict[BusinessNodeType, str] = {
    "product": "manufactures",
    "raw_material": "uses_material",
    "industry": "operates_in",
    "company": "competes_with",
    "segment": "has_segment",
}
_VALID_NODE_TYPES: set[str] = set(_DEFAULT_EDGE_FOR_TYPE)


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_ontology_entities(llm: LLMPort, model: str, ctx: dict) -> list[OntologyMention]:
    """조립 컨텍스트 원문에서 비즈니스 온톨로지 언급(LLM NER) 추출. 추가 DART 호출 없음.

    LLM 은 raw name + source_quote 만 반환 — 정준화는 _persist_ontology 의 normalizer 단에서.
    실패·비정형 응답 시 빈 목록(조립 중단 아님 — 온톨로지는 보강).
    """
    user = (
        f"[종목] {ctx['stock_code']} {ctx['stock_name']}\n\n"
        f"[베이스 사업보고서 원문 — 사업의 내용]\n{ctx['base']['text']}\n\n"
        f"[반기·분기 갱신 원문 — 회사의 개황]\n" + "\n---\n".join(u["text"] for u in ctx["updates"])
    )
    try:
        raw = llm.chat(
            model, _ONTOLOGY_EXTRACT_SYSTEM, user, temperature=0.2, timeout=_LLM_CALL_DEADLINE_S
        )
    except LLMError as e:
        logger.warning("ontology extract LLM failed %s: %s", ctx["stock_code"], e)
        return []
    data = _extract_json(raw)
    if not data:
        return []
    out: list[OntologyMention] = []
    for m in data.get("mentions", []):
        if not isinstance(m, dict):
            continue
        name = (m.get("name") or "").strip()
        nt = (m.get("node_type") or "").strip()
        if not name or nt not in _VALID_NODE_TYPES:
            continue
        out.append(
            OntologyMention(
                node_type=nt,
                name=name,
                edge_type=(m.get("edge_type") or "").strip(),
                share=_to_float(m.get("share")),
                period=(m.get("period") or "").strip() or None,
                source_quote=(m.get("source_quote") or "").strip(),
                confidence=_to_float(m.get("confidence")) or 0.0,
            )
        )
    return out


def _upsert_ontology_node(
    db: Session,
    code: str,
    node_type: str,
    canonical_id: str | None,
    korean_name: str,
    status: str,
    source_rcept: str | None,
    confidence: float | None,
    english_name: str | None = None,
) -> int:
    """노드 upsert → PK id 반환. (stock_code, node_type, korean_name) 로 중복 제거."""
    stmt = insert(BusinessOntologyNode).values(
        stock_code=code,
        node_type=node_type,
        canonical_id=canonical_id,
        korean_name=korean_name,
        english_name=english_name,
        status=status,
        source_rcept=source_rcept,
        confidence=confidence,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_bont_node",
        set_={
            "canonical_id": canonical_id,
            "status": status,
            "english_name": english_name,
            "source_rcept": source_rcept,
            "confidence": confidence,
        },
    )
    db.execute(stmt)
    return int(
        db.scalar(
            select(BusinessOntologyNode.id).where(
                BusinessOntologyNode.stock_code == code,
                BusinessOntologyNode.node_type == node_type,
                BusinessOntologyNode.korean_name == korean_name,
            )
        )
    )


def _upsert_ontology_edge(
    db: Session,
    code: str,
    src_id: int,
    dst_id: int,
    edge_type: str,
    share: float | None,
    period: str | None,
    source_quote: str,
    source_rcept: str | None,
    confidence: float | None,
    chain_stage: str | None = None,
) -> None:
    """엣지 upsert. period 는 '' 로 정규화(unique constraint NULL distinctness 회피)."""
    stmt = insert(BusinessOntologyEdge).values(
        stock_code=code,
        src_node_id=src_id,
        dst_node_id=dst_id,
        edge_type=edge_type,
        share=share,
        period=period or "",
        source_quote=source_quote or None,
        source_rcept=source_rcept,
        chain_stage=chain_stage,
        confidence=confidence,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_bont_edge",
        set_={
            "share": share,
            "source_quote": source_quote or None,
            "chain_stage": chain_stage,
            "confidence": confidence,
        },
    )
    db.execute(stmt)


def persist_ontology(
    db: Session,
    code: str,
    mentions: list[OntologyMention],
    source_rcept: str,
    stock_name: str,
    induty_code: str | None = None,
) -> dict[str, object]:
    """LLM 추출 mentions → 정규화·노드/엣지 영속 + 캐시 스냅싯 반환.

    회사(주체) 노드를 src 로 모든 엣지 연결. 대상 노드는 normalizer 로 정준화(회사는 CorpCodeMap DB 경유,
    산업은 GICS/dart 매핑, 제품/원재료/부문은 사전). 정준 미해결도 pending_review 로 저장(자동 병합 금지).
    induty_code(사업보고서 DART 산업) 가 있으면 회사 operates_in 산업 노드+엣지 추가.
    실패해도 조립을 깨지 않기 위해 호출측이 try/except 로 감싼다.
    """
    from app.services import business_ontology as bo_svc

    # 회사(주체) 노드 — 정준 ID = CMP_KRX_<stock_code>. 이미 있으면 재사용(이름 불일치 중복 노드 방지).
    company_id = _ensure_company_node(db, code, source_rcept, name=stock_name)

    # DART induty_code → GICS 산업 operates_in 엣지(회사 자기 산업).
    if induty_code:
        r = bo_svc.normalize_one(induty_code, "industry", standard="dart")
        if r.canonical_id:
            ind_node = bo_svc.industry(r.canonical_id)
            ind_korean = ind_node.korean_name if ind_node else (r.term or induty_code)
            ind_id = _upsert_ontology_node(
                db,
                code,
                "industry",
                r.canonical_id,
                ind_korean,
                r.status,
                source_rcept,
                r.confidence,
            )
            _upsert_ontology_edge(
                db,
                code,
                company_id,
                ind_id,
                "operates_in",
                None,
                None,
                "",
                source_rcept,
                r.confidence,
            )

    for m in mentions:
        if m.node_type == "company":
            r = bo_svc.resolve_company(db, m.name)
        else:
            r = bo_svc.normalize_one(m.name, m.node_type)  # type: ignore[arg-type]
        edge_type = m.edge_type or _DEFAULT_EDGE_FOR_TYPE[m.node_type]  # type: ignore[index]
        dst_id = _upsert_ontology_node(
            db,
            code,
            m.node_type,
            r.canonical_id,
            r.term or m.name,
            r.status,
            source_rcept,
            r.confidence,
        )
        _upsert_ontology_edge(
            db,
            code,
            company_id,
            dst_id,
            edge_type,
            m.share,
            m.period,
            m.source_quote,
            source_rcept,
            m.confidence,
        )
    db.commit()
    # 캐시 스냅샷은 DB 실제 행에서 재구성(회사 그래프 서비스와 동일 원천).
    return bo_svc.company_graph(db, code)


def _ensure_company_node(
    db: Session, code: str, source_rcept: str | None = None, name: str | None = None
) -> int:
    """회사(주체) 노드 PK 확보 — 이미 있으면 재사용, 없으면 생성.

    korean_name 이 호출마다 달라지는 것(조립 stock_name vs 리서치 시점)을 막기 위해 기존 노드가
    있으면 그대로 재사용한다. (stock_code, node_type, korean_name) 유일 제약 때문에 이름이 다르면
    별도 행이 생기기 때문. 신규 생성시에만 name(또는 CorpCodeMap.corp_name) 을 사용한다.
    """
    existing = db.scalar(
        select(BusinessOntologyNode.id).where(
            BusinessOntologyNode.stock_code == code,
            BusinessOntologyNode.node_type == "company",
        )
    )
    if existing:
        return int(existing)
    if not name:
        name = (
            db.scalar(select(CorpCodeMap.corp_name).where(CorpCodeMap.stock_code == code)) or code
        )
    return _upsert_ontology_node(
        db, code, "company", f"CMP_KRX_{code}", name, "canonical", source_rcept, 1.0
    )


# 밸류체인 단계(stage 키워드) → 온톨로지 chain_stage(5主+4지원 중主活动 4종).
_VALUE_CHAIN_STAGE_MAP: dict[str, str] = {
    "원료": "inbound",
    "조달": "inbound",
    "구매": "inbound",
    "생산": "operations",
    "제조": "operations",
    "가공": "operations",
    "유통": "outbound",
    "물류": "outbound",
    "판매": "outbound",
    "출하": "outbound",
    "서비스": "service",
    "마케팅": "marketing",
    "영업": "marketing",
}


def _map_chain_stage(stage: str) -> str | None:
    """리서치 value_chain.stage(자유텍스트) → 온톨로지 chain_stage enum."""
    s = (stage or "").strip()
    if not s:
        return None
    for kw, cs in _VALUE_CHAIN_STAGE_MAP.items():
        if kw in s:
            return cs
    return None


def _resolve_entity(db, name: str, bo_svc):
    """리서치 엔티티명 → (정규화결과, 노드타입). 회사→원재료→제품→산업 순 시도."""
    r = bo_svc.resolve_company(db, name)
    if r.resolved:
        return r, "company"
    for nt in ("raw_material", "product", "industry"):
        r2 = bo_svc.normalize_one(name, nt)  # type: ignore[arg-type]
        if r2.resolved:
            return r2, nt
    # 무매치 — company pending_review 로 보존(자동 병합 금지).
    return r, "company"


def promote_research_to_ontology(
    db: Session, code: str, summary: dict, source_rcept: str | None = None
) -> dict[str, object]:
    """리서치 결과(ResearchSummary 직렬화 dict) → 온톨로지 노드/엣지 승격 + 스냅샷 반환.

    Phase 3b: vendors→supplies(회사 공급자)/uses_material(원재료), customers→supplies_to,
    competitors→competes_with, value_chain→part_of_value_chain(chain_stage 매핑).
    정규화는 결정론적 normalizer 경유. 리서치는 특정 공시 rcept 기반이 아니므로 source_rcept=None.
    """
    from app.services import business_ontology as bo_svc

    company_id = _ensure_company_node(db, code, source_rcept)

    def _add(name: str, edge_type: str, note: str, chain_stage: str | None = None) -> None:
        name = (name or "").strip()
        if not name:
            return
        r, nt = _resolve_entity(db, name, bo_svc)
        dst_id = _upsert_ontology_node(
            db, code, nt, r.canonical_id, r.term or name, r.status, source_rcept, r.confidence
        )
        # 원재료로 정준화된 공급자는 uses_material(원재료 사용)로 일관성 유지.
        et = "uses_material" if (edge_type == "supplies" and nt == "raw_material") else edge_type
        _upsert_ontology_edge(
            db,
            code,
            company_id,
            dst_id,
            et,
            None,
            None,
            note,
            source_rcept,
            r.confidence,
            chain_stage=chain_stage,
        )

    for v in summary.get("vendors", []) or []:
        if isinstance(v, dict):
            _add(v.get("name", ""), "supplies", v.get("note", ""))
    for c in summary.get("customers", []) or []:
        if isinstance(c, dict):
            _add(c.get("name", ""), "supplies_to", c.get("note", ""))
    for comp in summary.get("competitors", []) or []:
        if isinstance(comp, dict):
            _add(comp.get("name", ""), "competes_with", comp.get("note", ""))
    for link in summary.get("value_chain", []) or []:
        if isinstance(link, dict):
            _add(
                link.get("entity", ""),
                "part_of_value_chain",
                link.get("note", ""),
                chain_stage=_map_chain_stage(link.get("stage", "")),
            )
    db.commit()
    return bo_svc.company_graph(db, code)


def assemble_overview(db: Session, settings: Settings, code: str, *, progress=None) -> dict | None:
    """종목 사업 개요 조립(map-reduce) → BusinessOverviewCache 저장 + 페이로드 반환.

    사업보고서 없으면 None. LLM 미완·섹션 과반 미달은 AssemblyError.
    progress: 선택 콜백(0~100) — assembly job 이 진행률 표기에 사용.

    트랜잭션 분리 설계:
    - Phase 1 (짧음, 즉시 COMMIT): DART 원문 추출·적재. DB 잠금 최소화.
    - Phase 2 (LLM 다수 소형 호출, 트랜잭션 없음): map → reduce → 섹션별 생성 → 절차 리뷰.
      각 호출이 작아 엣지 끊김·빈 응답 폭발반경이 호출 1개로 제한되고, 원문 절단 없이 전체를 커버.
    - Phase 3 (짧음, 즉시 COMMIT): payload + 온톨로지 캐시 저장.
    """
    corp_row = db.execute(
        select(CorpCodeMap.corp_code, CorpCodeMap.induty_code).where(CorpCodeMap.stock_code == code)
    ).first()
    if not corp_row or not settings.dart_api_key:
        return None
    corp_code = corp_row.corp_code
    induty_code = corp_row.induty_code
    llm = get_llm(settings)
    if llm is None:
        logger.info("business assemble %s: LLM 미설정 — 원문만 적재 가능", code)

    with requests.Session() as session:
        reports = _gather_for_assembly(settings, corp_code, session)
    if not reports:
        return None

    # ── Phase 1: DART 추출 (짧은 트랜잭션, 즉시 COMMIT) ──────────────────
    try:
        with requests.Session() as session:
            for rcept, kind, year in reports:
                if _load_raw(db, code, rcept):
                    continue
                sections = extract_sections(settings, corp_code, rcept, kind, session)
                if sections:
                    _store_raw(db, code, rcept, kind, _period_str(year, kind), sections)

            # 부문별 매출 — 구조화 데이터라 LLM과 무관하게 영속.
            base_rcept_pre, _base_kind_pre, base_year_pre = reports[0]
            try:
                fetch_segment_sales_for(
                    db, settings, code, corp_code, base_year_pre, base_rcept_pre, session
                )
            except Exception as e:
                logger.warning("segment_sales %s skipped: %s", code, e)

        db.commit()  # ← Phase 1 완료: DART 추출만 커밋, LLM은 트랜잭션 밖
    except dart.DartQuotaExceeded:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    if llm is None:
        return None  # 원문만 적재, 조립은 LLM 필요

    def _tick(pct: int) -> None:
        if progress is not None:
            with contextlib.suppress(Exception):  # 진행률 보고 실패가 조립을 깨지 않게
                progress(pct)

    model = settings.insight_model

    # ── Phase 2a: map — 전체 원문을 청크 사실추출 (절단 없음) ────────────
    base_rcept, _base_kind, _base_year = reports[0]
    name = (
        company_service.report_stock_name(db, code)
        or company_service.resolve_stock_name(db, code)
        or ""
    )
    sources: list[tuple[str, str]] = []
    base_text = _load_raw(db, code, base_rcept).get(bo.SECTION_BUSINESS_CONTENT, "")
    if not base_text:
        logger.info("business assemble %s: 베이스 원문 없음 — 조립 생략", code)
        return None
    sources.append((bo.ANNUAL, base_text))
    for rcept, kind, _year in reports[1:]:
        text = _load_raw(db, code, rcept).get(bo.SECTION_COMPANY_OVERVIEW, "")
        if text:
            sources.append((kind, text))

    chunks = [(kind, c) for kind, text in sources for c in _chunk_text(text)]
    facts: list[str] = []
    failed_chunks = 0
    for i, (kind, chunk) in enumerate(chunks):
        try:
            facts.extend(_map_facts(llm, model, kind, chunk))
        except LLMError as e:
            failed_chunks += 1
            logger.warning(
                "business map %s chunk %d/%d 실패(스킵): %s", code, i + 1, len(chunks), e
            )
        _tick(5 + int(30 * (i + 1) / max(len(chunks), 1)))
    if not facts:
        raise AssemblyError(f"map 실패 — 사실 추출 0건({failed_chunks}/{len(chunks)} 청크 실패)")
    logger.info(
        "business map %s: %d/%d 청크 성공, 사실 %d건",
        code,
        len(chunks) - failed_chunks,
        len(chunks),
        len(facts),
    )

    # ── Phase 2b: reduce — 주제별 카탈로그 통합 ──────────────────────────
    catalog = _reduce_catalog(llm, model, facts)
    _tick(50)

    # ── Phase 2c: synthesize — 섹션별 개별 생성 ──────────────────────────
    sections: dict[str, dict] = {}
    section_errors: dict[str, str] = {}
    for idx, sid in enumerate(bo.INVESTOR_SECTIONS):
        try:
            sections[sid] = _synthesize_section(llm, model, sid, name, catalog, feedback=None)
        except LLMError as e:
            section_errors[sid] = str(e)
            logger.warning("business section %s/%s 실패: %s", code, sid, e)
        _tick(55 + int(30 * (idx + 1) / len(bo.INVESTOR_SECTIONS)))

    ok_count = len(sections)
    if ok_count <= len(bo.INVESTOR_SECTIONS) // 2:
        raise AssemblyError(
            f"섹션 생성 과반 미달 {ok_count}/{len(bo.INVESTOR_SECTIONS)}"
            f" — errors={list(section_errors)[:3]}"
        )
    for sid in bo.INVESTOR_SECTIONS:  # 실패 섹션은 빈 스텁(누락 대신 정직한 공백)
        sections.setdefault(
            sid, {"id": sid, "title": "", "narrative": "", "tables": [], "updated_by_kind": None}
        )

    # ── Phase 2d: review — gap 지적 섹션만 재생성(전체 재생성 아님) ───────
    sections, sound = _review_and_fix_sections(llm, model, sections, catalog, name)
    _tick(90)
    result = {"sections": [sections[sid] for sid in bo.INVESTOR_SECTIONS]}
    if not sound:
        result["_procedure_incomplete"] = True
    result["_section_errors"] = section_errors  # 빈 dict면 직렬화 노이즈 없음

    # ── Phase 3: 캐시 저장 (짧은 트랜잭션, 즉시 COMMIT) ──────────────────
    ontology_snapshot: dict[str, object] = {"nodes": [], "edges": []}
    try:
        # 온톨로지 NER 도 카탈로그(작은 입력)로 — 원문 24K 재주입 회피.
        onto_ctx = {
            "stock_code": code,
            "stock_name": name,
            "base": {"text": json.dumps(catalog, ensure_ascii=False)},
            "updates": [],
        }
        mentions = extract_ontology_entities(llm, model, onto_ctx)
        if mentions:
            ontology_snapshot = persist_ontology(
                db, code, mentions, base_rcept, name, induty_code=induty_code
            )
    except Exception as e:  # BLE001: 온톨로지 보강 실패가 조립을 깨지 않게
        logger.warning("ontology extract %s skipped: %s", code, e)

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
        "ontology": ontology_snapshot,
    }
    _store_cache(
        db, code, name, base_rcept, payload["source_reports"], _inputs_hash(reports), payload
    )
    db.commit()  # ← Phase 3 완료
    logger.info(
        "business assemble %s: %d 섹션, base=%s, %d updates, sound=%s",
        code,
        len(payload["sections"]),
        base_rcept,
        len(reports) - 1,
        sound,
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

    # Phase 3b: 리서치 엔티티/밸류체인 → 온톨로지 노드/엣지 승격. 보강 정보라 실패해도 캐시 병합은 진행.
    ontology_snapshot: dict[str, object] | None = None
    try:
        ontology_snapshot = promote_research_to_ontology(db, code, research_summary)
    except Exception as e:  # BLE001: 온톨로지 승격 실패가 리서치 캐싱을 깨지 않게
        logger.warning("research→ontology promote %s skipped: %s", code, e)

    if row is None:
        # 스텁 생성: 빈 sections/source_reports, stock_name은 추후 채워짐.
        stub_payload = {
            "stock_code": code,
            "stock_name": "",
            "as_of_annual_rcept": "",
            "source_reports": [],
            "sections": [],
            "research_summary": research_summary,
            "ontology": ontology_snapshot or {"nodes": [], "edges": []},
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
    if ontology_snapshot is not None:
        payload["ontology"] = ontology_snapshot

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
