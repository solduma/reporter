"""산업 흐름 페이지용 라우터 — 산업별 발간일별 센티먼트 시계열."""

from __future__ import annotations

from datetime import date

import requests
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters.external import customs
from app.config import get_settings
from app.db.models import (
    Report,
    ReportAnalysis,
    TradeStat,
    UniverseSnapshot,
)
from app.db.session import get_session
from app.domain.analysis_scoring import SENTIMENT_SCORE
from app.schemas import IndustrySummary, ReportRef, SectorStock, SentimentPoint, TradePoint
from app.services import sector_ingest, universe_ingest
from reporter import sector_etf, us_market

router = APIRouter(prefix="/api/industries", tags=["industries"])


@router.get("", response_model=list[IndustrySummary])
def list_industries(db: Session = Depends(get_session)) -> list[IndustrySummary]:
    # 센티먼트 시계열과 동일하게 analysis 가 있는 리포트만 센다(카운트-플롯 정합성).
    rows = db.execute(
        select(Report.industry_name, func.count(Report.id))
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(Report.category == "industry", Report.industry_name.is_not(None))
        .group_by(Report.industry_name)
        .order_by(func.count(Report.id).desc())
    ).all()
    return [IndustrySummary(industry=name, report_count=count) for name, count in rows]


@router.get("/{name}/sentiment", response_model=list[SentimentPoint])
def industry_sentiment(
    name: str,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = Query(default=None),
    db: Session = Depends(get_session),
) -> list[SentimentPoint]:
    stmt = (
        select(Report, ReportAnalysis)
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(Report.category == "industry", Report.industry_name == name)
        .order_by(Report.published_date)
    )
    if from_:
        stmt = stmt.where(Report.published_date >= from_)
    if to:
        stmt = stmt.where(Report.published_date <= to)

    by_date: dict[date, list[tuple[Report, ReportAnalysis]]] = {}
    for report, analysis in db.execute(stmt).all():
        by_date.setdefault(report.published_date, []).append((report, analysis))

    points: list[SentimentPoint] = []
    for day in sorted(by_date):
        rows = by_date[day]
        avg = sum(SENTIMENT_SCORE[a.sentiment] for _, a in rows) / len(rows)
        points.append(
            SentimentPoint(
                date=day,
                avg_sentiment=round(avg, 3),
                reports=[
                    ReportRef(
                        id=r.id,
                        title=r.title,
                        broker=r.broker,
                        sentiment=a.sentiment.value,
                        summary=a.summary,
                        read_url=r.read_url,
                        has_pdf=bool(r.pdf_object_key),
                    )
                    for r, a in rows
                ],
            )
        )
    return points


def _kr_sector_stocks(
    db: Session, industry: str, sort: str, limit: int, offset: int
) -> list[SectorStock]:
    """산업명 → 섹터 소속 종목 + 최신 시세. sort=cap|value 정렬 후 [offset:offset+limit]."""
    codes = sector_ingest.sector_stock_codes(db, industry)
    if not codes:
        return []

    as_of = universe_ingest.latest_snapshot_date(db)
    rows = db.execute(
        select(
            UniverseSnapshot.stock_code,
            UniverseSnapshot.stock_name,
            UniverseSnapshot.close_price,
            UniverseSnapshot.change_pct,
            UniverseSnapshot.market_cap,
            UniverseSnapshot.trading_value,
        ).where(
            UniverseSnapshot.snapshot_date == as_of,
            UniverseSnapshot.stock_code.in_(codes),
        )
    ).all()
    key_idx = 5 if sort == "value" else 4  # value=거래대금, cap=시총
    rows = sorted(rows, key=lambda r: r[key_idx] or 0, reverse=True)[offset : offset + limit]
    out: list[SectorStock] = []
    for code, name, close, change, _cap, _val in rows:
        rising = None if change is None or change == 0 else change > 0
        out.append(
            SectorStock(
                name=name,
                code=code,
                symbol=code,  # 국내는 코드가 곧 차트 심볼
                market="KR",
                close=f"{close:,}" if close is not None else None,
                change_ratio=f"{change:+.2f}" if change is not None else None,
                rising=rising,
            )
        )
    return out


def _us_sector_stocks(industry: str, limit: int, offset: int) -> list[SectorStock]:
    """산업명 → 대응 미국 섹터 대표종목 + 네이버 시세. 정적 목록이라 [offset:offset+limit] 슬라이스."""
    kr_sector = sector_etf.themes_to_kr_sector([industry])
    us_sector = sector_etf.kr_sector_to_us(kr_sector)
    symbols = sector_etf.us_sector_stocks(us_sector)[offset : offset + limit]
    if not symbols:
        return []
    quotes = us_market.fetch_us_stock_quotes(symbols)
    return [
        SectorStock(
            name=q.name,
            code=None,  # 미국은 종목분석 페이지 없음
            symbol=symbol,  # 차트 조회용 네이버 심볼
            market="US",
            close=q.close,
            change_ratio=q.change_ratio,
            rising=q.rising,
        )
        for symbol, q in quotes
    ]


