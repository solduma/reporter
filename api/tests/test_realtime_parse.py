"""KIS 실시간 체결(H0STCNT0) 프레임 파싱 단위 테스트 — 실 연결 미사용."""

from __future__ import annotations

from app.config import Settings
from app.services.realtime import (
    _MAX_SUBSCRIPTIONS,
    RealtimeManager,
    _sign_to_rising,
    is_data_frame,
    parse_ticks,
)

# H0STCNT0 필드(^): 코드^시각^현재가^전일대비부호^전일대비^등락률^가중평균^시가^고가^저가
#                    ^매도호가1^매수호가1^체결거래량(12)^누적거래량(13) ...
# 배지에는 누적거래량(인덱스 13)을 쓴다.
_REC = "005930^093015^71800^2^300^0.42^71650^71500^72000^71400^71900^71800^120^3456789"


def test_is_data_frame():
    assert is_data_frame("0|H0STCNT0|001|" + _REC)
    assert is_data_frame("1|H0STCNT0|001|" + _REC)  # 암호화 플래그 1 도 데이터
    assert not is_data_frame('{"header":{"tr_id":"PINGPONG"}}')
    assert not is_data_frame("")


def test_parse_single_record():
    ticks = parse_ticks("0|H0STCNT0|001|" + _REC)
    assert len(ticks) == 1
    t = ticks[0]
    assert t.code == "005930"
    assert t.price == 71800
    assert t.rising is True  # 부호 2 = 상승
    assert t.change == 300.0
    assert t.change_ratio == 0.42
    assert t.volume == 3456789  # 누적거래량(인덱스 13)
    assert t.ts == "093015"


def test_parse_multi_record_takes_latest():
    older = "005930^093015^71800^2^300^0.42^71650^71500^72000^71400^71900^71800^120^3456789"
    newer = "005930^093045^71900^2^400^0.56^71660^71500^72000^71400^71950^71900^80^3456869"
    ticks = parse_ticks("0|H0STCNT0|002|" + older + "^" + newer)
    assert len(ticks) == 1
    assert ticks[0].price == 71900  # 최신 레코드
    assert ticks[0].ts == "093045"
    assert ticks[0].volume == 3456869


def test_parse_wrong_tr_id_ignored():
    assert parse_ticks("0|H0STASP0|001|" + _REC) == []


def test_parse_malformed_returns_empty():
    assert parse_ticks("0|H0STCNT0|abc|" + _REC) == []
    assert parse_ticks("0|H0STCNT0|001|too^few^fields") == []
    assert parse_ticks("garbage") == []


def test_sign_mapping():
    assert _sign_to_rising("1") is True  # 상한
    assert _sign_to_rising("2") is True  # 상승
    assert _sign_to_rising("3") is None  # 보합
    assert _sign_to_rising("4") is False  # 하한
    assert _sign_to_rising("5") is False  # 하락
    assert _sign_to_rising("") is None


def _mgr() -> RealtimeManager:
    # KIS 키가 있는 것으로 간주(네트워크는 안 씀 — acquire/release 는 동기 상태만 변경).
    return RealtimeManager(Settings(kis_app_key="k", kis_app_secret="s"))


def test_acquire_release_refcount():
    m = _mgr()
    assert m.acquire("005930") is True
    assert m.acquire("005930") is True  # 같은 종목 → refcount 2
    assert m._desired["005930"] == 2
    m.release("005930")
    assert m._desired["005930"] == 1  # 아직 구독 유지
    m.release("005930")
    assert "005930" not in m._desired  # 마지막 해제


def test_acquire_disabled_returns_false():
    m = RealtimeManager(Settings(kis_app_key="", kis_app_secret=""))
    assert m.acquire("005930") is False


def test_acquire_over_limit_rejected():
    m = _mgr()
    for i in range(_MAX_SUBSCRIPTIONS):
        assert m.acquire(f"{i:06d}") is True
    assert m.acquire("999999") is False  # 한도 초과
    # 기존 종목 추가 acquire 는 새 슬롯이 아니므로 허용.
    assert m.acquire("000000") is True


def test_release_unknown_is_noop():
    m = _mgr()
    m.release("005930")  # 예외 없이 무시
    assert not m._desired
