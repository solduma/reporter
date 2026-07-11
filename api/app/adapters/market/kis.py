"""KIS(한국투자증권 OpenAPI) 국내 봉 조회 — 네이버 차트 실패 시 폴백.

OAuth 토큰은 발급이 분당 1회로 제한되고 24h 유효하므로 프로세스 인메모리에 캐시한다.
기간별 시세(inquire-daily-itemchartprice)는 output2 가 최신→과거 내림차순이고 일봉이
최대 100건이라, 뒤집어서 Candle(오름차순)로 돌려준다. 실패 시 빈 리스트.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import requests

from app.config import Settings
from app.domain.candle import Candle, resample_candles_30min

logger = logging.getLogger(__name__)

_BASE = "https://openapi.koreainvestment.com:9443"
_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
_TR_ID = "FHKST03010100"
# 일자별 분봉: 한 요청이 끝 시각(FID_INPUT_HOUR_1)부터 과거로 최대 120분(1분봉)을 준다.
_MIN_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
_MIN_TR_ID = "FHKST03010230"
# 정규장 09:00~15:30 을 끝 시각 기준으로 훑는 페이징 커서(각 120분 커버, 겹쳐도 dedup).
_MIN_HOURS = ["153000", "133000", "113000", "093000"]
_MIN_REQUEST_INTERVAL = 0.06  # 분봉 요청 간 간격(초). 초당 ~16건 이하로 rate limit 회피
# tf → KIS FID_PERIOD_DIV_CODE
_PERIOD = {"day": "D", "week": "W", "month": "M"}

# 토큰 프로세스 캐시: (access_token, 만료_monotonic). 발급 분당 1회 제한 → 만료 임박까지 재사용.
_token_cache: tuple[str, float] | None = None
_TOKEN_MARGIN = 600.0  # 만료 10분 전 갱신


def _access_token(settings: Settings, session: requests.Session) -> str | None:
    """OAuth 토큰을 발급/재사용한다. 실패 시 None."""
    global _token_cache
    if _token_cache and time.monotonic() < _token_cache[1]:
        return _token_cache[0]
    if not settings.kis_app_key or not settings.kis_app_secret:
        return None
    try:
        resp = session.post(
            f"{_BASE}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": settings.kis_app_key,
                "appsecret": settings.kis_app_secret,
            },
            headers={"content-type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("KIS token issue failed: %s", e)
        return None
    token = data.get("access_token")
    if not token:  # 분당 재발급 제한 시 빈 토큰이 온다 → 캐시하지 않는다
        logger.warning("KIS token empty (rate-limited?)")
        return None
    expires_in = int(data.get("expires_in", 86400))
    _token_cache = (token, time.monotonic() + expires_in - _TOKEN_MARGIN)
    return token


def _parse_output2(rows: list[dict]) -> list[Candle]:
    """KIS output2(내림차순) → Candle 오름차순. 거래량 0 당일 플레이스홀더 봉은 제외."""
    candles: list[Candle] = []
    for r in reversed(rows):  # 최신→과거 → 과거→최신
        try:
            vol = int(r["acml_vol"])
            if vol == 0:  # 장 시작 전 당일 미체결 봉(O=H=L=C, V=0) 제외
                continue
            candles.append(
                Candle(
                    ts=datetime.strptime(r["stck_bsop_date"], "%Y%m%d"),
                    open=float(r["stck_oprc"]),
                    high=float(r["stck_hgpr"]),
                    low=float(r["stck_lwpr"]),
                    close=float(r["stck_clpr"]),
                    volume=vol,
                    foreign_ratio=None,
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return candles


def fetch_periodic(
    settings: Settings,
    stock_code: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    session: requests.Session,
) -> list[Candle]:
    """KIS 로 국내 일/주/월봉을 조회한다(네이버 폴백용). 일봉은 최대 100건 캡.

    30분봉·해외는 지원하지 않는다(빈 리스트). 실패 시 빈 리스트.
    """
    period = _PERIOD.get(timeframe)
    if not period:
        return []
    token = _access_token(settings, session)
    if not token:
        return []
    try:
        resp = session.get(
            f"{_BASE}{_CHART_PATH}",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": period,
                "FID_ORG_ADJ_PRC": "0",  # 수정주가
            },
            headers={
                "authorization": f"Bearer {token}",
                "appkey": settings.kis_app_key,
                "appsecret": settings.kis_app_secret,
                "tr_id": _TR_ID,
                "custtype": "P",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("KIS chart fetch failed %s: %s", stock_code, e)
        return []
    if data.get("rt_cd") != "0":
        logger.warning("KIS chart rt_cd=%s msg=%s", data.get("rt_cd"), data.get("msg1"))
        return []
    return _parse_output2(data.get("output2") or [])


def _headers(settings: Settings, token: str, tr_id: str) -> dict:
    return {
        "authorization": f"Bearer {token}",
        "appkey": settings.kis_app_key,
        "appsecret": settings.kis_app_secret,
        "tr_id": tr_id,
        "custtype": "P",
    }


def _minute_bars_for_day(
    settings: Settings, token: str, stock_code: str, day: str, session: requests.Session
) -> list[Candle]:
    """특정 거래일(day=YYYYMMDD)의 1분봉을 끝시각 커서로 페이징해 모은다(dedup)."""
    by_ts: dict[datetime, Candle] = {}
    for hour in _MIN_HOURS:
        time.sleep(_MIN_REQUEST_INTERVAL)  # KIS 시세 rate limit(초당 ~20건) 완화
        try:
            resp = session.get(
                f"{_BASE}{_MIN_CHART_PATH}",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                    "FID_INPUT_DATE_1": day,
                    "FID_INPUT_HOUR_1": hour,
                    "FID_PW_DATA_INCU_YN": "N",
                    "FID_FAKE_TICK_INCU_YN": "N",
                },
                headers=_headers(settings, token, _MIN_TR_ID),
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning("KIS minute fetch failed %s %s@%s: %s", stock_code, day, hour, e)
            continue
        if data.get("rt_cd") != "0":
            continue
        for r in data.get("output2") or []:
            try:
                d, t = r["stck_bsop_date"], r["stck_cntg_hour"]
                vol = int(r.get("cntg_vol", 0))
                if d != day or vol == 0:
                    continue
                ts = datetime.strptime(d + t, "%Y%m%d%H%M%S")
                by_ts[ts] = Candle(
                    ts=ts,
                    open=float(r["stck_oprc"]),
                    high=float(r["stck_hgpr"]),
                    low=float(r["stck_lwpr"]),
                    close=float(r["stck_prpr"]),
                    volume=vol,
                )
            except (KeyError, ValueError, TypeError):
                continue
    return [by_ts[k] for k in sorted(by_ts)]


def fetch_intraday_30min(
    settings: Settings, stock_code: str, days: list[str], session: requests.Session | None = None
) -> list[Candle]:
    """KIS 로 여러 거래일(days=[YYYYMMDD,...])의 30분봉을 조회한다(네이버 분봉 백필용).

    거래일마다 1분봉을 페이징 수집 후 30분으로 리샘플해 이어붙인다. 실패 시 빈 리스트.
    """
    session = session or requests.Session()
    token = _access_token(settings, session)
    if not token:
        return []
    out: list[Candle] = []
    for day in days:
        minutes = _minute_bars_for_day(settings, token, stock_code, day, session)
        out.extend(resample_candles_30min(minutes))
    return out
