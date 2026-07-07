from reporter.models import Report
from reporter.selector import select_top


def _report(category: str, broker: str, views: int) -> Report:
    return Report(category=category, title="t", broker=broker, date="26.07.07", views=views)


def test_major_broker_gets_bonus_over_higher_views():
    # Arrange: 비주요 증권사가 조회수는 최고지만, 주요 증권사가 보너스로 역전 가능
    reports = [
        _report("company", "듣보증권", 100),  # 100점 + 0 = 100
        _report("company", "삼성증권", 80),   # 80점 + 30 = 110
    ]
    # Act
    top = select_top(reports, top_n=1)
    # Assert
    assert top[0].broker == "삼성증권"


def test_scores_are_normalized_per_category():
    reports = [
        _report("company", "듣보A", 200),
        _report("industry", "듣보B", 50),
    ]
    select_top(reports, top_n=5)
    # 각 카테고리 내 최대 조회수가 100점이 되어야 한다
    assert reports[0].score == 100.0
    assert reports[1].score == 100.0


def test_top_n_limit_per_category():
    reports = [_report("company", f"b{i}", i * 10) for i in range(1, 9)]
    top = select_top(reports, top_n=3)
    assert len(top) == 3
    assert [r.views for r in top] == [80, 70, 60]
