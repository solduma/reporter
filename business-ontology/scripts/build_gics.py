#!/usr/bin/env python3
"""GICS 2023 구조(11 sector / 25 group / 69 industry / 163 sub-industry) → industries.yaml 생성.

데이터는 공개 GICS 구조(MSCI/S&P, 2023 edition)를 기준으로 작성된 시드다.
정확한 8자리 sub-industry 코드는 MSCI GICS 구조를 따른다. 산업 분류 체계가
개정되면 이 임베디드 데이터를 갱신하고 재실행한다.

실행: python3 business-ontology/scripts/build_gics.py
출력: business-ontology/ontology/industries.yaml
"""

from __future__ import annotations

from pathlib import Path

import yaml

# 노드 형식: (코드, 한국어, 영어, 자식목록_또는_별칭목록)
#   sector(2자리) / group(4자리) / industry(6자리) / sub-industry(8자리, 잎)
#   잎(sub-industry)의 네번째 원소는 문자열 볕칭 목록이다.
_GICS = [
    # 10 Energy
    ("10", "에너지", "Energy", [
        ("1010", "에너지 장비 및 서비스", "Energy Equipment & Services", [
            ("101010", "에너지 장비 및 서비스", "Energy Equipment & Services", [
                ("10101010", "유전용 장비·서비스", "Oil & Gas Drilling", ["유정시추"]),
                ("10101020", "유전·가스 장비", "Oil & Gas Equipment", []),
            ]),
        ]),
        ("1020", "석유·가스·소비연료", "Oil, Gas & Consumable Fuels", [
            ("102050", "석유·가스·소비연료", "Oil, Gas & Consumable Fuels", [
                ("10205010", "통합 석유가스", "Integrated Oil & Gas", ["종합석유"]),
                ("10205020", "석유·가스 탐사·생산", "Oil & Gas Exploration & Production", ["E&P", "탐사생산"]),
                ("10205030", "석유·가스 정제·마케팅", "Oil & Gas Refining & Marketing", ["정제"]),
                ("10205040", "석유·가스 저장·운송", "Oil & Gas Storage & Transportation", ["파이프라인"]),
                ("10205050", "석탄·소비연료", "Coal & Consumable Fuels", ["석탄"]),
            ]),
        ]),
    ]),
    # 15 Materials
    ("15", "소재", "Materials", [
        ("1510", "소재", "Materials", [
            ("151010", "화학", "Chemicals", [
                ("15101010", "다목적 화학", "Commodity Chemicals", ["기초화학", "벌크화학"]),
                ("15101020", "특수 화학", "Specialty Chemicals", ["정밀화학"]),
                ("15101030", "비료·농약", "Fertilizers & Agricultural Chemicals", ["비료", "농약"]),
                ("15101040", "산업용 가스", "Industrial Gases", []),
            ]),
            ("151020", "건축 자재", "Construction Materials", [
                ("15102010", "건축 자재", "Construction Materials", ["건축자재", "시멘트"]),
            ]),
            ("151030", "용기·포장", "Containers & Packaging", [
                ("15103010", "금속·유리·플라스틱 용기", "Metal, Glass & Plastic Containers", ["용기"]),
                ("15103020", "종이·플라스틱 포장", "Paper & Plastic Packaging", ["포장"]),
            ]),
            ("151040", "금속·채광", "Metals & Mining", [
                ("15104010", "알루미늄", "Aluminum", ["알루미늄"]),
                ("15104020", "다목적 금속", "Diversified Metals", ["비철금속"]),
                ("15104025", "구리", "Copper", ["동"]),
                ("15104030", "금", "Gold", ["금"]),
                ("15104040", "귀금속·광물", "Precious Metals & Minerals", ["귀금속"]),
                ("15104045", "은", "Silver", ["은"]),
                ("15104050", "철강", "Steel", ["철강"]),
            ]),
            ("151050", "제지·임산물", "Paper & Forest Products", [
                ("15105010", "임산물", "Forest Products", ["임산물"]),
                ("15105020", "제지", "Paper Products", ["제지"]),
            ]),
        ]),
    ]),
    # 20 Industrials — 단일 그룹 2010 Capital Goods 아래에 모든 industry 가 포함된다.
    ("20", "산업재", "Industrials", [
        ("2010", "자본재", "Capital Goods", [
            ("201010", "항공·국방", "Aerospace & Defense", [
                ("20101010", "항공·국방", "Aerospace & Defense", ["항공국방"]),
            ]),
            ("201040", "건축·엔지니어링", "Building Products", [
                ("20104010", "건축 제품", "Building Products", ["건축제품"]),
                ("20104020", "건축·엔지니어링 서비스", "Construction & Engineering", ["건설"]),
                ("20104030", "주택 건설", "Homebuilders", ["주택건설"]),
            ]),
            ("201050", "전기 장비", "Electrical Components & Equipment", [
                ("20105010", "전기 부품·장비", "Electrical Components & Equipment", ["전기장비"]),
                ("20105020", "중전기 기기", "Heavy Electrical Equipment", ["중전기"]),
            ]),
            ("201060", "산업 기계·공급·운송", "Industrial Machinery & Supplies & Components", [
                ("20106010", "건설 기계·중장비", "Construction Machinery & Heavy Trucks", ["건설기계"]),
                ("20106015", "농업·산업 기계", "Agricultural & Farm Machinery", ["농기계"]),
                ("20106020", "산업 기계·부품", "Industrial Machinery & Supplies & Components", ["산업기계"]),
                ("20106030", "다목적 산업용 기계", "Diversified Industrial", []),
            ]),
            ("201070", "상업·전문 서비스", "Commercial & Professional Services", [
                ("20107010", "상업 인쇄·서비스", "Commercial Printing", ["인쇄"]),
                ("20107020", "환경·시설 서비스", "Environmental & Facilities Services", ["환경서비스"]),
                ("20107030", "사무서비스·외주", "Office Services & Supplies", []),
            ]),
            ("201080", "운송", "Transportation", [
                ("20108010", "항공 화물·물류", "Air Freight & Logistics", ["항공화물"]),
                ("20108020", "여객 항공", "Passenger Airlines", ["항공사"]),
                ("20108030", "해운", "Marine Transportation", ["해운"]),
                ("20108040", "철도", "Ground Transportation", ["철도"]),
                ("20108050", "물류 인프라", "Transportation Infrastructure", ["항만", "공항"]),
                ("20108060", "트럭 운송", "Trucking", ["화물운송"]),
            ]),
        ]),
    ]),
    # 25 Consumer Discretionary
    ("25", "임의소비재", "Consumer Discretionary", [
        ("2510", "자동차·부품", "Automobiles & Components", [
            ("251010", "자동차 부품", "Auto Components", [
                ("25101010", "자동차 부품·장비", "Auto Parts & Equipment", ["자동차부품"]),
                ("25101020", "타이어·고무", "Tires & Rubber", ["타이어"]),
            ]),
            ("251020", "자동차", "Automobiles", [
                ("25102010", "자동차 제조", "Automobile Manufacturers", ["완성차"]),
                ("25102020", "오토바이 제조", "Motorcycle Manufacturers", ["오토바이"]),
            ]),
        ]),
        ("2520", "가전제품", "Household Durables", [
            ("252010", "가전제품", "Household Appliances", [
                ("25201010", "가전제품", "Household Appliances", ["가전"]),
            ]),
            ("252020", "가구", "Housewares & Specialties", [
                ("25202010", "가구", "Home Furnishings", ["가구"]),
                ("25202020", "주방용품", "Home Improvement Retail", []),
            ]),
            ("252030", "레저용품", "Leisure Products", [
                ("25203010", "레저용품", "Leisure Products", ["레저용품"]),
            ]),
        ]),
        ("2530", "의류·호텔·레저", "Consumer Services", [
            ("253010", "호텔·리조트·크루즈", "Hotels, Resorts & Cruise Lines", [
                ("25301010", "호텔·리조트·크루즈", "Hotels, Resorts & Cruise Lines", ["호텔"]),
            ]),
            ("253020", "레저 시설·서비스", "Restaurants & Leisure Facilities", [
                ("25302010", "레저 시설", "Specialized Consumer Services", []),
                ("25302020", "레스토랑", "Restaurants", ["외식"]),
            ]),
        ]),
        ("2540", "미디어", "Media", [
            ("254010", "광고", "Advertising", [
                ("25401010", "광고", "Advertising", ["광고"]),
            ]),
            ("254020", "방송·출판", "Broadcasting & Publishing", [
                ("25402010", "방송", "Broadcasting", ["방송"]),
                ("25402020", "출판", "Publishing", ["출판"]),
            ]),
            ("254030", "영화·엔터테인먼트", "Movies & Entertainment", [
                ("25403010", "영화·엔터테인먼트", "Movies & Entertainment", ["엔터테인먼트"]),
            ]),
            ("254040", "인터랙티브 미디어·서비스", "Interactive Media & Services", [
                ("25404010", "인터랙티브 미디어·서비스", "Interactive Media & Services", ["인터넷서비스", "포털"]),
            ]),
        ]),
        ("2550", "소매", "Retail", [
            ("255010", "유통·소매", "Distribution & Retail", [
                ("25501010", "의류 소매", "Apparel Retail", ["의류소매"]),
                ("25501020", "다목적 소매", "Multiline Retail", ["백화점"]),
                ("25501030", "전자상거래", "Internet & Direct Marketing Retail", ["이커머스"]),
            ]),
            ("255020", "전문 소매", "Specialty Retail", [
                ("25502010", "컴퓨터·전자 소매", "Computer & Electronics Retail", ["전자소매"]),
                ("25502020", "홈임프루브먼트 소매", "Home Improvement Retail", ["건설소매"]),
                ("25502030", "전문 소매", "Specialty Stores", ["전문소매"]),
                ("25502040", "자동차 소매", "Automotive Retail", []),
            ]),
        ]),
    ]),
    # 30 Consumer Staples
    ("30", "필수소비재", "Consumer Staples", [
        ("3010", "식품·약품 소매", "Food & Staples Retailing", [
            ("301010", "식품·약품 소매", "Food & Staples Retailing", [
                ("30101010", "식료품 소매", "Food Retail", ["식료품소매"]),
                ("30101020", "식품·약품 소매", "Food & Staples Retailing", ["대형마트"]),
            ]),
        ]),
        ("3020", "식품·음료·담배", "Food, Beverage & Tobacco", [
            ("302010", "식품", "Food Products", [
                ("30201010", "가공 식품", "Packaged Foods & Meats", ["식품"]),
                ("30201020", "유제품", "Dairy", ["유제품"]),
            ]),
            ("302020", "음료", "Beverages", [
                ("30202010", "주류", "Brewers", ["맥주"]),
                ("30202015", "증류주·와인", "Distillers & Vintners", ["주류"]),
                ("30202020", "비알콜 음료", "Non-Alcoholic Beverages", ["음료"]),
            ]),
            ("302030", "담배", "Tobacco", [
                ("30203010", "담배", "Tobacco", ["담배"]),
            ]),
        ]),
        ("3030", "가정·개인용품", "Household & Personal Products", [
            ("303010", "가정용품", "Household Products", [
                ("30301010", "가정용품", "Household Products", ["생활용품"]),
            ]),
            ("303020", "개인용품", "Personal Products", [
                ("30302010", "개인용품", "Personal Care Products", ["화장품"]),
                ("30302020", "의류·신발·액세서리 제조", "Apparel, Accessories & Footwear", ["의류제조"]),
            ]),
        ]),
    ]),
    # 35 Health Care
    ("35", "헬스케어", "Health Care", [
        ("3510", "헬스케어 장비·서비스", "Health Care Equipment & Services", [
            ("351010", "헬스케어 장비·용품", "Health Care Equipment & Supplies", [
                ("35101010", "헬스케어 장비", "Health Care Equipment", ["의료기기"]),
                ("35101020", "헬스케어 용품", "Health Care Supplies", []),
            ]),
            ("351020", "헬스케어 제공자·서비스", "Health Care Providers & Services", [
                ("35102010", "헬스케어 서비스", "Health Care Services", ["병원"]),
                ("35102020", "헬스케어 시설", "Health Care Facilities", []),
            ]),
            ("351030", "헬스케어 기술", "Health Care Technology", [
                ("35103010", "헬스케어 기술", "Health Care Technology", ["의료IT"]),
            ]),
        ]),
        ("3520", "제약·생명과학·생명공학", "Pharmaceuticals, Biotechnology & Life Sciences", [
            ("352010", "제약", "Pharmaceuticals", [
                ("35201010", "제약", "Pharmaceuticals", ["제약"]),
            ]),
            ("352020", "생명과학·생명공학", "Biotechnology & Life Sciences", [
                ("35202010", "생명공학", "Biotechnology", ["바이오"]),
                ("35202020", "생명과학 도구·서비스", "Life Sciences Tools & Services", ["생명과학"]),
            ]),
        ]),
    ]),
    # 40 Financials
    ("40", "금융", "Financials", [
        ("4010", "은행", "Banks", [
            ("401010", "은행", "Banks", [
                ("40101010", "다목적 은행", "Diversified Banks", ["시중은행"]),
                ("40101020", "지역 은행", "Regional Banks", ["지방은행"]),
            ]),
        ]),
        ("4020", "금융 서비스", "Financial Services", [
            ("402010", "금융 서비스", "Financial Services", [
                ("40201010", "다목적 금융 서비스", "Diversified Financial Services", []),
                ("40201020", "다목적 소비 금융", "Consumer Finance", ["소비자금융"]),
                ("40201030", "자본시장", "Capital Markets", ["증권", "투자은행"]),
            ]),
            ("402030", "보험", "Insurance", [
                ("40203010", "보험", "Insurance", ["보험"]),
                ("40203020", "재보험", "Reinsurance", ["재보험"]),
                ("40203030", "보험 브로커·서비스", "Insurance Brokers", ["보험중개"]),
            ]),
        ]),
        ("4030", "부동산", "Real Estate", [
            ("403010", "부동산", "Real Estate", [
                ("40301010", "부동산 운영", "Real Estate Operating Companies", ["부동산"]),
                ("40301020", "부동산 개발", "Real Estate Development", ["건설"]),
                ("40301030", "REITs", "REITs", ["리츠"]),
            ]),
        ]),
    ]),
    # 45 Information Technology
    ("45", "정보기술", "Information Technology", [
        ("4510", "반도체·반도체 장비", "Semiconductors & Semiconductor Equipment", [
            ("451010", "반도체 장비", "Semiconductor Equipment Products", [
                ("45101010", "반도체 장비", "Semiconductor Equipment", ["반도체장비"]),
                ("45101020", "대규모 반도체 제조", "Alternative Energy Equipment", []),
            ]),
            ("451020", "반도체", "Semiconductors", [
                ("45102010", "대규모 반도체 제조", "Semiconductors", ["반도체", "메모리반도체", "비메모리반도체"]),
                ("45102020", "반도체 부품", "Semiconductor Components", ["반도체부품"]),
            ]),
        ]),
        ("4520", "소프트웨어·서비스", "Software & Services", [
            ("452010", "소프트웨어", "Software", [
                ("45201010", "응용 소프트웨어", "Application Software", ["소프트웨어", "앱SW"]),
                ("45201020", "시스템 소프트웨어", "Systems Software", ["시스템SW"]),
                ("45201030", "홈 엔터테인먼트 소프트웨어", "Home Entertainment Software", []),
            ]),
            ("452020", "IT 서비스", "IT Services", [
                ("45202010", "IT 컨설팅·서비스", "IT Consulting & Other Services", ["IT서비스"]),
                ("45202020", "데이터 처리·외주 서비스", "Data Processing & Outsourced Services", ["데이터처리"]),
            ]),
        ]),
        ("4530", "기술 하드웨어·장비", "Technology Hardware, Storage & Peripherals", [
            ("453010", "통신 장비", "Communications Equipment", [
                ("45301010", "통신 장비", "Communications Equipment", ["통신장비"]),
            ]),
            ("453020", "기술 하드웨어·저장·주변장치", "Technology Hardware, Storage & Peripherals", [
                ("45302010", "기술 하드웨어·저장·주변장치", "Technology Hardware, Storage & Peripherals", ["하드웨어"]),
                ("45302020", "전자 제조 서비스", "Electronic Manufacturing Services", ["EMS"]),
                ("45302030", "전자 부품", "Electronic Components", ["전자부품"]),
                ("45302040", "전자 계기·제어", "Electronic Equipment & Instruments", ["전자계측"]),
            ]),
            ("453030", "기술 전자·계측기", "Technology Distributors", [
                ("45303010", "기술 유통", "Technology Distributors", []),
            ]),
        ]),
    ]),
    # 50 Communication Services
    ("50", "커뮤니케이션 서비스", "Communication Services", [
        ("5010", "통신", "Telecommunication Services", [
            ("501010", "통신 서비스", "Telecommunication Services", [
                ("50101010", "종합 통신 서비스", "Integrated Telecommunication Services", ["통신", "이동통신"]),
                ("50101020", "무선 통신 서비스", "Wireless Telecommunication Services", ["무선통신"]),
            ]),
        ]),
        ("5020", "미디어·엔터테인먼트", "Media & Entertainment", [
            ("502010", "미디어·엔터테인먼트", "Media & Entertainment", [
                ("50201010", "영화·엔터테인먼트", "Movies & Entertainment", ["엔터"]),
                ("50201020", "인터랙티브 홈 엔터테인먼트", "Interactive Home Entertainment", ["게임"]),
                ("50201030", "인터랙티브 미디어·서비스", "Interactive Media & Services", ["인터넷", "포털"]),
            ]),
        ]),
    ]),
    # 55 Utilities
    ("55", "유틸리티", "Utilities", [
        ("5510", "유틸리티", "Utilities", [
            ("551010", "전기 유틸리티", "Electric Utilities", [
                ("55101010", "전기 유틸리티", "Electric Utilities", ["전력"]),
                ("55101020", "종합 유틸리티", "Multi-Utilities", ["종합공익"]),
                ("55101030", "수도 유틸리티", "Water Utilities", ["수도"]),
                ("55101040", "독립 전력 생산", "Independent Power Producers", ["IPP"]),
            ]),
            ("551020", "가스 유틸리티", "Gas Utilities", [
                ("55102010", "가스 유틸리티", "Gas Utilities", ["가스"]),
            ]),
        ]),
    ]),
    # 60 Real Estate
    ("60", "부동산", "Real Estate", [
        ("6010", "부동산", "Real Estate", [
            ("601010", "REITs", "Diversified REITs", [
                ("60101010", "다목적 REITs", "Diversified REITs", ["리츠"]),
                ("60101020", "산업 REITs", "Industrial REITs", []),
                ("60101030", "주거 REITs", "Residential REITs", []),
                ("60101040", "헬스케어 REITs", "Health Care REITs", []),
                ("60101050", "소매 REITs", "Retail REITs", []),
                ("60101060", "오피스 REITs", "Office REITs", []),
                ("60101070", "특수 REITs", "Specialized REITs", []),
            ]),
            ("601020", "부동산 운영·개발", "Real Estate Management & Development", [
                ("60102010", "부동산 운영·개발", "Real Estate Management & Development", ["부동산개발"]),
            ]),
        ]),
    ]),
]


