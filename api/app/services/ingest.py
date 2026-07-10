"""수집 파이프라인: 네이버 리서치 크롤 → PDF 저장 → GLM 요약·센티먼트 → Postgres 영속.

기존 reporter.crawler / reporter.pdf / reporter.analyzer / reporter.ollama_client 를 재사용한다.
동기 requests 기반이므로 스케줄러 워커나 threadpool(def 엔드포인트)에서 호출한다.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import DailyMarketInfo, Report, ReportAnalysis, Sentiment
from app.services import sentiment as sentiment_svc
from app.storage import minio_store
from reporter import analyzer, article, fallback, market, news, us_market
from reporter.crawler import crawl_categories
from reporter.models import Report as CrawledReport
from reporter.ollama_client import OllamaClient
from reporter.pdf import extract_text_from_bytes

logger = logging.getLogger(__name__)

_SUMMARY_PAGES = 3
_SENTIMENT_PAGES = 5


def _to_date(yymmdd: str) -> date:
    """네이버 목록의 'YY.MM.DD' 를 date 로 변환한다."""
    return datetime.strptime(yymmdd, "%y.%m.%d").date()


def _download_pdf(url: str, session: requests.Session) -> bytes | None:
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        logger.warning("PDF download failed %s: %s", url, e)
        return None


def _dedup_key(cr: CrawledReport) -> str:
    """리포트 고유 식별자. read_url 이 없으면 (제목·증권사·발행일) 조합으로 안정적 폴백."""
    return cr.read_url or f"{cr.category}|{cr.title}|{cr.broker}|{cr.date}"


def _ingest_one(
    db: Session, client: OllamaClient, settings: Settings, cr: CrawledReport, session: requests.Session
) -> Report | None:
    """크롤된 리포트 1건을 저장·분석한다. 이미 있으면 건너뛴다. PDF 없으면 None."""
    dedup = _dedup_key(cr)
    if db.scalar(select(Report).where(Report.read_url == dedup)):
        return None  # 멱등성: 이미 수집됨 (read_url 없으면 조합키가 read_url 컬럼에 저장됨)
    if not cr.pdf_url:
        return None

    pdf_bytes = _download_pdf(cr.pdf_url, session)
    if not pdf_bytes:
        return None

    summary_text = extract_text_from_bytes(pdf_bytes, _SUMMARY_PAGES)
    if not summary_text:
        return None
    sentiment_text = extract_text_from_bytes(pdf_bytes, _SENTIMENT_PAGES)

    # 결정적 키: 재시도해도 동일 객체를 덮어써 고아가 쌓이지 않는다.
    digest = hashlib.sha256(dedup.encode()).hexdigest()[:16]
    object_key = f"{cr.category}/{_to_date(cr.date).isoformat()}/{digest}.pdf"

    report = Report(
        category=cr.category,
        title=cr.title,
        broker=cr.broker,
        published_date=_to_date(cr.date),
        views=cr.views,
        stock_code=cr.stock_code,
        stock_name=cr.stock_name,
        industry_name=cr.industry,  # 산업분석 목록의 '분류' 컬럼(업종명)
        read_url=dedup,  # 실제 URL 또는 폴백 조합키. UNIQUE 제약이 NULL 케이스도 보호하도록.
        pdf_url=cr.pdf_url,
        pdf_object_key=object_key,
    )

    # 1차 요약(기존 로직 재사용) + 센티먼트(신규)
    cr.text = summary_text
    summarized = analyzer.summarize_reports(client, settings.summary_model, [cr])
    summary = summarized[0].summary if summarized else ""
    sent = sentiment_svc.classify(
        client, settings.insight_model, cr.category, cr.title, sentiment_text or summary_text
    )

    report.analysis = ReportAnalysis(
        summary=summary or sent.one_liner,
        sentiment=Sentiment(sent.sentiment),
        rationale=sent.rationale,
        model=settings.summary_model,
    )
    # 분석까지 성공한 뒤에 PDF 를 저장해, 중간 실패 시 MinIO 고아가 남지 않게 한다.
    minio_store.put_pdf(object_key, pdf_bytes)
    db.add(report)
    return report


def ingest_reports(db: Session, settings: Settings, target_date: str | None = None) -> int:
    """종목·산업 리포트를 수집·분석·저장한다. 저장한(신규) 리포트 수를 반환한다."""
    crawled = crawl_categories(list(settings.report_categories), target_date=target_date)
    if not crawled:
        logger.info("no reports crawled for %s", target_date or "today")
        return 0

    client = OllamaClient(settings.ollama_host, settings.ollama_api_key)
    session = requests.Session()
    saved = 0
    for cr in crawled:
        try:
            if _ingest_one(db, client, settings, cr, session) is not None:
                db.commit()
                saved += 1
            else:
                db.rollback()
        except Exception as e:  # 한 건 실패가 전체 배치를 막지 않도록
            db.rollback()
            logger.warning("ingest failed for %s: %s", cr.title, e)
    logger.info("ingested %d new reports", saved)
    return saved


def backfill_industry_names(db: Session, target_date: str | None = None) -> int:
    """기존 산업 리포트의 누락된 industry_name('분류')을 재크롤해 채운다.

    크롤러가 '분류' 컬럼을 잡기 전에 적재된 데이터 보정용. read_url 로 매칭한다.
    갱신한 행 수를 반환한다.
    """
    crawled = crawl_categories(["industry"], target_date=target_date)
    updated = 0
    for cr in crawled:
        if not cr.industry or not cr.read_url:
            continue
        report = db.scalar(select(Report).where(Report.read_url == cr.read_url))
        if report and not report.industry_name:
            report.industry_name = cr.industry
            updated += 1
    if updated:
        db.commit()
    return updated


# 시황 국면 경계(KST). 09:30~ 장중, 16:00~ 마감(15:30 마감 + 마감시황 리포트 발행 시차).
# 09:00~09:30 은 지수 데이터가 얇아 개장 전(forecast) 유지.
_MARKET_OPEN = (9, 30)
_MARKET_CLOSE_HOUR = 16

# 뉴스 종합 시 본문까지 크롤할 상위 기사 수(headless 라 무거워 소수만). pipeline 과 동일 정책.
_NEWS_ARTICLE_TOP = 3
_INTRADAY_NEWS_LIMIT = 8


def _market_phase(now: datetime) -> str:
    """현재 시각으로 시황 국면을 판정한다: forecast(개장 전)/intraday(장중)/closing(마감 후)."""
    if now.hour >= _MARKET_CLOSE_HOUR:
        return "closing"
    if (now.hour, now.minute) >= _MARKET_OPEN:
        return "intraday"
    return "forecast"


def _quote_line(q) -> str:
    """IndexQuote → '코스피 2,650.12 (+0.45%)' 형태 한 줄."""
    ratio = q.change_ratio
    sign = "" if ratio.startswith(("-", "+")) else "+"
    return f"{q.name} {q.close} ({sign}{ratio}%)"


def _news_blocks(items, session: requests.Session) -> list[str]:
    """뉴스 아이템 → LLM 입력 블록. 상위 몇 건만 본문 크롤(무거움), 나머지는 제목만."""
    blocks: list[str] = []
    for it in items[:_NEWS_ARTICLE_TOP]:
        body = article.fetch_article_text(it.link)
        blocks.append(f"[{it.source}] {it.title}\n{body}" if body else f"[{it.source}] {it.title}")
    for it in items[_NEWS_ARTICLE_TOP:]:
        blocks.append(f"[{it.source}] {it.title}")
    return blocks


def _build_intraday(settings: Settings, session: requests.Session) -> tuple[str, int] | None:
    """장중: 리서치 제외. 실시간 지수·환율 + 장중 뉴스로 '지금 장 상황'을 종합한다.

    (요약 텍스트, source_count) 또는 근거를 전혀 못 구하면 None.
    """
    quotes = [
        *us_market.fetch_kr_indices(session),
        *us_market.fetch_exchange_rates(session),
        *us_market.fetch_us_indices(session),  # 간밤 미국 마감(참고)
    ]
    items = news.collect(news.MARKET_NEWS_KEYWORDS, _INTRADAY_NEWS_LIMIT, session)
    if not quotes and not items:
        return None
    quote_lines = [_quote_line(q) for q in quotes]
    blocks = _news_blocks(items, session)
    client = OllamaClient(settings.ollama_host, settings.ollama_api_key)
    briefing = analyzer.synthesize_intraday(client, settings.insight_model, quote_lines, blocks)
    return briefing.text, len(quotes) + len(items)


def _build_research(
    settings: Settings, phase: str, target_date: str | None, session: requests.Session
) -> tuple[str, int] | None:
    """개장 전/마감: 증권사 리서치 + 장중/글로벌 뉴스로 예상 또는 마감 리뷰를 종합한다."""
    crawled = crawl_categories(["market_info"], target_date=target_date)
    if not crawled:
        logger.info("no market_info reports")
        return None

    if phase == "closing":
        _morning, sources = market.split_by_closing(crawled)
        if not sources:  # 마감시황 리포트가 아직이면 전체로 폴백
            fallback.log_fallback(
                "market_brief.closing_to_all",
                reason="장 마감 후이나 국내 마감시황 리포트 미발행 → 전체 리포트로 폴백",
                detail=str(target_date or datetime.now().date()),
            )
            sources = crawled
    else:
        sources = crawled

    texts: list = []
    for cr in sources:
        if not cr.pdf_url:
            continue
        pdf_bytes = _download_pdf(cr.pdf_url, session)
        if pdf_bytes:
            text = extract_text_from_bytes(pdf_bytes, _SUMMARY_PAGES)
            if text:
                cr.text = text
                texts.append(cr)
    if not texts:
        return None

    client = OllamaClient(settings.ollama_host, settings.ollama_api_key)
    summarized = analyzer.summarize_reports(client, settings.summary_model, texts)
    if not summarized:
        return None

    # 리서치 요약에 장중 뉴스를 근거로 합류(개장 전은 글로벌, 마감은 국내 장중 뉴스).
    keywords = news.GLOBAL_NEWS_KEYWORDS if phase == "forecast" else news.MARKET_NEWS_KEYWORDS
    news_items = news.collect(keywords, _INTRADAY_NEWS_LIMIT, session)
    for block in _news_blocks(news_items, session):
        summarized.append(_NewsReport(block))

    synth = analyzer.synthesize_closing_review if phase == "closing" else analyzer.synthesize_forecast
    briefing = synth(client, settings.insight_model, summarized)
    return briefing.text, len(summarized)


class _NewsReport:
    """리서치 요약 리스트에 뉴스 블록을 섞기 위한 최소 어댑터(analyzer 는 .label/.summary 만 읽음)."""

    def __init__(self, block: str) -> None:
        self.label = "[news] 장중 뉴스"
        self.summary = block
        self.category = "news"


def build_market_brief(
    db: Session, settings: Settings, target_date: str | None = None, phase: str | None = None
) -> str | None:
    """당일 시황을 국면별로 종합해 daily_market_info 에 저장한다.

    - forecast(개장 전): 전날 국내마감+간밤 미장 리서치 + 글로벌 뉴스로 '오늘 예상'.
    - intraday(장중): 리서치 제외, 실시간 지수·환율 + 국내 장중 뉴스로 '지금 장 상황'.
    - closing(마감 후): 오늘 국내 마감시황 리서치 + 국내 뉴스로 '마감 리뷰+내일 전망'.

    phase 미지정 시: 과거 백필(target_date 지정)은 마감 리뷰(장중 실시간 불가), 그 외는
    현재 시각으로 판정. market_date 는 수집 실행일(또는 지정일)로 고정한다.
    """
    if phase is None:
        phase = "closing" if target_date else _market_phase(datetime.now())

    session = requests.Session()
    if phase == "intraday":
        built = _build_intraday(settings, session)
    else:
        built = _build_research(settings, phase, target_date, session)
    if built is None:
        return None
    summary_text, source_count = built

    # market_date = 수집 실행일(지정일 우선). 리스트 최상단 발행일에 의존하지 않는다.
    market_date = _to_date(target_date) if target_date else datetime.now().date()
    existing = db.scalar(select(DailyMarketInfo).where(DailyMarketInfo.market_date == market_date))
    if existing:
        existing.summary = summary_text
        existing.source_count = source_count
        existing.model = settings.insight_model
        existing.phase = phase
        existing.updated_at = datetime.now().astimezone()
    else:
        db.add(
            DailyMarketInfo(
                market_date=market_date,
                summary=summary_text,
                source_count=source_count,
                model=settings.insight_model,
                phase=phase,
            )
        )
    db.commit()
    return summary_text
