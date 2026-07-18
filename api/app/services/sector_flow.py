"""수급 기반 섹터 로테이션 — 섹터 ETF 일봉에서 자금 유입 강도 스코어.

각 섹터 ETF 의 기술 지표(technicals)를 자금 흐름 관점으로 재조합한다:
모멘텀(추세) + 거래량 급증(관심) + 신고가 근접(주도력) + (국내)외국인 순증.
스코어 계산은 순수 함수, 데이터 조립(compute_flows)은 섹터 ETF 차트를 조회한다.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import requests

from app.domain import analysis_scoring as domain_scoring
from app.domain import technicals
from app.domain.technicals import Technicals
from reporter import sector_etf

# compute_flows 는 섹터 ETF ~17종(KR/US 각각)의 일봉을 외부 조회해 ~1초 걸린다. 섹터 ETF 는
# PriceCandle 에 저장돼 있지 않고, flow 스코어는 일봉 기반이라 분 단위로 안 바뀐다. /analysis·
# /api/sectors/flow 가 매 요청 재조회하지 않도록 시장별로 프로세스 인메모리 TTL 캐시를 둔다.
_FLOW_TTL_S = 300.0  # 5분
_flow_cache: dict[str, tuple[float, list]] = {}
_flow_lock = threading.Lock()


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
    """섹터 ETF 기술 지표를 0~100 자금유입 스코어로. 규칙은 domain.analysis_scoring 에 위임.

    Technicals 객체에서 원시 지표를 뽑아 도메인 스코어러에 넘기는 얇은 어댑터.
    """
    return domain_scoring.flow_score(
        return_3m=tech.return_3m,
        near_high_pct=tech.near_high_pct,
        vol_ratio=tech.vol_ratio,
        foreign_delta=foreign_delta,
    )


# 외국인 보유율 변화는 순수 계산 그대로 도메인 함수를 재노출.
foreign_delta = domain_scoring.foreign_delta

# 지수 flow 도 섹터 flow 와 같은 5분 TTL 캐시(지수명 → (계산시각, score)).
_index_cache: dict[str, tuple[float, float | None]] = {}


def index_flow_score(index_name: str, session: requests.Session | None = None) -> float | None:
    """국내 지수(KOSPI|KOSDAQ)의 자금유입 스코어(0~100). 섹터 ETF 와 동일한 flow_score 규칙을
    지수 일봉(price_candles 의 stock_code=지수명)에 적용한다. 지수엔 외국인비율이 없어 foreign_delta
    는 None(가중치 0.10 자동 재정규화). 방향(bool)만 쓰던 탑다운 지수 항을 수급 점수로 승격."""
    now = time.monotonic()
    with _flow_lock:
        cached = _index_cache.get(index_name)
        if cached and now - cached[0] < _FLOW_TTL_S:
            return cached[1]

    from app.db.session import SessionLocal
    from app.services import candle_service

    db = SessionLocal()
    try:
        candles = candle_service.ensure_periodic(db, index_name, "day")
    finally:
        db.close()
    if not candles:
        return None
    score = flow_score(technicals.compute(candles), None)
    with _flow_lock:
        _index_cache[index_name] = (time.monotonic(), score)
    return score


def warm_cache() -> None:
    """섹터·지수 flow 캐시를 미리 채운다(startup·주기 호출용).

    compute_flows 는 첫 호출 시 섹터 ETF ~42종 봉(수만 행)을 cold Postgres 에서 읽어 수백ms~
    수초 걸린다. 이 비용을 유저 첫 요청(종목 analysis·screener topdown)이 물지 않도록 미리
    호출해 5분 TTL 캐시를 데운다. 각 함수가 자체 캐싱하므로 여기선 호출만 한다."""
    for market in ("KR", "US"):
        compute_flows(market)
    for index_name in ("KOSPI", "KOSDAQ"):
        index_flow_score(index_name)


def compute_flows(market: str, session: requests.Session | None = None) -> list[SectorFlow]:
    """시장(KR|US)의 모든 섹터 ETF flow 를 계산해 flow_score 내림차순으로 반환한다.

    5분 TTL 프로세스 캐시. 외부 조회(~1초)를 반복하지 않는다.
    """
    now = time.monotonic()
    with _flow_lock:
        cached = _flow_cache.get(market)
        if cached and now - cached[0] < _FLOW_TTL_S:
            return cached[1]

    flows = _compute_flows_uncached(market, session)
    if flows:  # 전량 실패(빈 결과)는 캐시하지 않고 다음 호출에서 재시도
        with _flow_lock:
            _flow_cache[market] = (time.monotonic(), flows)
    return flows


def _compute_flows_uncached(
    market: str, session: requests.Session | None = None
) -> list[SectorFlow]:
    etfs = sector_etf.KR_SECTOR_ETFS if market == "KR" else sector_etf.US_SECTOR_ETFS

    # DB 우선: 저장된 ETF 일봉을 쓰고, 없을 때만 외부 조회(candle_service 가 저장까지 함).
    # 지연 import(순환 방지: candle_service→adapters, 여기선 candle_service 만 필요).
    from app.db.session import SessionLocal
    from app.services import candle_service

    # ETF 전체를 세션 하나로 순회한다(ETF 당 세션 생성=커넥션 처닝 방지).
    flows: list[SectorFlow] = []
    db = SessionLocal()
    try:
        candle_lists = [
            candle_service.ensure_periodic(db, etf.symbol, "day", market=market) for etf in etfs
        ]
    finally:
        db.close()
    for etf, candles in zip(etfs, candle_lists, strict=True):
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
