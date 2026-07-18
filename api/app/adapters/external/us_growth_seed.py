"""US 성장주 강제 편입 화이트리스트 — 테마별 중소형 성장주.

S&P500·Nasdaq 시총 상위 컷에 걸리지 않는 순수 성장주(클린에너지·우라늄·차세대반도체·양자·
우주·바이오·핀테크 등)를 유니버스에 확실히 편입하기 위한 목록. 네이버가 커버하지 않는 티커는
fetch_row 가 None 을 반환해 자동 제외되므로, 여기 넣어도 무해하다(조회 실패 = 미편입).

sector 값은 테마 라벨로 쓴다(GICS 정식 섹터 아님) — 스크리너·표시용 그룹핑.
"""

from __future__ import annotations

# 테마 → 티커 목록. dedup 은 아래에서 처리(BE·NET 등 복수 테마 중복 허용).
_BY_THEME: dict[str, list[str]] = {
    "클린에너지": ["FLNC", "BE", "STEM", "RUN", "SHLS", "ARRY", "NXT", "MAXN", "CSIQ",
                "SEDG", "ENPH", "FSLR"],
    "우라늄·원자력": ["UEC", "CCJ", "DNN", "UUUU", "UROY", "LEU", "SMR", "OKLO", "NNE", "BWXT"],
    "차세대반도체": ["NVTS", "WOLF", "POWI", "CRDO", "ALAB", "AMBA", "LSCC", "SITM", "INDI",
                 "MPWR", "RMBS", "ARM"],
    "AI인프라": ["CRWV", "NBIS", "SMCI", "VRT", "TSSI", "AI", "BBAI", "SOUN", "PLTR", "PSTG", "GDS"],
    "양자컴퓨팅": ["IONQ", "RGTI", "QBTS", "QUBT", "ARQQ", "LAES"],
    "우주·위성": ["RKLB", "ASTS", "LUNR", "RDW", "PL", "BKSY", "SATS", "SPIR"],
    "전기차·자율주행": ["RIVN", "LCID", "CHPT", "EVGO", "QS", "AMPX", "SES", "MVST", "ENVX",
                   "LAZR", "OUST", "INVZ", "AUR"],
    "수소": ["PLUG", "BLDP", "FCEL"],
    "바이오·유전자": ["VKTX", "CRSP", "NTLA", "BEAM", "RXRX", "SDGR", "RNA", "ARWR", "RARE", "TEM"],
    "핀테크": ["SOFI", "AFRM", "UPST", "DLO", "MQ", "BILL", "TOST", "NU", "PAYO"],
    "사이버보안": ["CRWD", "ZS", "S", "NET", "TENB", "RBRK", "VRNS", "CYBR"],
    "클라우드·SaaS": ["SNOW", "DDOG", "MDB", "CFLT", "GTLB", "ESTC", "PATH", "FROG", "APP"],
    "암호화폐·블록체인": ["COIN", "MARA", "RIOT", "CLSK", "IREN", "HUT", "BITF", "CIFR", "WULF",
                   "BTDR", "HOOD", "CORZ"],
    "로보틱스·드론": ["SERV", "KSCP", "RR", "AVAV", "ONDS", "UMAC", "RCAT"],
    "전력인프라·성장소비": ["GEV", "POWL", "TTD", "RDDT", "DUOL"],
}


def growth_seed() -> list[tuple[str, str]]:
    """(ticker, theme) 목록. 첫 등장 테마를 sector 로 부여하고 티커 dedup."""
    seen: dict[str, str] = {}
    for theme, tickers in _BY_THEME.items():
        for t in tickers:
            seen.setdefault(t, theme)
    return sorted(seen.items())
