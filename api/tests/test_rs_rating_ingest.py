"""RS Rating 배치 서비스 테스트 — DB read/update 를 스텁해 계산·적재 흐름을 검증한다."""

from __future__ import annotations

from datetime import date

from app.services import rs_rating_ingest


def test_run_rs_rating_batch_rates_and_updates(monkeypatch):
    # 3종목: 상승·평탄·하락 → rating 이 상승 > 평탄 > 하락 순. OHLCV 봉으로 RS·추세 함께 계산.
    series = {
        "UP": [100.0 * (1.003**i) for i in range(300)],
        "FLAT": [100.0] * 300,
        "DOWN": [300.0 * (0.997**i) for i in range(300)],
    }

    def _bars(db, code):
        return [
            rs_rating_ingest._Bar(close=c, high=c * 1.01, low=c * 0.99, volume=1000)
            for c in series[code]
        ]

    monkeypatch.setattr(
        rs_rating_ingest.universe_ingest, "latest_snapshot_date", lambda db: date(2026, 7, 10)
    )
    monkeypatch.setattr(rs_rating_ingest, "_universe_codes", lambda db, d: list(series))
    monkeypatch.setattr(rs_rating_ingest, "_recent_bars", _bars)

    updates: dict[str, int] = {}

    class _FakeDB:
        def execute(self, stmt):
            # UPDATE ... .values(rs_rating=N).where(stock_code==code) 에서 값·코드 추출.
            compiled = stmt.compile()
            params = compiled.params
            # where 절의 stock_code 는 bindparam 이름이 달라 values 의 rating 만 확인용으로 모은다.
            updates[len(updates)] = params
            return None

        def commit(self):
            pass

    result = rs_rating_ingest.run_rs_rating_batch(_FakeDB())
    assert result["rated"] == 3
    assert result["total"] == 3
    # 3건 UPDATE 실행됨.
    assert len(updates) == 3


def test_run_rs_rating_batch_no_snapshot(monkeypatch):
    monkeypatch.setattr(
        rs_rating_ingest.universe_ingest, "latest_snapshot_date", lambda db: None
    )
    result = rs_rating_ingest.run_rs_rating_batch(db=None)
    assert result == {"rated": 0, "total": 0}


def test_momentum_3m_from_closes():
    # +5% over the 63-bar window(오늘 종가 / 63거래일 전 종가 - 1).
    closes = [100.0] * 63 + [105.0]
    assert rs_rating_ingest._momentum_3m(closes) == 5.0
    # 데이터 부족 시 None.
    assert rs_rating_ingest._momentum_3m([100.0] * 10) is None


def test_run_rs_rating_batch_folds_momentum_when_requested(monkeypatch):
    # 장중 사이클 경로: with_momentum=True 면 UPDATE values 에 momentum_3m 이 포함된다.
    closes = [100.0 * (1.003**i) for i in range(300)]
    monkeypatch.setattr(
        rs_rating_ingest.universe_ingest, "latest_snapshot_date", lambda db: date(2026, 7, 14)
    )
    monkeypatch.setattr(rs_rating_ingest, "_universe_codes", lambda db, d: ["UP"])
    monkeypatch.setattr(
        rs_rating_ingest, "_recent_bars",
        lambda db, code: [
            rs_rating_ingest._Bar(close=c, high=c * 1.01, low=c * 0.99, volume=1000) for c in closes
        ],
    )
    captured: list[dict] = []

    class _FakeDB:
        def execute(self, stmt):
            captured.append(stmt.compile().params)

        def commit(self):
            pass

    rs_rating_ingest.run_rs_rating_batch(_FakeDB(), with_momentum=True)
    assert captured, "UPDATE 가 실행되어야 한다"
    assert any("momentum_3m" in p for p in captured)  # 모멘텀이 폴딩됨

    # 기본(야간)에는 momentum 을 건드리지 않는다(growth_ingest 소유).
    captured.clear()
    rs_rating_ingest.run_rs_rating_batch(_FakeDB())
    assert captured
    assert all("momentum_3m" not in p for p in captured)
