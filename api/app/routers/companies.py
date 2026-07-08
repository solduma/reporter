"""기업 분석 페이지용 라우터 — 주가 봉차트 + 종목 요약. (재무/피어/타임라인은 후속 단계)"""

from __future__ import annotations

from datetime import datetime, timedelta

import requests
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    Financial,
    Peer,
    PriceCandle,
    PriceCandleIntraday,
    Report,
    Timeframe,
)
from app.db.session import get_session
from app.schemas import (
    CandlePoint,
    CompanySummary,
    FinancialPeriodOut,
    PeerOut,
)
from app.services import chart, quote

router = APIRouter(prefix="/api/companies", tags=["companies"])

# tf 별 조회 범위(요구사항): 30m=2주, day=1년(3개월도 프론트에서 슬라이스), month=3년
_RANGE_DAYS = {"day": 400, "month": 365 * 3 + 30}


@router.get("/{code}/summary", response_model=CompanySummary)
def company_summary(code: str, db: Session = Depends(get_session)) -> CompanySummary:
    name = db.scalar(
        select(Report.stock_name)
        .where(Report.stock_code == code, Report.stock_name.is_not(None))
        .order_by(Report.published_date.desc())
        .limit(1)
    )
    return CompanySummary(stock_code=code, stock_name=name)


def _upsert_periodic(db: Session, code: str, tf: Timeframe, candles: list[chart.Candle]) -> None:
    for c in candles:
        stmt = insert(PriceCandle).values(
            stock_code=code,
            timeframe=tf,
            bar_date=c.ts.date(),
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume,
            foreign_ratio=c.foreign_ratio,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_candle",
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "foreign_ratio": stmt.excluded.foreign_ratio,
            },
        )
        db.execute(stmt)
    db.commit()


def _upsert_intraday(db: Session, code: str, candles: list[chart.Candle]) -> None:
    for c in candles:
        stmt = insert(PriceCandleIntraday).values(
            stock_code=code, bar_ts=c.ts, open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_candle_intraday",
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )
        db.execute(stmt)
    db.commit()


@router.get("/{code}/candles", response_model=list[CandlePoint])
def company_candles(
    code: str,
    tf: str = Query(default="day", pattern="^(30m|day|month)$"),
    db: Session = Depends(get_session),
) -> list[CandlePoint]:
    session = requests.Session()

    if tf == "30m":
        # 매 조회 시 가용 분봉을 리샘플·누적(cache-aside). 누적분과 합쳐 반환.
        fresh = chart.fetch_intraday_30min(code, session)
        if fresh:
            _upsert_intraday(db, code, fresh)
        # 요구사항은 '최근 2주' 30분봉. cron 누적(8단계)으로 더 쌓여도 2주만 반환한다.
        window_start = datetime.now() - timedelta(days=14)
        rows = db.scalars(
            select(PriceCandleIntraday)
            .where(
                PriceCandleIntraday.stock_code == code,
                PriceCandleIntraday.bar_ts >= window_start,
            )
            .order_by(PriceCandleIntraday.bar_ts)
        ).all()
        return [
            CandlePoint(t=r.bar_ts.isoformat(), o=r.open, h=r.high, low=r.low, c=r.close, v=r.volume)
            for r in rows
        ]

    frame = Timeframe(tf)
    end = datetime.now()
    start = end - timedelta(days=_RANGE_DAYS[tf])
    fresh = chart.fetch_periodic(code, tf, start, end, session)
    if fresh:
        _upsert_periodic(db, code, frame, fresh)
    rows = db.scalars(
        select(PriceCandle)
        .where(PriceCandle.stock_code == code, PriceCandle.timeframe == frame)
        .order_by(PriceCandle.bar_date)
    ).all()
    return [
        CandlePoint(t=r.bar_date.isoformat(), o=r.open, h=r.high, low=r.low, c=r.close, v=r.volume)
        for r in rows
    ]


# 동일업종 테이블의 한글 행 라벨 → peers 컬럼
_PEER_FIELDS = {
    "price": "현재가",
    "market_cap": "시가총액(억)",
    "foreign_ratio": "외국인비율(%)",
    "per": "PER(%)",
    "pbr": "PBR(배)",
    "roe": "ROE(%)",
}


@router.get("/{code}/financials", response_model=list[FinancialPeriodOut])
def company_financials(code: str, db: Session = Depends(get_session)) -> list[FinancialPeriodOut]:
    session = requests.Session()
    fetched = quote.fetch_financials(code, session)
    for f in fetched:
        stmt = insert(Financial).values(
            stock_code=code,
            period=f.period,
            is_estimate=f.is_estimate,
            revenue=f.revenue,
            operating_income=f.operating_income,
            net_income=f.net_income,
            eps=f.eps,
            bps=f.bps,
            per=f.per,
            pbr=f.pbr,
            roe=f.roe,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_financial",
            set_={
                c: getattr(stmt.excluded, c)
                for c in ("is_estimate", "revenue", "operating_income", "net_income",
                          "eps", "bps", "per", "pbr", "roe")
            },
        )
        db.execute(stmt)
    if fetched:
        db.commit()

    rows = db.scalars(
        select(Financial).where(Financial.stock_code == code).order_by(Financial.period)
    ).all()
    return [
        FinancialPeriodOut(
            period=r.period,
            is_estimate=r.is_estimate,
            revenue=r.revenue,
            operating_income=r.operating_income,
            net_income=r.net_income,
            eps=r.eps,
            per=r.per,
            pbr=r.pbr,
            roe=r.roe,
        )
        for r in rows
    ]


@router.get("/{code}/peers", response_model=list[PeerOut])
def company_peers(code: str, db: Session = Depends(get_session)) -> list[PeerOut]:
    session = requests.Session()
    fetched = quote.fetch_peers(code, session)
    for p in fetched:
        vals = {field: p.values.get(label) for field, label in _PEER_FIELDS.items()}
        stmt = insert(Peer).values(
            base_stock_code=code,
            peer_stock_code=p.stock_code,
            peer_name=p.name,
            **vals,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_peer",
            set_={"peer_name": stmt.excluded.peer_name, **{f: getattr(stmt.excluded, f) for f in _PEER_FIELDS}},
        )
        db.execute(stmt)
    if fetched:
        db.commit()

    rows = db.scalars(
        select(Peer).where(Peer.base_stock_code == code)
    ).all()
    return [
        PeerOut(
            stock_code=r.peer_stock_code,
            name=r.peer_name,
            price=r.price,
            market_cap=r.market_cap,
            foreign_ratio=r.foreign_ratio,
            per=r.per,
            pbr=r.pbr,
            roe=r.roe,
        )
        for r in rows
    ]
