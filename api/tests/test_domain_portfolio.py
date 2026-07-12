"""보유종목 손익·손절·섹터비중 순수 계산 단위 테스트."""

from __future__ import annotations

from app.domain import portfolio as p


def test_compute_holding_profit():
    c = p.compute_holding(shares=10, avg_cost=70000, current_price=77000, stop_loss=None)
    assert c.cost_basis == 700000
    assert c.market_value == 770000
    assert c.pnl == 70000
    assert c.pnl_pct == 10.0
    assert c.stop_status == "none"


def test_compute_holding_loss():
    c = p.compute_holding(shares=5, avg_cost=100000, current_price=90000, stop_loss=None)
    assert c.pnl == -50000
    assert c.pnl_pct == -10.0


def test_compute_holding_no_price():
    # 현재가 없으면 손익 None, 원가만 계산.
    c = p.compute_holding(shares=10, avg_cost=50000, current_price=None, stop_loss=45000)
    assert c.cost_basis == 500000
    assert c.market_value is None and c.pnl is None and c.pnl_pct is None
    assert c.stop_status == "ok"  # 현재가 모르면 판단 보류


def test_stop_status_hit_near_ok():
    # 손절선 50000: 현재가 49000=hit, 52000=near(5% 이내=52500), 60000=ok.
    assert p.compute_holding(1, 60000, 49000, 50000).stop_status == "hit"
    assert p.compute_holding(1, 60000, 52000, 50000).stop_status == "near"
    assert p.compute_holding(1, 60000, 60000, 50000).stop_status == "ok"


def test_summarize_partial_prices():
    calcs = [
        p.compute_holding(10, 70000, 77000, None),  # +70000, cost 700000
        p.compute_holding(5, 100000, None, None),  # 현재가 없음 → 손익 제외
        p.compute_holding(2, 50000, 45000, 46000),  # -10000, cost 100000, stop hit
    ]
    s = p.summarize(calcs)
    assert s.total_pnl == 60000  # 70000 - 10000 (가격 없는 것 제외)
    # 손익률 분모는 가격 있는 것의 원가만: 700000 + 100000 = 800000
    assert s.total_pnl_pct == round(60000 / 800000 * 100, 2)
    # 전체 원가 = 700000 + 500000(가격 없는 것) + 100000 = 1,300,000
    assert s.total_cost == 1300000
    assert s.stop_hit == 1 and s.stop_near == 0


def test_sector_weights():
    w = p.sector_weights([("반도체", 600), ("2차전지", 300), ("반도체", 100)])
    # 반도체 700/1000=70%, 2차전지 300/1000=30%, 내림차순.
    assert w == [("반도체", 70.0), ("2차전지", 30.0)]


def test_sector_weights_empty():
    assert p.sector_weights([("반도체", 0)]) == []
