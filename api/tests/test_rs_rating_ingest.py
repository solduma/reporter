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
