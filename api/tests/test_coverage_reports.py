"""커버리지 — 종목 리포트 + 종목이 본문 언급된 산업 리포트를 합산·목록화한다.

산업 리포트는 industry_name(섹터 분류, '기타' 사각지대 있음) 대신 본문 언급으로 판정한다:
종목코드(6자리) 언급 OR (종목명 단어경계 매칭 & 혼동명 부재). 혼동명(다른 회사)이 임계 이상인
재벌 약칭(SK·LG)은 이름 매칭을 끄고 코드에만 의존한다.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    Base,
    Report,
    ReportAnalysis,
    Sentiment,
    UniverseSnapshot,
)
from app.services import company_service


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[Report.__table__, ReportAnalysis.__table__, UniverseSnapshot.__table__],
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


def _universe(db, *names):
    # 혼동명 사전 소스 — (code, name) 등록. code 는 임의.
    for i, nm in enumerate(names):
        db.add(UniverseSnapshot(
            stock_code=f"{900000 + i}", stock_name=nm, market="KOSPI",
            snapshot_date=date(2026, 7, 1),
        ))


def test_coverage_own_plus_name_mention_for_unique_name(db, monkeypatch):
    # 고유명(혼동명 0): 종목 리포트 + 종목명 단어경계 언급된 산업 리포트. industry_name 무관.
    monkeypatch.setattr(company_service, "resolve_stock_name", lambda db, code: "케이아이엔엑스")
    _universe(db, "케이아이엔엑스")
    for r, a in [
        _report(1, "company", stock_code="093320"),
        _report(2, "industry", industry_name="기타", full_text="케이아이엔엑스 IDC 성장"),  # '기타'여도 언급 O
        _report(3, "industry", industry_name="IT", rationale="케이아이엔엑스 목표가"),        # rationale 언급 O
        _report(4, "industry", industry_name="IT", full_text="다른 회사 얘기"),               # 미언급 → 제외
        _report(5, "company", stock_code="000660"),                                            # 다른 종목 → 제외
    ]:
        db.add(r)
        db.add(a)
    db.commit()

    cnt, _buys = company_service.coverage_counts(db, "093320", date(2026, 1, 1))
    assert cnt == 3  # 종목1 + 언급 산업 2('기타'!),3
    ids = {r.id for r, _ in company_service.coverage_reports(db, "093320", date(2026, 1, 1))}
    assert ids == {1, 2, 3}


def test_coverage_matches_by_stock_code_in_body(db, monkeypatch):
    # 종목코드(6자리) 본문 언급도 커버(오탐 0 신뢰 소스).
    monkeypatch.setattr(company_service, "resolve_stock_name", lambda db, code: "케이아이엔엑스")
    _universe(db, "케이아이엔엑스")
    for r, a in [
        _report(1, "industry", industry_name="IT", full_text="종목코드 093320 참고"),  # 코드 언급 O
    ]:
        db.add(r)
        db.add(a)
    db.commit()
    cnt, _ = company_service.coverage_counts(db, "093320", date(2026, 1, 1))
    assert cnt == 1


def test_abbreviation_name_match_disabled_uses_code_only(db, monkeypatch):
    # 재벌 약칭(혼동명 임계 이상): 이름 매칭 끔 → 부분매칭 오탐 차단, 코드 언급만 커버.
    monkeypatch.setattr(company_service, "resolve_stock_name", lambda db, code: "SK")
    # 'SK' 를 포함하는 다른 회사 4개 이상 → 약칭 판정
    _universe(db, "SK", "SK하이닉스", "SK이노베이션", "SK텔레콤", "SK증권", "SKC")
    for r, a in [
        _report(1, "industry", industry_name="IT", full_text="SK하이닉스 HBM 호조"),  # 부분매칭 오탐 → 제외
        _report(2, "industry", industry_name="IT", full_text="SK 지주 종목코드 034730"),  # 코드 언급 → 커버
    ]:
        db.add(r)
        db.add(a)
    db.commit()
    cnt, _ = company_service.coverage_counts(db, "034730", date(2026, 1, 1))
    assert cnt == 1  # 코드 언급 1건만(SK하이닉스 부분매칭 오탐 제외)


def test_unique_name_not_matched_as_substring(db, monkeypatch):
    # 고유명이라도 단어경계 매칭 — 다른 단어의 일부로는 안 잡힘.
    monkeypatch.setattr(company_service, "resolve_stock_name", lambda db, code: "동서")
    _universe(db, "동서", "아이에스동서")  # 혼동명 1개(임계 미만) → 이름매칭 켬
    for r, a in [
        _report(1, "industry", industry_name="음식료", full_text="동서 커피 점유율"),   # 단독 언급 O
        _report(2, "industry", industry_name="건설", full_text="아이에스동서 실적"),      # 부분문자열만 → 제외
    ]:
        db.add(r)
        db.add(a)
    db.commit()
    cnt, _ = company_service.coverage_counts(db, "026960", date(2026, 1, 1))
    assert cnt == 1  # '동서' 단독 1건(아이에스동서는 혼동명 제거로 제외)


def test_coverage_excludes_industry_without_name(db, monkeypatch):
    # 종목명 미상이면 산업 리포트는 코드 언급만(이름 매칭 불가).
    monkeypatch.setattr(company_service, "resolve_stock_name", lambda db, code: None)
    for r, a in [
        _report(1, "company", stock_code="005930"),
        _report(2, "industry", industry_name="반도체", full_text="삼성전자"),  # 이름만 → 제외(name None)
    ]:
        db.add(r)
        db.add(a)
    db.commit()
    cnt, _ = company_service.coverage_counts(db, "005930", date(2026, 1, 1))
    assert cnt == 1  # 종목 리포트만


def test_confusable_excludes_own_derivatives(db):
    # 우선주·ETF 파생은 혼동명에서 제외 — 파생만 있는 고유명(삼성전자)은 혼동명 0 으로 이름매칭 켬.
    _universe(db, "삼성전자", "삼성전자우", "KODEX 삼성전자채권혼합", "TIGER 삼성전자단일종목레버리지")
    conf = company_service._confusable_names(db, "삼성전자")
    assert conf == []  # 파생만 → 혼동명 없음(고유명)