def _is_leaf(rest) -> bool:
    return all(isinstance(r, str) for r in rest)


def _flatten(node, sector_code: str, group_code: str, industry_code: str):
    """노드를 재귀 평탄화. 잎(sub-industry, 8자리)만 (sub8, kr, en, aliases, sec, grp, ind) 를 yield."""
    code, kr, en, rest = node
    if len(code) == 8:
        aliases = [a for a in rest if isinstance(a, str)]
        yield (code, kr, en, aliases, sector_code, group_code, industry_code)
        return
    children = [c for c in rest if isinstance(c, tuple)]
    if len(code) == 2:
        for child in children:
            yield from _flatten(child, code, "", "")
    elif len(code) == 4:
        for child in children:
            yield from _flatten(child, sector_code, code, "")
    elif len(code) == 6:
        for child in children:
            yield from _flatten(child, sector_code, group_code, code)


def build() -> dict:
    industries: dict[str, dict] = {}
    for sector in _GICS:
        for sub8, kr, en, aliases, sec, grp, ind in _flatten(sector, "", "", ""):
            industries[f"IND_GICS_{sub8}"] = {
                "id": f"IND_GICS_{sub8}",
                "gics_code": sub8,
                "gics_sector": sec,
                "gics_group": grp,
                "gics_industry": ind,
                "gics_sub_industry": sub8,
                "korean_name": kr,
                "english_name": en,
                "aliases": aliases,
            }
    return {
        "version": "0.1.0",
        "metadata": {
            "name": "GICS 산업 분류",
            "description": "GICS 2023 — 11 sector / 25 group / 69 industry / 163 sub-industry 시드",
            "standards": ["GICS"],
            "created": "2026-07-27",
            "updated": "2026-07-27",
        },
        "ontology": {"industries": industries},
    }


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "ontology" / "industries.yaml"
    doc = build()
    with out.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False)
    print(f"wrote {out} ({len(doc['ontology']['industries'])} industries)")


if __name__ == "__main__":
    main()