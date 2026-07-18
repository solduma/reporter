"""커버리지 — 종목 리포트 + 종목 소속 산업 리포트를 합산·목록화한다."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    Report,
    ReportAnalysis,
    SectorTheme,
    SectorThemeStock,
    Sentiment,
)
from app.services import company_service


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            Report.__table__, ReportAnalysis.__table__,
            SectorTheme.__table__, SectorThemeStock.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _report(
    rid, category, *, stock_code=None, industry_name=None, sentiment=Sentiment.BUY,
    full_text=None, rationale="r",
):
    r = Report(
        id=rid, category=category, title=f"리포트{rid}", broker="X",
        published_date=date(2026, 7, 10), stock_code=stock_code, industry_name=industry_name,
    )
    a = ReportAnalysis(
        report_id=rid, summary="s", sentiment=sentiment, rationale=rationale, full_text=full_text
    )
    return r, a


def test_coverage_includes_own_and_mentioned_industry(db, monkeypatch):
    # 종목 리포트 + 소속 산업 리포트 중 **본문에 회사명 언급된 것만** 합산한다.
    monkeypatch.setattr(company_service, "theme_names", lambda db, code: ["반도체"])
    monkeypatch.setattr(
        company_service, "sector_report_industries", lambda db, code: ["반도체", "전기전자"]
    )
    monkeypatch.setattr(company_service, "resolve_stock_name", lambda db, code: "삼성전자")
    for r, a in [
        _report(1, "company", stock_code="005930"),
        _report(2, "industry", industry_name="반도체", full_text="삼성전자 HBM 호조"),  # 언급 O
        _report(3, "industry", industry_name="전기전자", rationale="삼성전자 부품"),      # rationale 언급 O
        _report(4, "industry", industry_name="반도체", full_text="SK하이닉스 위주"),      # 섹터 맞지만 언급 X → 제외
        _report(5, "industry", industry_name="자동차", full_text="삼성전자"),             # 다른 산업 → 제외
        _report(6, "company", stock_code="000660"),                                        # 다른 종목 → 제외
    ]:
        db.add(r)
        db.add(a)
    db.commit()

    since = date(2026, 1, 1)
    cnt, _buys = company_service.coverage_counts(db, "005930", since)
    assert cnt == 3  # 종목1 + 언급된 산업 2,3 (4는 섹터맞지만 미언급 제외)
    reports = company_service.coverage_reports(db, "005930", since)
    ids = {r.id for r, _ in reports}
    assert ids == {1, 2, 3}


def test_coverage_excludes_industry_without_name(db, monkeypatch):
    # 종목명 미상이면 산업 리포트는 넣지 않는다(오탐 방지).
    monkeypatch.setattr(company_service, "sector_report_industries", lambda db, code: ["반도체"])
    monkeypatch.setattr(company_service, "resolve_stock_name", lambda db, code: None)
    for r, a in [
        _report(1, "company", stock_code="005930"),
        _report(2, "industry", industry_name="반도체", full_text="삼성전자"),
    ]:
        db.add(r)
        db.add(a)
    db.commit()
    cnt, _ = company_service.coverage_counts(db, "005930", date(2026, 1, 1))
    assert cnt == 1  # 종목 리포트만(이름 없어 산업 제외)


def test_coverage_own_only_when_no_industries(db, monkeypatch):
    # 섹터 매핑 없으면(빈 리스트) 종목 리포트만 잡는다.
    monkeypatch.setattr(company_service, "sector_report_industries", lambda db, code: [])
    monkeypatch.setattr(company_service, "resolve_stock_name", lambda db, code: "삼성전자")
    for r, a in [
        _report(1, "company", stock_code="005930"),
        _report(2, "industry", industry_name="반도체", full_text="삼성전자"),
    ]:
        db.add(r)
        db.add(a)
    db.commit()
    cnt, _ = company_service.coverage_counts(db, "005930", date(2026, 1, 1))
    assert cnt == 1  # 종목 리포트만


def test_sector_report_industries_maps_via_sector_etf(db, monkeypatch):
    # judal 테마 → 섹터 → 산업명 후보(sector_etf 매핑).
    monkeypatch.setattr(company_service, "theme_names", lambda db, code: ["반도체"])
    industries = company_service.sector_report_industries(db, "005930")
    assert "반도체" in industries  # 반도체 섹터 → 반도체 산업 리포트