@router.get("/{name}/stocks", response_model=list[SectorStock])
def sector_stocks(
    name: str,
    market: str = Query(default="KR", pattern="^(KR|US)$"),
    sort: str = Query(default="cap", pattern="^(cap|value)$"),
    limit: int = Query(default=30, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> list[SectorStock]:
    """섹터 소속 종목 명단. 국내=judal 매칭+시세(cap|value 정렬), 미국=대표종목 정적매핑+시세."""
    if market == "KR":
        return _kr_sector_stocks(db, name, sort, limit, offset)
    return _us_sector_stocks(name, limit, offset)


# 별도 라우터: 무역통계는 /api/trade (산업 흐름 페이지 하단).
trade_router = APIRouter(prefix="/api/trade", tags=["trade"])

# 대표 품목 프리셋(HS 4자리). 산업 흐름과 연관 큰 품목 위주.
TRADE_PRESETS = {
    "8542": "반도체",
    "8471": "컴퓨터",
    "8517": "통신기기",
    "2710": "석유제품",
    "8703": "승용차",
    "8708": "자동차부품",
}

# 4자리 대표품목 → 하위 6자리 세부품목(수출 실적 확인된 것만, 수출액 큰 순).
# 관세청 무역통계 API 실데이터로 검증(2025년). 노이즈($0.01B 미만) 코드는 제외.
# 8471(컴퓨터)은 4자리 총액 자체가 작아 드릴다운 실익이 낮아 세분하지 않는다.
TRADE_SUBITEMS: dict[str, dict[str, str]] = {
    "8542": {
        "854232": "메모리",
        "854231": "프로세서·컨트롤러",
        "854239": "기타 집적회로",
        "854233": "증폭기",
    },
    "8517": {
        "851713": "스마트폰",
        "851779": "통신기기 부분품",
        "851762": "통신장비(송수신기기)",
        "851771": "안테나·반사기",
        "851761": "기지국",
        "851714": "기타 전화기",
    },
    "2710": {
        "271019": "중질유(경유·중유·윤활유)",
        "271012": "경질유(휘발유·나프타)",
    },
    "8703": {
        "870323": "가솔린차(1500~3000cc)",
        "870340": "하이브리드차",
        "870380": "전기차(EV)",
        "870322": "가솔린차(1000~1500cc)",
        "870324": "가솔린차(3000cc 초과)",
        "870332": "디젤차(1500~2500cc)",
        "870360": "플러그인 하이브리드(가솔린)",
        "870321": "가솔린차(1000cc 이하)",
        "870333": "디젤차(2500cc 초과)",
    },
    "8708": {
        "870899": "기타 부분품",
        "870840": "기어박스(변속기)",
        "870829": "차체 부분품",
        "870850": "구동·비구동 차축",
        "870880": "서스펜션",
        "870894": "조향장치",
        "870830": "브레이크",
        "870893": "클러치",
        "870870": "로드휠",
        "870810": "범퍼",
        "870892": "머플러·배기관",
        "870821": "안전벨트",
        "870891": "방열기",
    },
}


@trade_router.get("", response_model=list[TradePoint])
def trade_stats(
    hs: str = Query(default="8542", pattern=r"^\d{4,12}$", description="HS 코드(4~12자리)"),
    start: str = Query(..., pattern=r"^\d{6}$", description="시작 YYYYMM"),
    end: str = Query(..., pattern=r"^\d{6}$", description="종료 YYYYMM"),
    db: Session = Depends(get_session),
) -> list[TradePoint]:
    settings = get_settings()
    if settings.customs_api_key:
        fetched = customs.fetch_trade_by_hs(
            settings.customs_api_key, hs, start, end, requests.Session()
        )
        for m in fetched:
            stmt = insert(TradeStat).values(
                hs_code=hs,
                period=m.period,
                export_usd=m.export_usd,
                import_usd=m.import_usd,
                balance_usd=m.balance_usd,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_trade_stat",
                set_={
                    "export_usd": stmt.excluded.export_usd,
                    "import_usd": stmt.excluded.import_usd,
                    "balance_usd": stmt.excluded.balance_usd,
                },
            )
            db.execute(stmt)
        if fetched:
            db.commit()

    # 요청 [start, end] 윈도우만 반환. period 는 'YYYY.MM' 제로패딩이라 문자열 비교로 대소 판정.
    start_p, end_p = f"{start[:4]}.{start[4:]}", f"{end[:4]}.{end[4:]}"
    rows = db.scalars(
        select(TradeStat)
        .where(
            TradeStat.hs_code == hs,
            TradeStat.period.between(start_p, end_p),
        )
        .order_by(TradeStat.period)
    ).all()
    return [
        TradePoint(
            period=r.period,
            export_usd=r.export_usd,
            import_usd=r.import_usd,
            balance_usd=r.balance_usd,
        )
        for r in rows
    ]


@trade_router.get("/presets")
def trade_presets() -> dict:
    """대표품목(4자리)과 그 하위 세부품목(6자리) 프리셋.

    groups: {hs4: 명칭}, subitems: {hs4: {hs6: 명칭}}. 프론트가 4자리 선택 후
    하위 세부품목으로 드릴다운할 수 있게 계층으로 내려준다.
    """
    return {"groups": TRADE_PRESETS, "subitems": TRADE_SUBITEMS}
