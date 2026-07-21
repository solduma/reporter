"""딥다이브 도구 카탈로그 — LLM 이 JSON 으로 요청하면 코드로 실행하는 리서치 도구들.

각 도구는 (ctx, args) → dict 순수 디스패치. 기존 어댑터/서비스(DART·재무·리포트·피어·웹검색)를
감싸 LLM 의 tool 요청을 안전히 처리한다. agent 의 mini tool-loop 가 이 카탈로그를 참조·호출한다.
신규 수집 파이프라인 없음(기존 자산 재사용) — 웹검색만 신규 어댑터.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import requests
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.adapters.dart import client as dart
from app.config import Settings
from app.db.models import CorpCodeMap, Report, ReportAnalysis
from app.services import company_service
from app.services.deepdive import websearch

logger = logging.getLogger(__name__)


def _no_space(s: str) -> str:
    """공백 제거(종목명 매칭용 — 띄어쓰기 편차 흡수)."""
    return "".join((s or "").split())


@dataclass
class ToolContext:
    """도구 실행 컨텍스트 — 대상 종목 + 리소스 핸들.

    job 1회당 하나 생성돼 여러 단계가 공유하므로, 무거운 라이브 조회(정기보고서 본문)를
    단계 간 재사용하도록 실행 스코프 캐시를 둔다.
    """

    db: Session
    settings: Settings
    session: requests.Session
    code: str
    corp_code: str | None = None
    _cache: dict = field(default_factory=dict)


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
    """최신 정기보고서(사업/반기/분기 중 접수 최신)의 본문 발췌 + 종류.

    overview·business 단계가 같은 종목의 같은 문서를 요청하므로, 첫 조회 결과를 ctx 캐시에
    담아 재사용한다(정기보고서 탐색 6콜 + 본문 다운로드 1콜의 중복 제거).
    DART 한도초과는 삼키지 않고 전파해 딥다이브를 중단시킨다(불완전한 데이터로 분석 강행 방지)."""
    cached = ctx._cache.get("recent_periodic_report")
    if cached is not None:
        return cached
    picked = _recent_periodic_rcept(ctx)  # DartQuotaExceeded → 전파(중단)
    if not picked:
        result = {"available": False, "note": "해당 기업의 정기보고서를 찾지 못함(발췌 생략, 다른 도구로 진행)"}
        ctx._cache["recent_periodic_report"] = result
        return result
    rcept, kind = picked
    text = dart.fetch_document_text(ctx.settings.dart_api_key, rcept, ctx.session, max_chars=8000)
    result = {"available": True, "kind": kind, "rcept_no": rcept, "text": text}
    ctx._cache["recent_periodic_report"] = result
    return result


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
            "depreciation": r.depreciation, "capex": r.capex,  # FCFF 산출용(연간 억원)
            "effective_tax_rate": r.effective_tax_rate, "cost_of_debt": r.cost_of_debt,  # WACC·NOPAT 실측
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
    except dart.DartQuotaExceeded:
        raise  # DART 한도초과는 딥다이브 중단(dispatch 가 전파)
    except Exception as e:  # 그 외 외부 IO 경계 방어
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


def _largest_shareholders(ctx: ToolContext) -> dart.LargestShareholders | None:
    """최근 확정 사업연도부터 최대주주 현황(DS005)을 조회. 사업보고서는 다음 해 제출이라 직전 연도부터."""
    today = datetime.now(UTC).date()
    for year in range(today.year - 1, today.year - 4, -1):
        result = dart.fetch_largest_shareholders(
            ctx.settings.dart_api_key, ctx.corp_code, year, 4, ctx.session
        )
        if result:
            return result
    return None


def tool_ownership(ctx: ToolContext, args: dict) -> dict:
    """주주구성·대주주: 최대주주 지분(구조화) + 임원·주요주주 소유변동(임원·주요주주 보고)."""
    if not ctx.corp_code or not ctx.settings.dart_api_key:
        return {"available": False, "note": "DART 키·매핑 없음"}
    try:
        top = _largest_shareholders(ctx)  # DS005 최대주주 현황(구조화 지분)
        changes = dart.fetch_ownership_changes(ctx.settings.dart_api_key, ctx.corp_code, ctx.session)
    except dart.DartQuotaExceeded:
        raise  # DART 한도초과는 딥다이브 중단
    except Exception as e:
        logger.warning("deepdive ownership failed %s: %s", ctx.code, e)
        return {"available": False, "note": "소유변동 조회 실패"}
    items = [
        {"rcept_no": k, "reporter": v.reporter, "position": v.position,
         "shares_after": v.shares_after, "shares_delta": v.shares_delta}
        for k, v in list(changes.items())[:20]
    ]
    result = {"available": True, "count": len(items), "changes": items}
    if top:  # 최대주주명·특수관계인 합산 지분율(LLM 자유서술 대신 구조화 수치)
        result["largest_holder"] = top.top_holder
        result["largest_group_stake_pct"] = top.group_stake_pct
    return result


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
    # ② 이 종목이 속한 산업 리포트. 회사 리포트엔 industry_name 이 대개 없어(조인 키 부재), 종목의
    #    대표 섹터(sector_etf)를 리포트 industry_name 후보로 매핑해 연결한다(#4 해결).
    industries = {r.industry_name for (r, _a) in own if r.industry_name}
    industries |= set(_sector_industry_names(ctx))
    ind_rows = []
    if industries:
        ind_rows = db.execute(
            select(Report, ReportAnalysis)
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
            .where(Report.category == "industry", Report.industry_name.in_(industries))
            .order_by(Report.published_date.desc()).limit(6)
        ).all()
    # ③ 원문·요약에 종목명 언급된 타 리포트(산업 리포트의 개별 종목 언급 포함). full_text 우선 검색
    #    (요약엔 대표주만 남아 소실 — #5 해결). full_text 없으면 rationale 폴백.
    mention_rows = []
    if name:
        mention_rows = db.execute(
            select(Report, ReportAnalysis)
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
            .where(
                Report.stock_code != ctx.code,
                or_(
                    ReportAnalysis.full_text.contains(name),
                    ReportAnalysis.rationale.contains(name),
                ),
            )
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
    name = company_service.report_stock_name(ctx.db, ctx.code) or company_service.resolve_stock_name(ctx.db, ctx.code)
    res = websearch.research(
        ctx.settings, str(query), ctx.session,
        sort=args.get("sort", "sim"),
        crawl_bodies=int(args.get("crawl", 4)),
        aliases=search_aliases(ctx, name),  # 종목·관계사명 관련성 필터
        seen_titles=_seen_titles(ctx),  # job 스코프 중복 방지
    )
    res["available"] = bool(res.get("hits") or res.get("bodies"))
    return res


def sector_for(ctx: ToolContext) -> str | None:
    """종목의 대표 국내 섹터(이벤트 키워드 선택용). 테마→섹터 매핑은 reporter.sector_etf 소유."""
    from reporter import sector_etf

    themes = company_service.theme_names(ctx.db, ctx.code)
    return sector_etf.stock_kr_sector(ctx.code, themes)


def search_aliases(ctx: ToolContext, name: str | None) -> list[str]:
    """웹서치 관련성 판정용 alias — 종목명·코드 + 관계사(모/자회사)명. job 스코프 캐시.

    제목엔 종목명이 없어도 본문/스니펫에 모·자회사가 언급된 관련 기사(예: 모회사 기사에 이 종목
    언급)를 포착하기 위해 관계사명을 alias 에 더한다(RelatedCompany, related_company_ingest 수집)."""
    cached = ctx._cache.get("search_aliases")
    if cached is not None:
        return cached
    from app.services import related_company_ingest

    aliases = [a for a in (name, _no_space(name or ""), ctx.code) if a]
    aliases += related_company_ingest.related_names(ctx.db, ctx.code)
    ctx._cache["search_aliases"] = aliases
    return aliases


def _seen_titles(ctx: ToolContext) -> set:
    """job 스코프 '이미 본 제목' 집합 — 단계 간 웹서치 중복 크롤·중복 주입 방지."""
    return ctx._cache.setdefault("seen_titles", set())


def _sector_industry_names(ctx: ToolContext) -> list[str]:
    """종목의 대표 섹터를 산업 리포트 industry_name 후보로 매핑(#4 — 산업↔종목 연결).

    섹터→산업 매핑은 reporter.sector_etf 가 소유(딥다이브·종목 커버리지 공유 단일 소스)."""
    from reporter import sector_etf

    return list(sector_etf.kr_sector_to_report_industries(sector_for(ctx)))


def _event_candidates(ctx: ToolContext, name: str, kw) -> list[dict]:
    """이벤트 뉴스 후보 수집(하이브리드). (1) 종목 직결 뉴스 — 오매칭 0·최근 주요뉴스,
    (2) 섹터 키워드 검색 — 과거 이벤트 커버리지. 두 소스 병합, 종목명 포함 기사만, URL 중복 제거."""
    from app.adapters.external import naver_search, naver_stock_news

    # 종목명·코드 + 관계사(모/자회사)명 — 제목엔 종목명 없어도 관계사 언급 기사 포착.
    aliases = search_aliases(ctx, name)
    cand: dict[str, dict] = {}  # url → {title, summary, press, datetime, trusted}

    # (1) 종목 직결 뉴스 — 종목코드 연결이라 주체가 이 종목(신뢰). trusted=True.
    try:
        for n in naver_stock_news.fetch_stock_news(ctx.code, ctx.session, pages=2):
            cand[n.url] = {"title": n.title, "summary": n.summary, "press": n.press,
                           "datetime": n.datetime, "trusted": True}
    except Exception as e:
        logger.warning("stock news failed %s: %s", ctx.code, e)

    # (2) 섹터 촉매·리스크 키워드 검색(과거 이벤트 포착). 종목명이 제목·요약에 있는 기사만 후보로.
    # 단 키워드 검색은 '여러 종목 나열' 기사를 섞으므로 trusted=False → 본문 주체성 검증(아래) 필요.
    # 쿼리 수는 촉매·리스크 각 4개로 제한(네이버 API rate limit 429 회피 + 시간 통제).
    cid, secret = ctx.settings.naver_client_id, ctx.settings.naver_client_secret
    if cid and secret:
        for k in kw.catalysts[:4] + kw.risks[:4]:
            try:
                hits = naver_search.search_news(cid, secret, f"{name} {k}", ctx.session,
                                                display=5, sort="date")
            except Exception:
                continue
            for h in hits:
                text = _no_space(h.title + h.description)
                if h.link not in cand and any(_no_space(a) in text for a in aliases if a):
                    cand[h.link] = {"title": h.title, "summary": h.description, "press": "",
                                    "datetime": h.post_date, "trusted": False}
    return [{"url": u, **v} for u, v in cand.items()]


def _is_subject(name: str, title: str, body: str) -> bool:
    """이 기사가 해당 종목을 '주체'로 다루는가(단순 나열·비교 언급 배제).

    제목에 종목명(또는 영문 KINX 류 alias)이 있으면 주체. 아니면 본문에서 3회 이상 언급돼야 주체로
    본다(여러 종목 나열하는 산업·시황 기사는 대개 1~2회만 스침)."""
    ntitle = _no_space(title)
    if _no_space(name) in ntitle or "KINX" in title.upper():
        return True
    mentions = body.count(name) + body.upper().count("KINX")
    return mentions >= 3


def tool_event_search(ctx: ToolContext, args: dict) -> dict:
    """미래 이벤트(촉매·리스크) 탐색. 종목 직결 뉴스 + 섹터 키워드 검색(하이브리드) + DART 공시.

    상방(수주·계약·증설)·하방(소송·유증·우발부채)을 함께 찾는다. 섹터별 키워드는 공통+섹터별
    이중관리(domain.event_keywords). 이벤트성 상위 기사는 본문까지 크롤. args: max_articles(기본 6)."""
    from app.adapters.external import article_crawler
    from app.domain import event_keywords as ek

    name = company_service.report_stock_name(ctx.db, ctx.code) or company_service.resolve_stock_name(
        ctx.db, ctx.code
    )
    if not name:
        return {"available": False, "note": "종목명 해석 실패"}
    sector = sector_for(ctx)
    kw = ek.for_sector(sector)
    candidates = _event_candidates(ctx, name, kw)

    # 제목+요약으로 촉매/리스크 키워드 매칭 점수화. 이벤트성 기사를 앞으로.
    def _hits(text: str, keywords: list[str]) -> list[str]:
        return [k for k in keywords if k in text]

    scored = []
    for c in candidates:
        text = f"{c['title']} {c['summary']}"
        cats = _hits(text, kw.catalysts)
        risks = _hits(text, kw.risks)
        scored.append((len(cats) + len(risks), c, cats, risks))
    scored.sort(key=lambda x: (x[0] > 0, x[0], x[1]["datetime"]), reverse=True)

    max_articles = int(args.get("max_articles", 6))
    articles: list[dict] = []
    for score, c, cats, risks in scored:
        item = {"title": c["title"], "press": c["press"], "datetime": c["datetime"], "url": c["url"],
                "summary": c["summary"][:600], "catalyst_hits": cats, "risk_hits": risks}
        # 이벤트 매칭된 상위 기사만 전체 본문 크롤(토큰·시간 통제).
        if score > 0 and len([a for a in articles if a.get("body")]) < max_articles:
            body = article_crawler.crawl_article(c["url"], ctx.session)
            if body and body.get("body"):
                full = body["body"]
                # 키워드 검색 기사(trusted=False)는 이 종목이 주체인지 본문으로 검증. 단순 나열·비교
                # 언급(타종목 기사에 스친 것)이면 제외 — LLM 이 타종목 이벤트를 오인하는 것 방지.
                if not c.get("trusted") and not _is_subject(name, c["title"], full):
                    continue
                item["body"] = full[:2500]
        elif not c.get("trusted") and not _is_subject(name, c["title"], c["summary"]):
            # 본문 미크롤 + 비신뢰 기사: 제목·요약만으로 주체성 판정, 아니면 제외.
            continue
        articles.append(item)

    # DART 이벤트 공시(공급계약·소송·유증 등) 병행 — 구조화 정본.
    disclosures: list[dict] = []
    if ctx.corp_code and ctx.settings.dart_api_key:
        begin = date.today() - timedelta(days=365)
        try:
            seen: set[str] = set()
            # 전체 공시 → 섹터 키워드 필터. + 주요사항보고(DS005, pblntf_ty='B')는 이미 정형이라
            # 키워드 없이 전량 병합(유증·CB·자기주식·합병 등 촉매·리스크 정본).
            rows = dart.fetch_disclosures(
                ctx.settings.dart_api_key, ctx.corp_code, ctx.code, begin, date.today(), ctx.session,
            )
            major = dart.fetch_disclosures(
                ctx.settings.dart_api_key, ctx.corp_code, ctx.code, begin, date.today(),
                ctx.session, pblntf_ty="B",
            )
            for d in rows:
                if any(f in d.report_nm for f in kw.disclosure_filters):
                    seen.add(d.rcept_no)
                    disclosures.append({"rcept_no": d.rcept_no, "report_nm": d.report_nm,
                                        "rcept_dt": d.rcept_dt.isoformat(), "material": False})
            for d in major:
                if d.rcept_no not in seen:
                    seen.add(d.rcept_no)
                    disclosures.append({"rcept_no": d.rcept_no, "report_nm": d.report_nm,
                                        "rcept_dt": d.rcept_dt.isoformat(), "material": True})
        except dart.DartQuotaExceeded:
            # event_search 는 뉴스가 주 소스이고 DART 공시는 보조라 여기선 중단하지 않고 뉴스로 진행.
            # (정기보고서·공시가 핵심인 overview·redflags 단계는 dispatch 가 전파해 중단됨.)
            disclosures = []
        except Exception as e:
            logger.warning("event disclosures failed %s: %s", ctx.code, e)

    return {
        "available": True, "sector": sector,
        "catalyst_keywords": kw.catalysts[:10], "risk_keywords": kw.risks[:10],
        "news": articles[:12],
        "event_disclosures": disclosures[:20],
    }


def tool_fetch_web_page(ctx: ToolContext, args: dict) -> dict:
    """URL 본문 추출. 네이버 블로그(iframe 해제) + 일반 뉴스/기사(범용 추출). args: url(필수)."""
    from app.adapters.external import article_crawler, blog_crawler

    url = args.get("url")
    if not url:
        return {"available": False, "note": "url 필요"}
    # 네이버 블로그는 전용 크롤러(iframe 해제), 그 외(뉴스·언론사)는 범용 기사 추출기.
    page = blog_crawler.crawl_blog(str(url), ctx.session) or article_crawler.crawl_article(
        str(url), ctx.session
    )
    if not page:
        return {"available": False, "note": "본문 추출 실패(비공개·구조 상이)"}
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
    "fetch_web_page": (tool_fetch_web_page, "URL 본문 추출(블로그·뉴스) (args: url)"),
    "event_search": (tool_event_search, "미래 이벤트 탐색 — 섹터별 촉매(수주·계약·증설)·리스크"
                     "(소송·유증·우발부채) 뉴스 본문+DART 공시 (args: side, max_queries)"),
}


def dispatch(name: str, ctx: ToolContext, args: dict) -> dict:
    """도구 이름으로 실행. 미지의 도구·일반 예외는 오류 dict(에이전트 루프가 다음 판단에 반영).

    단 DART 한도초과(DartQuotaExceeded)는 삼키지 않고 전파한다 — 삼키면 LLM 이 DART 도구를 상한까지
    재시도하며 매 호출이 스로틀·타임아웃을 겪어 딥다이브가 오래 매달린다. 즉시 중단이 옳다."""
    entry = TOOLS.get(name)
    if not entry:
        return {"error": f"unknown tool: {name}", "available_tools": list(TOOLS)}
    try:
        return entry[0](ctx, args or {})
    except dart.DartQuotaExceeded:
        raise  # 상위(run_stage→run_job)가 딥다이브를 중단·실패 처리
    except Exception as e:  # 그 외 도구 실패는 루프를 죽이지 않게 오류 dict
        logger.warning("deepdive tool %s failed %s: %s", name, ctx.code, e)
        # DB 오류(데드락 등)로 트랜잭션이 중단(aborted) 상태가 되면 이후 모든 DB 호출이
        # InFailedSqlTransaction 으로 실패한다. 롤백으로 트랜잭션을 초기화해 다음 툴 호출이
        # 정상 동작하도록 한다(딥다이브 툴은 읽기 전용이므로 롤백 안전).
        with suppress(Exception):
            ctx.db.rollback()
        return {"error": f"tool {name} failed: {e}"}


def resolve_corp_code(db: Session, code: str) -> str | None:
    """종목코드 → DART corp_code(CorpCodeMap). 딥다이브 시작 시 1회 조회해 ctx 에 캐시."""
    return db.scalar(select(CorpCodeMap.corp_code).where(CorpCodeMap.stock_code == code))
