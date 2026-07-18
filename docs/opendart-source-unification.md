# OpenDART 데이터 소스 통일 설계

## 목표
재무·배당·주식수·주주·이벤트 데이터의 출처를 **네이버 스크랩·KRX에서 OpenDART로 통일**하고,
지표를 이 정규화된 DB에서 뽑는다. 가격(일봉/종가/시총)은 OpenDART가 제공하지 않으므로 예외.

## 1. 현재 소스 의존도 (as-is)

| 데이터 | 현재 소스 | 저장 위치 | OpenDART 대체 |
|---|---|---|---|
| 매출·영업이익·순이익·자본·EPS | DART DS003(fnlttSinglAcntAll) | report_financials, financials | ✅ 이미 DART |
| 전체 계정(BS/IS/CF 전 계정) | 일부 원문 XML 파싱 | — | ✅ DS003 단일회사 전체재무제표(fnlttSinglAcntAll, fs_div=CFS) |
| D&A(감가+무형상각) | **원문 document.xml 파싱** | report_financials.depreciation | △ DS003 전체재무제표 CF 계정으로 대체 시도 가능(대형사 검증 필요) |
| **ROE** | **네이버 스크랩** | financials.roe | ✅ DS003 재무지표(fnlttSinglIndx, M210000 수익성) |
| **DPS·배당수익률** | **네이버 스크랩** | financials.dps, div_yield | ✅ DS002 배당(alotMatter) |
| **발행주식수** | **KRX(fetch_shares)** | (EV 계산 in-memory) | ✅ DS002 주식총수(stockTotqySttus) |
| 최대주주·소액주주 | 원문 XML 파싱(딥다이브) | — | ✅ DS002(2019007/2019009) |
| PER·PBR·PSR·BPS | DART재무 + 가격 계산 | financials | ✅ 재무는 DART, 가격만 외부 |
| 이벤트(유상증자·소송·자기주식·합병) | 뉴스 + DART 공시목록(하이브리드) | — | ✅ DS005 주요사항보고서 정형 파싱 강화 |
| **가격(일봉·종가·시총)** | **네이버·KRX** | price_candles, universe_snapshot | ❌ **OpenDART 미제공 — 유지** |

## 2. OpenDART API 매핑 (to-be)

DART API 그룹: DS001 공시정보 · DS002 정기보고서 주요정보 · DS003 재무정보 · DS004 지분공시 · DS005 주요사항보고서 · DS006 증권신고서.

| 신규 사용 API | 엔드포인트 | 대체 대상 | 제공 필드 |
|---|---|---|---|
| DS003 단일회사 주요 재무지표 | `/api/fnlttSinglIndx.json` | 네이버 ROE 등 | idx_cl_code=M210000(수익성)/M220000(안정성)/M230000(성장성)/M240000(활동성), idx_nm·idx_val. **2023 3Q부터 제공** |
| DS002 배당에 관한 사항 | `/api/alotMatter.json` | 네이버 DPS·배당수익률 | se(구분: 주당현금배당금·현금배당수익률 등)·thstrm/frmtrm/lwfr(당/전/전전기). **2015부터** |
| DS002 주식의 총수 현황 | `/api/stockTotqySttus.json` | KRX 발행주식수 | istc_totqy(발행총수)·tesstk_co(자기주식)·distb_stock_co(유통주식). **2015부터** |
| DS002 최대주주 현황 | `/api/hyslrSttus.json`(2019007) | 원문 XML 파싱 | 지분율·보유주식수 |
| DS003 단일회사 전체 재무제표 | `/api/fnlttSinglAcntAll.json` | 원문 XML D&A | 전 계정(CF 감가상각 포함) |
| DS005 주요사항보고서 | 유상증자결정·소송제기·자기주식취득 등 | 이벤트 뉴스 보조 | 정형 이벤트(신뢰도 100%) |

## 3. 설계 원칙

### 3-1. 단일 정규화 재무 테이블
현재 `report_financials`(원본·원 단위)와 `financials`(혼합·억원)로 이원화. OpenDART 통일 후:
- **`report_financials`를 정규 원천으로 승격** — 모든 재무·지표·배당·주식수가 DART 유래로 일원화.
- `financials`는 파생/표시 캐시로만(가격 곱한 PER/PBR + DART 지표 복사).
- 소스 컬럼(`source`) 추가로 각 값 출처 추적(dart_indx / dart_dividend / dart_stock / price).

### 3-2. 헥사고날 준수
- OpenDART 호출은 `adapters/dart/client.py` 한 곳에 집중(포트 `KrDisclosurePort`/신규 `KrFinancialsPort`).
- 지표 계산은 domain(순수)에서, DB 조회는 service에서. 네이버 어댑터는 **가격 전용**으로 축소.

### 3-3. 점진 마이그레이션 (하위호환)
각 필드를 소스별로 독립 전환. 네이버 폴백을 남겨 DART 결측(예: 재무지표 2023 3Q 이전)을 메움.

## 4. 구현 단계 (제안)

1. ✅ **DS002 주식총수 → 발행주식수** (#426 완료): KRX fetch_shares 대체(폴백 유지). EV/EBITDA·PER 정확도 향상. (독립·저위험)
2. ✅ **DS002 배당 → DPS·배당수익률** (#428 완료): alotMatter 로 백필 10년치 dps·현금배당수익률을 DART 우선 적재, 미공시·무배당은 네이버 스크랩 폴백.
3. **DS003 재무지표 → ROE 등**: fnlttSinglIndx로 ROE·영업이익률 등. 2023 3Q 이전은 네이버 폴백.
4. **DS003 전체재무제표 → D&A**: 원문 XML 파싱을 구조화 API로 대체 시도(대형사 정확도 A/B 검증 후).
5. **DS002 주주현황 → 최대주주**: 딥다이브 원문 파싱 대체.
6. **DS005 정형 이벤트**: 딥다이브 이벤트 탐색에 정형 공시 추가(뉴스 보조).

## 5. 확정된 결정 (2026-07-17)
- **가격 소스**: 네이버/KRX 유지(OpenDART 미제공). 재무·배당·주식수·지표만 DART 통일.
- **마이그레이션**: 1단계(주식수)부터 점진, 각 단계 독립 PR. 결측 구간은 기존 소스 폴백.
- **재무지표 2023 3Q 이전**: 네이버 폴백 유지(fnlttSinglIndx 미제공 구간).
- **DART 일일 한도**: 백업키 링([[reporter-dart-quota-failover]]) + 캐싱 + 야간 분산으로 흡수.
