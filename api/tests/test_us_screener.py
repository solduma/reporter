"""US 스크리너 서비스 단위 테스트 — us_universe 픽스처로 필터·정렬·스코어 검증(네트워크 없음)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, UsDisclosure, UsUniverse
from app.services import us_screener_service as scr

_AS_OF = date(2026, 7, 12)


@pytest.fixture
def db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng, tables=[UsUniverse.__table__, UsDisclosure.__table__])
    s = sessionmaker(bind=eng)()
    # 3종목: 저PER(JPM)·고PER(NVDA)·중간(XOM)
    s.add_all([
        UsUniverse(snapshot_date=_AS_OF, ticker="JPM", naver_symbol="JPM", name="JPMorgan",
                   exchange="NYSE", sector="Financials", close_price=336.0, change_pct=1.0,
                   market_cap=902e9, trading_value=2.1e9, per=15.9, pbr=2.5, eps=21.0,
                   high_52w=340.0, low_52w=200.0, momentum_3m=20.0),
        UsUniverse(snapshot_date=_AS_OF, ticker="NVDA", naver_symbol="NVDA.O", name="NVIDIA",
                   exchange="NASDAQ", sector="IT", close_price=210.0, change_pct=4.0,
                   market_cap=5105e9, trading_value=31e9, per=32.3, pbr=26.1, eps=6.5,
                   high_52w=236.0, low_52w=161.0, momentum_3m=5.0),
        UsUniverse(snapshot_date=_AS_OF, ticker="XOM", naver_symbol="XOM", name="Exxon",
                   exchange="NYSE", sector="Energy", close_price=120.0, change_pct=-0.5,
                   market_cap=576e9, trading_value=1.5e9, per=22.4, pbr=2.0, eps=5.4,
                   high_52w=130.0, low_52w=95.0, momentum_3m=-2.0),
    ])
    s.commit()
    yield s
    s.close()


def test_score_ranks_cheap_high_momentum_first(db):
    r = scr.screen(db, sort="score")
    assert r.total == 3
    # JPM: 저PER·저PBR·고모멘텀·신고가근접 → 최상위.
    assert r.items[0].ticker == "JPM"
    assert r.items[0].score > r.items[-1].score


def test_per_max_filter(db):
    r = scr.screen(db, per_max=20)
    assert {i.ticker for i in r.items} == {"JPM"}  # PER 15.9 만 통과


def test_mktcap_and_exchange_filter(db):
    r = scr.screen(db, mktcap_min=1000e9)  # 1조 이상 → NVDA(5.1T) 만
    assert {i.ticker for i in r.items} == {"NVDA"}
    r2 = scr.screen(db, exchange="NYSE")
    assert {i.ticker for i in r2.items} == {"JPM", "XOM"}


def test_sort_by_market_cap(db):
    r = scr.screen(db, sort="market_cap")
    assert [i.ticker for i in r.items] == ["NVDA", "JPM", "XOM"]  # 시총 내림차순


def test_near_high_pct_computed(db):
    r = scr.screen(db, sort="market_cap")
    nvda = next(i for i in r.items if i.ticker == "NVDA")
    assert nvda.near_high_pct == round(210.0 / 236.0 * 100, 1)


def test_has_event_filter(db):
    # 8-K 없으면 has_event=True 는 빈 결과.
    assert scr.screen(db, has_event=True).total == 0
    db.add(UsDisclosure(ticker="JPM", cik="0000019617", accession="0000019617-26-000001",
                        form_type="8-K", filing_date=_AS_OF, primary_doc_url="http://x"))
    db.commit()
    r = scr.screen(db, has_event=True)
    assert {i.ticker for i in r.items} == {"JPM"}
    assert next(i for i in r.items if i.ticker == "JPM").has_recent_8k is True
