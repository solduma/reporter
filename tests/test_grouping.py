from reporter.grouping import group_by_entity
from reporter.models import Report


def _r(category, stock_name=None, industry=None, title="t"):
    return Report(category=category, title=title, broker="b", date="26.07.08", views=1,
                  stock_name=stock_name, industry=industry)


def test_company_grouped_by_stock_name():
    reports = [
        _r("company", stock_name="삼성전자", title="a"),
        _r("company", stock_name="삼성전자", title="b"),
        _r("company", stock_name="SK하이닉스", title="c"),
    ]
    groups = group_by_entity(reports)
    assert set(groups) == {"삼성전자", "SK하이닉스"}
    assert len(groups["삼성전자"]) == 2


def test_industry_grouped_by_industry_name():
    reports = [_r("industry", industry="반도체", title="a"), _r("industry", industry="반도체", title="b")]
    groups = group_by_entity(reports)
    assert list(groups) == ["반도체"]
    assert len(groups["반도체"]) == 2


def test_missing_key_falls_back_to_title():
    # 종목명/업종명이 없으면 제목으로 개별 그룹 유지(뭉뚱그려지지 않음)
    reports = [_r("company", stock_name=None, title="제목A"), _r("company", stock_name=None, title="제목B")]
    groups = group_by_entity(reports)
    assert set(groups) == {"제목A", "제목B"}
