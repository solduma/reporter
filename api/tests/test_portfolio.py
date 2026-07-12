"""보유종목(포트폴리오) 서비스 단위 테스트 — fake HoldingRepository 주입(DB·네트워크 무접속).

포트 치환성(seam _repo 교체)으로 응용 로직이 SQLAlchemy 없이 도는 것을 실증한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services import portfolio


@dataclass
class _Row:
    """Holding ORM 행을 흉내내는 최소 구조(_to_out 이 읽는 필드만)."""

    stock_code: str
    shares: float
    avg_cost: float
    stop_loss: float | None = None
    note: str | None = None
    updated_at: object | None = None


class _FakeHoldingRepo:
    """HoldingRepository 포트를 만족하는 인메모리 fake."""

    def __init__(self):
        self._rows: dict[str, _Row] = {}

    def list_all(self):
        return [self._rows[k] for k in sorted(self._rows)]

    def get(self, stock_code):
        return self._rows.get(stock_code)

    def upsert(self, item):
        self._rows[item.stock_code] = _Row(
            stock_code=item.stock_code,
            shares=item.shares,
            avg_cost=item.avg_cost,
            stop_loss=item.stop_loss,
            note=item.note,
        )
        return self._rows[item.stock_code]

    def delete(self, stock_code):
        return self._rows.pop(stock_code, None) is not None


def _wire(monkeypatch):
    repo = _FakeHoldingRepo()
    monkeypatch.setattr(portfolio, "_repo", lambda db: repo)
    # 종목명·현재가 조회는 별개 관심사 — 스텁으로 고정(현재가 None → 손익 계산 생략, CRUD 만 검증).
    monkeypatch.setattr(portfolio.company_service, "resolve_stock_name", lambda db, code: f"종목{code}")
    monkeypatch.setattr(portfolio.company_service, "latest_snapshot", lambda db, code: None)
    return repo


def test_save_and_list(monkeypatch):
    _wire(monkeypatch)
    out = portfolio.save_holding(
        object(), portfolio.HoldingInput(stock_code="005930", shares=10, avg_cost=70000, stop_loss=65000)
    )
    assert out.stock_code == "005930"
    assert out.stock_name == "종목005930"  # 서비스가 종목명 조립
    assert out.shares == 10 and out.avg_cost == 70000 and out.stop_loss == 65000

    listed = portfolio.list_holdings(object())
    assert [h.stock_code for h in listed] == ["005930"]


def test_upsert_overwrites(monkeypatch):
    _wire(monkeypatch)
    portfolio.save_holding(object(), portfolio.HoldingInput("000660", 5, 100000))
    portfolio.save_holding(object(), portfolio.HoldingInput("000660", 8, 120000))
    listed = portfolio.list_holdings(object())
    assert len(listed) == 1  # 종목당 1행
    assert listed[0].shares == 8 and listed[0].avg_cost == 120000


def test_delete(monkeypatch):
    _wire(monkeypatch)
    portfolio.save_holding(object(), portfolio.HoldingInput("035720", 3, 50000))
    assert portfolio.delete_holding(object(), "035720") is True
    assert portfolio.delete_holding(object(), "035720") is False  # 이미 없음
    assert portfolio.list_holdings(object()) == []


def test_list_sorted_by_code(monkeypatch):
    _wire(monkeypatch)
    for code in ("035720", "005930", "000660"):
        portfolio.save_holding(object(), portfolio.HoldingInput(code, 1, 1000))
    assert [h.stock_code for h in portfolio.list_holdings(object())] == ["000660", "005930", "035720"]
