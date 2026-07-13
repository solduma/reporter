"""섹터 매핑 단위 테스트 — themes_to_kr_sector 멱등성 회귀 방지.

종목 상세가 topdown.kr_sector(이미 폴딩된 확정 섹터명)를 섹터 차트에 다시 넘기면
키워드 부분일치가 확정명을 재폴딩해 엉뚱한 섹터로 가던 버그(필수소비재→에너지화학,
로봇→기계장비, 경기소비재→None) 회귀 방지.
"""

from __future__ import annotations

import pytest

from reporter import sector_etf


@pytest.mark.parametrize("sector", [e.sector for e in sector_etf.KR_SECTOR_ETFS])
def test_canonical_sector_is_idempotent(sector):
    # 확정 섹터명을 넣으면 그대로 나와야 한다(재폴딩 금지).
    assert sector_etf.themes_to_kr_sector([sector]) == sector


def test_raw_theme_names_still_fold():
    # 원시 judal 테마명은 기존대로 대표 섹터로 폴딩된다.
    assert sector_etf.themes_to_kr_sector(["반도체 장비"]) == "반도체 소부장"
    assert sector_etf.themes_to_kr_sector(["2차전지 소재"]) == "2차전지"
    assert sector_etf.themes_to_kr_sector(["해운"]) == "운송"


def test_unmatched_returns_none():
    assert sector_etf.themes_to_kr_sector(["존재하지않는테마"]) is None


def test_new_sectors_fold_and_map_to_us():
    # 전수 검수(#302)로 추가한 실질 섹터 — 국내 폴딩 + 미국 대응.
    cases = {
        "의료기기": ("의료기기", "의료기기"),
        "철강": ("철강", "철강"),
        "5G": ("통신", "커뮤니케이션"),
        "우주": ("방산우주", "방산우주"),
        "패션": ("경기소비재", "임의소비재"),
        "비만치료제(위고비)": ("바이오", "바이오"),
        "사이버 보안": ("IT", "기술"),
        "원자력발전(SMR)": ("에너지화학", "에너지"),
    }
    for theme, (kr, us) in cases.items():
        got_kr = sector_etf.themes_to_kr_sector([theme])
        assert got_kr == kr, f"{theme} -> {got_kr} (기대 {kr})"
        assert sector_etf.kr_sector_to_us(got_kr) == us


def test_medical_device_precedes_bio():
    # 의료기기 테마는 바이오(제약)가 아니라 의료기기로 접혀야 한다(우선순위).
    assert sector_etf.themes_to_kr_sector(["의료기기", "제약"]) == "의료기기"


def test_new_kr_etfs_have_us_counterpart():
    # 새 국내 섹터 ETF 는 모두 미국 대응(kr_sector_to_us)이 있어야 한다.
    for e in sector_etf.KR_SECTOR_ETFS:
        assert sector_etf.kr_sector_to_us(e.sector) is not None, e.sector


def test_code_override_beats_themes():
    # 코드 오버라이드가 테마 매핑을 이긴다(솔본: 테마=지주사뿐이라 override 로 의료기기).
    assert sector_etf.stock_kr_sector("035610", ["지주사"]) == "의료기기"
    # 오버라이드 없는 코드는 테마 키워드로 폴딩.
    assert sector_etf.stock_kr_sector("000000", ["철강"]) == "철강"
    # 코드도 테마도 없으면 None.
    assert sector_etf.stock_kr_sector(None, ["지주사"]) is None


def test_noise_themes_stay_unmatched():
    # 지주사·스팩·계절·인물 등 노이즈 테마는 섹터로 접히지 않는다.
    for noise in ["지주사", "스팩", "고배당", "MSCI Korea", "밸류업"]:
        assert sector_etf.themes_to_kr_sector([noise]) is None
