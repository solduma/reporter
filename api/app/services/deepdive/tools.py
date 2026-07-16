"""딥다이브 도구 카탈로그 — LLM 이 JSON 으로 요청하면 코드로 실행하는 리서치 도구들.

각 도구는 (ctx, args) → dict 순수 디스패치. 기존 어댑터/서비스(DART·재무·리포트·피어·웹검색)를
감싸 LLM 의 tool 요청을 안전히 처리한다. agent 의 mini tool-loop 가 이 카탈로그를 참조·호출한다.
신규 수집 파이프라인 없음(기존 자산 재사용) — 웹검색만 신규 어댑터.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.dart import client as dart
from app.config import Settings
from app.db.models import CorpCodeMap, Report, ReportAnalysis
from app.services import company_service
from app.services.deepdive import websearch

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """도구 실행 컨텍스트 — 대상 종목 + 리소스 핸들."""

    db: Session
    settings: Settings
    session: requests.Session
    code: str
    corp_code: str | None = None


# ── 정기보고서(사업/반기/분기 중 최신) ────────────────────────────────
_PERIODIC_KINDS = ("annual", "half", "quarter")


def _recent_periodic_rcept(ctx: ToolContext) -> tuple[str, str] | None:
    """최근 2회계연도의 사업/반기/분기 정기보고서 중 접수번호 최신(=최신 문서) → (rcept_no, kind).

    사업보고서만이 아니라 반기·분기도 대상. rcept_no 는 시간순 증가라 최대값이 가장 신선한 제출.
    """
    if not ctx.corp_code or not ctx.settings.dart_api_key:
        return None
    this_year = date.today().year
    found: list[tuple[str, str]] = []  # (rcept_no, kind)
    for year in (this_year, this_year - 1):
        for kind in _PERIODIC_KINDS:
            rcept = dart.find_periodic_report(
                ctx.settings.dart_api_key, ctx.corp_code, year, kind, ctx.session
            )
            if rcept:
                found.append((rcept, kind))
    if not found:
        return None
    return max(found, key=lambda t: t[0])  # rcept_no 최대 = 최신


# ── 도구 구현 ─────────────────────────────────────────────────────────
def tool_recent_periodic_report(ctx: ToolContext, args: dict) -> dict:
    """최신 정기보고서(사업/반기/분기 중 접수 최신)의 본문 발췌 + 종류."""
    try:
        picked = _recent_periodic_rcept(ctx)
    except dart.DartQuotaExceeded:
        # 한도초과는 매핑·데이터 문제가 아닌 일시적 제약(자정 리셋). 다른 지표로 판단하도록 안내.
        return {
            "available": False,
            "note": "DART 일일 조회한도 초과로 원문 발췌 불가(일시적). 매핑·데이터 문제 아님 — "
            "다른 도구(financials·reports 등)로 분석 진행.",
        }
    if not picked:
        return {"available": False, "note": "해당 기업의 정기보고서를 찾지 못함(발췌 생략, 다른 도구로 진행)"}
    rcept, kind = picked
    text = dart.fetch_document_text(ctx.settings.dart_api_key, rcept, ctx.session, max_chars=8000)
    return {"available": True, "kind": kind, "rcept_no": rcept, "text": text}


def tool_financials(ctx: ToolContext, args: dict) -> dict:
    """분기·연간 재무 시계열(매출·영업이익·순이익·EPS·PER/PBR·배당·EV/EBITDA 등)."""
    rows = company_service.financials_rows(ctx.db, ctx.code)
    out = [
        {
            "period": r.period, "is_estimate": r.is_estimate,
            "revenue": r.revenue, "operating_income": r.operating_income,
            "net_income": r.net_income, "eps": r.eps, "bps": r.bps,
            "per": r.per, "pbr": r.pbr, "roe": r.roe, "ebitda": r.ebitda,
            "dps": r.dps, "div_yield": r.div_yield, "psr": r.psr, "ev_ebitda": r.ev_ebitda,
        }
        for r in rows
    ]
    return {"count": len(out), "periods": out}


def tool_disclosures(ctx: ToolContext, args: dict) -> dict:
    """과거 공시 목록. args: years(기본 3), kind_filter(report_nm 부분일치, 선택)."""
    if not ctx.corp_code or not ctx.settings.dart_api_key:
        return {"available": False, "note": "DART 키·매핑 없음"}
    years = int(args.get("years", 3))
    end = date.today()
    begin = end - timedelta(days=365 * years)
    kw = args.get("kind_filter")
    try:
        rows = dart.fetch_disclosures(
            ctx.settings.dart_api_key, ctx.corp_code, ctx.code, begin, end, ctx.session
        )
    except Exception as e:  # 외부 IO 경계 방어
        logger.warning("deepdive disclosures failed %s: %s", ctx.code, e)
        return {"available": False, "note": "공시 조회 실패"}
    items = [
        {"rcept_no": d.rcept_no, "report_nm": d.report_nm, "rcept_dt": d.rcept_dt.isoformat()}
        for d in rows
        if not kw or kw in d.report_nm
    ]
    return {"available": True, "count": len(items), "disclosures": items[:40]}


def tool_disclosure_text(ctx: ToolContext, args: dict) -> dict:
    """공시 원문 발췌. args: rcept_no(필수)."""
    rcept = args.get("rcept_no")
    if not rcept or not ctx.settings.dart_api_key:
        return {"available": False, "note": "rcept_no·DART 키 필요"}
    text = dart.fetch_document_text(ctx.settings.dart_api_key, str(rcept), ctx.session, max_chars=8000)
    return {"available": bool(text), "rcept_no": rcept, "text": text}


def tool_ownership(ctx: ToolContext, args: dict) -> dict:
    """주주구성·대주주 소유변동(임원·주요주주 보고)."""
    if not ctx.corp_code or not ctx.settings.dart_api_key:
        return {"available": False, "note": "DART 키·매핑 없음"}
    try:
        changes = dart.fetch_ownership_changes(ctx.settings.dart_api_key, ctx.corp_code, ctx.session)
    except Exception as e:
        logger.warning("deepdive ownership failed %s: %s", ctx.code, e)
        return {"available": False, "note": "소유변동 조회 실패"}
    items = [
        {"rcept_no": k, "reporter": v.reporter, "position": v.position,
         "shares_after": v.shares_after, "shares_delta": v.shares_delta}
        for k, v in list(changes.items())[:20]
    ]
    return {"available": True, "count": len(items), "changes": items}


def tool_reports(ctx: ToolContext, args: dict) -> dict:
    """리포트: ① 개별 종목 리포트 ② 이 종목이 속한 산업 리포트 ③ 본문에 종목명 언급된 리포트.

    개별 커버가 없어도 산업 리포트·타 종목 리포트 본문에서 언급을 찾는다(사용자 지시 반영).
    """
    db = ctx.db
    name = company_service.report_stock_name(db, ctx.code) or company_service.resolve_stock_name(db, ctx.code)
    # ① 개별 종목 리포트
    own = db.execute(
        select(Report, ReportAnalysis)
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(Report.stock_code == ctx.code)
        .order_by(Report.published_date.desc()).limit(8)
    ).all()
    # ② 이 종목 리포트들의 industry_name → 같은 산업 리포트(category=industry)
    industries = {
        r.industry_name for (r, _a) in own if r.industry_name
    } | set(
        db.scalars(
            select(Report.industry_name).where(
                Report.stock_code == ctx.code, Report.industry_name.is_not(None)
            )
        ).all()
    )
    ind_rows = []
    if industries:
        ind_rows = db.execute(
            select(Report, ReportAnalysis)
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
            .where(Report.category == "industry", Report.industry_name.in_(industries))
            .order_by(Report.published_date.desc()).limit(6)
        ).all()
    # ③ 본문(rationale/summary)에 종목명 언급된 타 리포트
    mention_rows = []
    if name:
        mention_rows = db.execute(
            select(Report, ReportAnalysis)
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
            .where(Report.stock_code != ctx.code, ReportAnalysis.rationale.contains(name))
            .order_by(Report.published_date.desc()).limit(6)
        ).all()

    def _fmt(rows: list) -> list[dict]:
        return [
            {"title": r.title, "broker": r.broker, "date": r.published_date.isoformat(),
             "sentiment": a.sentiment.value if a.sentiment else None,
             "summary": (a.summary or a.rationale or "")[:600]}
            for (r, a) in rows
        ]

    return {
        "stock_name": name,
        "own_reports": _fmt(own),
        "industry_reports": _fmt(ind_rows),
        "mention_reports": _fmt(mention_rows),
    }


def tool_peers(ctx: ToolContext, args: dict) -> dict:
    """동일업종 밸류에이션(피어 PER/PBR 등)."""
    peers = company_service.peers_rows(ctx.db, ctx.code)
    return {
        "count": len(peers),
        "peers": [
            {"name": p.peer_name, "code": p.peer_stock_code, "per": p.per, "pbr": p.pbr,
             "roe": p.roe, "ev_ebitda": p.ev_ebitda, "market_cap": p.market_cap}
            for p in peers[:12]
        ],
    }


def tool_price_context(ctx: ToolContext, args: dict) -> dict:
    """현재가·시총·모멘텀 스냅샷."""
    u = company_service.growth_snapshot(ctx.db, ctx.code)
    if not u:
        return {"available": False}
    return {
        "available": True, "market": u.market, "market_cap": u.market_cap,
        "close_price": u.close_price, "change_pct": u.change_pct, "momentum_3m": u.momentum_3m,
    }


def tool_web_search(ctx: ToolContext, args: dict) -> dict:
    """웹 리서치(네이버 블로그 우선 + 뉴스). args: query(필수), sort(sim|date), crawl(본문 크롤 수).

    한국 종목은 네이버 블로그 심층 리서치 글이 핵심 소스. 키 미설정 시 빈 결과.
    """
    query = args.get("query")
    if not query:
        return {"available": False, "note": "query 필요"}
    res = websearch.research(
        ctx.settings, str(query), ctx.session,
        sort=args.get("sort", "sim"),
        crawl_bodies=int(args.get("crawl", 4)),
    )
    res["available"] = bool(res.get("hits") or res.get("bodies"))
    return res


def tool_fetch_web_page(ctx: ToolContext, args: dict) -> dict:
    """URL 본문 추출(네이버 블로그 iframe 해제 포함). args: url(필수)."""
    from app.adapters.external import blog_crawler

    url = args.get("url")
    if not url:
        return {"available": False, "note": "url 필요"}
    page = blog_crawler.crawl_blog(str(url), ctx.session)
    if not page:
        return {"available": False, "note": "네이버 블로그 아님/본문 없음(1차는 네이버 블로그만 지원)"}
    return {"available": True, **page}


# 도구 레지스트리 — LLM 프롬프트에 이름·설명을 노출하고, 이름으로 디스패치.
TOOLS: dict[str, tuple] = {
    "recent_periodic_report": (tool_recent_periodic_report, "최신 정기보고서(사업/반기/분기) 본문 발췌"),
    "financials": (tool_financials, "분기·연간 재무 시계열"),
    "disclosures": (tool_disclosures, "과거 공시 목록 (args: years, kind_filter)"),
    "disclosure_text": (tool_disclosure_text, "공시 원문 발췌 (args: rcept_no)"),
    "ownership": (tool_ownership, "주주구성·대주주 소유변동"),
    "reports": (tool_reports, "개별+산업 리포트 및 본문 내 종목 언급"),
    "peers": (tool_peers, "동일업종 밸류에이션"),
    "price_context": (tool_price_context, "현재가·시총·모멘텀"),
    "web_search": (tool_web_search, "웹 리서치(네이버 블로그 우선) (args: query, sort, crawl)"),
    "fetch_web_page": (tool_fetch_web_page, "URL 본문 추출 (args: url)"),
}


def dispatch(name: str, ctx: ToolContext, args: dict) -> dict:
    """도구 이름으로 실행. 미지의 도구·예외는 오류 dict(에이전트 루프가 다음 판단에 반영)."""
    entry = TOOLS.get(name)
    if not entry:
        return {"error": f"unknown tool: {name}", "available_tools": list(TOOLS)}
    try:
        return entry[0](ctx, args or {})
    except Exception as e:  # 도구 실패가 루프를 죽이지 않게
        logger.warning("deepdive tool %s failed %s: %s", name, ctx.code, e)
        return {"error": f"tool {name} failed: {e}"}


def resolve_corp_code(db: Session, code: str) -> str | None:
    """종목코드 → DART corp_code(CorpCodeMap). 딥다이브 시작 시 1회 조회해 ctx 에 캐시."""
    return db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
