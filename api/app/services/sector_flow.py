"""수급 기반 섹터 로테이션 — 섹터 ETF 일봉에서 자금 유입 강도 스코어.

각 섹터 ETF 의 기술 지표(technicals)를 자금 흐름 관점으로 재조합한다:
모멘텀(추세) + 거래량 급증(관심) + 신고가 근접(주도력) + (국내)외국인 순증.
스코어 계산은 순수 함수, 데이터 조립(compute_flows)은 섹터 ETF 차트를 조회한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import requests

from app.services import chart, technicals
from app.services.technicals import Technicals
from reporter import sector_etf


@dataclass
class SectorFlow:
    sector: str
    market: str  # KR | US
    symbol: str
    flow_score: float | None  # 0~100 자금유입 강도
    return_3m: float | None
    near_high_pct: float | None
    vol_ratio: float | None
    foreign_delta: float | None  # 외국인비율 최근 변화(pp), 국내만


def flow_score(tech: Technicals, foreign_delta: float | None) -> float | None:
    """섹터 ETF 기술 지표를 0~100 자금유입 스코어로. 계산 가능한 항목만 가중 평균."""
    parts: list[tuple[float, float]] = []
    if tech.return_3m is not None:
        # -20%~+40% → 0~1. 추세가 핵심 가중.
        parts.append((max(0.0, min((tech.return_3m + 20) / 60, 1.0)), 0.40))
    if tech.near_high_pct is not None:
        # 70%~100% 근접 → 0~1. 주도 섹터일수록 신고가권.
        parts.append((max(0.0, min((tech.near_high_pct / 100 - 0.7) / 0.3, 1.0)), 0.30))
    if tech.vol_ratio is not None:
        # 거래량 0.5배=0, 2배↑=1. 관심 유입.
        parts.append((max(0.0, min((tech.vol_ratio - 0.5) / 1.5, 1.0)), 0.20))
    if foreign_delta is not None:
        # 외국인비율 -1pp~+1pp → 0~1(국내 전용 수급 신호).
        parts.append((max(0.0, min((foreign_delta + 1) / 2, 1.0)), 0.10))
    if not parts:
        return None
    total_w = sum(w for _, w in parts)
    return round(sum(v * w for v, w in parts) / total_w * 100, 1)


def foreign_delta(foreign_ratios: list[float | None], lookback: int = 20) -> float | None:
    """외국인 보유율의 최근 변화(pp). 최신 - lookback거래일 전. 데이터 부족 시 None."""
    vals = [(i, r) for i, r in enumerate(foreign_ratios) if r is not None]
    if len(vals) < 2:
        return None
    last_i, last = vals[-1]
    # lookback 이전 중 가장 가까운 유효값.
    prior = next((r for i, r in reversed(vals) if i <= last_i - lookback), vals[0][1])
    return round(last - prior, 2)


def compute_flows(market: str, session: requests.Session | None = None) -> list[SectorFlow]:
    """시장(KR|US)의 모든 섹터 ETF flow 를 계산해 flow_score 내림차순으로 반환한다."""
    session = session or requests.Session()
    etfs = sector_etf.KR_SECTOR_ETFS if market == "KR" else sector_etf.US_SECTOR_ETFS
    end = datetime.now()
    start = end - timedelta(days=400)

    flows: list[SectorFlow] = []
    for etf in etfs:
        if market == "KR":
            candles = chart.fetch_periodic(etf.symbol, "day", start, end, session)
        else:
            candles = chart.fetch_periodic_foreign(etf.symbol, "day", start, end, session)
        if not candles:
            continue
        tech = technicals.compute(candles)
        fd = foreign_delta([c.foreign_ratio for c in candles]) if market == "KR" else None
        flows.append(
            SectorFlow(
                sector=etf.sector,
                market=market,
                symbol=etf.symbol,
                flow_score=flow_score(tech, fd),
                return_3m=tech.return_3m,
                near_high_pct=tech.near_high_pct,
                vol_ratio=tech.vol_ratio,
                foreign_delta=fd,
            )
        )
    flows.sort(key=lambda f: (f.flow_score is not None, f.flow_score or 0), reverse=True)
    return flows
