# Financial Ontology Layer

DART(K-IFRS) XBRL · IFRS · US GAAP · SEC XBRL 재무제표를 하나의 정규화된 온톨로지로 통합하는 계정 과목·지표 그래프. 한국 상장사(KOSPI/KOSDAQ) 공시가 1차 대상 — **한국어 계정명을 정준(canonical)**으로 사용한다.

## 목적
1. 계정과목 정규화(Normalization) — 이기준·이언어 공시 항목을 단일 ID로 매핑
2. 재무비율 계산 — 계정 ID 기반 formula + `required_accounts`
3. LLM 기반 재무 분석 — 계정 메타데이터로 의미 기반 프롬프트 구성
4. RAG 검색 — 한국명/영문명/별칭 역방향 인덱스로 계정 검색
5. 산업별 확장 — common 상속 + manufacturing/bank/insurance/securities

## 구조
```
financial-ontology/
├── ontology/                     # 온톨로지(단일 진실원, SOT)
│   ├── common.yaml               # K-IFRS 공통 골격, 5 명세서 129 계정
│   ├── manufacturing.yaml        # 제조업 확장(재고·원가요소·유형자산·R&D·CAPEX)
│   ├── bank.yaml                 # 은행(예수금·대출금·이자수익·대손상각비)
│   ├── insurance.yaml            # 보험(책임준비금·보험료·보험금·준비금변동)
│   └── securities.yaml           # 증권(매매·운용유가증권·수수료)
├── mappings/                     # 매핑(자동 생성 — 수정 금지, 아래 스크립트로 재생성)
│   ├── dart_mapping.yaml         # ontology_id ↔ DART XBRL taxonomy + 한국명 역인덱스
│   ├── ifrs_mapping.yaml
│   └── usgaap_mapping.yaml
├── ratios/
│   ├── profitability.yaml        # ROE·ROA·마진·EBITDA·NIM·배당성향 등 17
│   ├── liquidity.yaml            # 유동·당좌·현금비율·운전자본·FCF·CCC 등 11
│   ├── leverage.yaml             # 부채비율·D/E·이자보상배율·예대율·NPL·종합비율 등 18
│   └── valuation.yaml            # EPS·PER·PBR·EV/EBITDA·FCF수익률·PEG 등 11
├── schema/
│   ├── ontology_schema.json      # 온톨로지 문서 검증
│   └── account_schema.json       # 개별 계정 검증
├── examples/
│   ├── samsung_electronics.yaml  # 삼성전자 공시 → 온톨로지 노드(제조)
│   ├── hyundai_motor.yaml        # 현대차(제조)
│   └── kb_financial.yaml         # KB금융지주(은행)
├── scripts/
│   ├── build_mappings.py         # ontology/*.yaml → mappings/*.yaml 자동 생성
│   └── validate.py               # 전체 무결성 검증(스키마+참조+ID 패턴)
└── README.md
```

## 설계 원칙
1. **Semantic First** — 각 계정은 `id / korean_name / english_name / statement / category / sign / cashflow_mapping / ratios / aliases / mappings` 메타데이터 보유
2. **Global Unique ID** — `{STATEMENT}_{CATEGORY}_{ACCOUNT}` (예: `BS_CA_AR`, `IS_OP_INCOME`, `CF_OP_TOTAL`). 한번 부여되면 **변경 금지**
3. **Multi-standard Mapping** — `mappings.{dart,ifrs,usgaap}` + 별도 매핑 테이블(역방향 인덱스 포함)
4. **Industry Extension** — `metadata.extends: common.yaml` 선언 후 산업 계정만 추가. ID 체계는 common과 공유
5. **Ratio Aware** — 계정의 `ratios`/`affects`(하향 지표)·`depends_on`(상향 계정)로 그래프 구성

### 지표 그래프 패턴
```yaml
# 계정 → 자신이 입력인 비율·파생지표(하향)
BS_CA_AR:
  affects: [WorkingCapital, OCF]
  ratios: [receivable_turnover, cash_conversion_cycle]

# 비율 → 상위 계정(상향 의존)
roe:
  depends_on: [IS_NI_PARENT, BS_EQ_PARENT]

# 현금흐름 계정이 영향하는 파생지표
CF_OP_TOTAL:
  affects: [FCF, OCF, CashConversionCycle]
```

## ID 규칙
- 패턴: `^[A-Z]+_[A-Z]+_[A-Z0-9_]+$` (최소 2개 언더스코어, 3그룹)
- 명세서 접두어: `BS_`(재무상태표) `IS_`(손익계산서) `CI_`(기타포괄손익) `CE_`(자본변동표) `CF_`(현금흐름표)
- 카테고리 예: `CA`(유동자산) `NCA`(비유동자산) `CL`(유동부채) `NCL`(비유동부채) `EQ`(자본) `REV`(수익) `COGS`(원가) `OP`(영업) `NONOP`(영업외) `PBT`(세전) `NIM`(순이자) 등

## 사용
```bash
# 매핑 재생성(온톨로지 변경 후)
python3 financial-ontology/scripts/build_mappings.py

# 전체 검증
python3 financial-ontology/scripts/validate.py
```
의존성: `pyyaml`, `jsonschema`.

## 정규화 사용 예시
DART XBRL 요소 → 온톨로지 ID:
```python
import yaml
m = yaml.safe_load(open("financial-ontology/mappings/dart_mapping.yaml"))
ont_id = m["by_taxonomy"]["ifrs-full_CashAndCashEquivalents"]  # -> BS_CA_CASH
```
한국 계정명(텍스트 공시) → 온톨로지 ID:
```python
ont_id = m["by_korean_name"]["매출채권"]  # -> BS_CA_AR
```

## 상태
- 계정 187(common 129 + manufacturing 12 + bank 19 + insurance 14 + securities 13)
- 비율 57
- 매핑: DART 87 / IFRS 88 / US GAAP 73 taxonomy 요소, 한국명(정준+별칭) 432
- 예시 기업 3종(삼성전자·현대차·KB금융)
- 모든 파일 `validate.py` 통과

`api/web/infra` 기존 서비스와는 아직 미연결 — 독립 모듈. 연결 시 adapters에서 온톨로지 로드·정규화·비율 계산 포트 제공 예정.