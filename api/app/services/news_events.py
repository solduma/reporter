"""매크로/테마 뉴스 → 종목 이벤트 파이프라인.

Google News RSS 로 매크로·신기술·공급망 키워드 뉴스를 모아, LLM 으로 이벤트 유형과 관련
테마를 분류하고, sector_theme 매핑으로 구성종목에 전파해 StockEvent 로 적재한다. 이벤트드리븐
스크리너가 공시·리포트·급등락과 함께 이 뉴스 이벤트를 조회한다.

뉴스는 대개 개별종목이 아닌 섹터/테마 단위라(예: '반도체 장비 공급망 병목'), LLM 이 뉴스와
가장 관련된 기존 테마명을 고르게 하고, 그 테마 구성종목 전체에 이벤트를 붙인다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import NewsArticle, SectorTheme, SectorThemeStock, StockEvent
from app.services.sentiment import _extract_json
from reporter import news
from reporter.ollama_client import OllamaClient, OllamaError

logger = logging.getLogger(__name__)

# 매크로·신기술·공급망·규제 뉴스 검색 키워드(종목 무관 시장 이벤트).
_EVENT_KEYWORDS = [
    "반도체 공급망", "2차전지 공급망", "신기술", "인공지능 반도체", "전기차 배터리",
    "원자재 가격", "환율 급등", "금리 인상", "수출 규제", "미국 관세",
    "공급 부족", "증설 투자", "정부 지원 정책",
]
_MAX_NEWS = 40  # 배치당 최대 뉴스 수(LLM 토큰 통제)
_VALID_KINDS = {"신기술", "공급망", "규제", "매크로", "실적", "정책"}
# 종목 테마가 아닌 상품·그룹 분류(전파 시 과대). LLM 후보에서 제외.
_EXCLUDE_THEMES = {"ETF", "ETN"}
# 한 뉴스가 이보다 많은 종목에 전파되면 너무 광범위 → 이벤트 신호로서 무의미하므로 스킵.
_MAX_PROPAGATION = 120

_SYSTEM = (
    "너는 증시 뉴스를 읽고 어떤 종목 테마에 영향을 주는지 판단하는 애널리스트다. "
    "주어진 뉴스 제목과 테마 후보 목록을 보고, 뉴스와 가장 관련된 테마명 하나(목록에 있는 그대로)와 "
    "이벤트 유형, 한 줄 요약을 뽑는다. 관련 테마가 목록에 없으면 theme 을 빈 문자열로 둔다. "
    "반드시 아래 JSON 만 출력한다.\n"
    '{"event_kind": "신기술|공급망|규제|매크로|실적|정책", "theme": "테마명 또는 빈문자열", '
    '"summary": "뉴스 핵심 한 줄(80자 이내)"}'
)


def _classify(client: OllamaClient, model: str, title: str, theme_names: list[str]) -> dict | None:
    """뉴스 1건을 LLM 으로 분류. {event_kind, theme, summary} 또는 실패 시 None."""
    prompt = f"뉴스 제목: {title}\n\n테마 후보(이 중에서만 고를 것):\n{', '.join(theme_names)}"
    try:
        raw = client.chat(model, _SYSTEM, prompt, temperature=0.2)
    except OllamaError as e:
        logger.warning("news classify failed for %s: %s", title[:40], e)
        return None
    data = _extract_json(raw)
    if not data:
        return None
    kind = str(data.get("event_kind", "")).strip()
    if kind not in _VALID_KINDS:
        kind = "매크로"
    return {
        "event_kind": kind,
        "theme": str(data.get("theme", "")).strip(),
        "summary": str(data.get("summary", "")).strip()[:80],
    }


def _theme_names(db: Session) -> list[str]:
    """LLM 후보 테마명(상품·그룹 분류 제외). 그룹주(예: 'CJ그룹')도 이벤트 테마로 부적합해 뺀다."""
    names = db.scalars(select(SectorTheme.name)).all()
    return [n for n in names if n not in _EXCLUDE_THEMES and not n.endswith("그룹")]


def _theme_stock_codes(db: Session, theme: str) -> list[str]:
    """정확한 테마명의 구성종목만. sector_stock_codes 는 대표섹터로 접어 과전파(반도체→131종목)
    되므로, 뉴스 전파는 그 테마(judal_idx)의 구성종목(반도체→22종목)만 쓴다."""
    idxs = list(db.scalars(select(SectorTheme.judal_idx).where(SectorTheme.name == theme)).all())
    if not idxs:
        return []
    return list(
        db.scalars(
            select(SectorThemeStock.stock_code)
            .where(SectorThemeStock.judal_idx.in_(idxs))
            .distinct()
        ).all()
    )


def run_news_events(db: Session, settings: Settings | None = None) -> dict:
    """뉴스 수집 → LLM 분류 → 테마 구성종목 전파 → StockEvent 적재. 결과 dict.

    link 로 멱등: 이미 저장된 뉴스는 재분류하지 않는다.
    """
    settings = settings or get_settings()
    if not settings.ollama_api_key:
        return {"news": 0, "classified": 0, "events": 0}

    session = requests.Session()
    items = news.collect(_EVENT_KEYWORDS, limit=_MAX_NEWS, session=session)
    if not items:
        return {"news": 0, "classified": 0, "events": 0}

    # 이미 저장된 link 는 건너뛴다(멱등).
    links = [it.link for it in items]
    existing = set(
        db.scalars(select(NewsArticle.link).where(NewsArticle.link.in_(links))).all()
    )
    fresh = [it for it in items if it.link not in existing]

    client = OllamaClient(settings.ollama_host, settings.ollama_api_key)
    theme_names = _theme_names(db)
    now = datetime.now(UTC)
    classified = events = 0

    for it in fresh:
        result = _classify(client, settings.insight_model, it.title, theme_names)
        # 뉴스 원문 저장(분류 실패해도 이력 보존).
        news_row = _upsert_news(db, it, now, result)
        if result is None:
            db.commit()
            continue
        classified += 1
        # 테마 매핑 → 구성종목 전파.
        theme = result["theme"]
        if theme and theme in theme_names:
            codes = _theme_stock_codes(db, theme)
            if len(codes) > _MAX_PROPAGATION:
                codes = []  # 너무 광범위한 테마는 전파 스킵(이벤트 신호 무의미)
            for code in codes:
                _upsert_event(db, code, news_row.id, result, now.date())
                events += 1
        db.commit()

    logger.info("news events: %d fresh, %d classified, %d stock events", len(fresh), classified, events)
    return {"news": len(fresh), "classified": classified, "events": events}


def _upsert_news(db: Session, item, now: datetime, result: dict | None) -> NewsArticle:
    stmt = (
        insert(NewsArticle)
        .values(
            link=item.link,
            title=item.title,
            source=item.source[:128],
            published_at=now,
            event_kind=(result["event_kind"] if result else ""),
            theme=(result["theme"] if result else ""),
            summary=(result["summary"] if result else ""),
            classified=result is not None,
        )
        .on_conflict_do_update(
            constraint="uq_news_link",
            set_={
                "event_kind": (result["event_kind"] if result else ""),
                "theme": (result["theme"] if result else ""),
                "summary": (result["summary"] if result else ""),
                "classified": result is not None,
            },
        )
        .returning(NewsArticle.id)
    )
    news_id = db.execute(stmt).scalar_one()
    db.flush()
    return db.get(NewsArticle, news_id)


def _upsert_event(db: Session, code: str, news_id: int, result: dict, event_date) -> None:
    stmt = (
        insert(StockEvent)
        .values(
            stock_code=code,
            news_id=news_id,
            event_kind=result["event_kind"],
            theme=result["theme"],
            summary=result["summary"],
            event_date=event_date,
        )
        .on_conflict_do_nothing(constraint="uq_stock_event")
    )
    db.execute(stmt)


def recent_events_by_code(db: Session, codes: list[str], since) -> dict[str, dict]:
    """종목별 최근 뉴스 이벤트(최신 1건). {code: {kind, date, summary}}. 스크리너용."""
    if not codes:
        return {}
    rows = db.scalars(
        select(StockEvent)
        .where(StockEvent.stock_code.in_(codes), StockEvent.event_date >= since)
        .order_by(StockEvent.event_date.desc())
    ).all()
    out: dict[str, dict] = {}
    for r in rows:
        if r.stock_code not in out:
            out[r.stock_code] = {"kind": "뉴스", "date": r.event_date, "summary": r.summary}
    return out
