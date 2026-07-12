# 공유 커널 (shared kernel) — `src/reporter` ↔ `api/app`

> `api/app`(웹 API + 수집 워커)이 `src/reporter`(텔레그램 발송 CLI 제품)에서 재사용하는
> **공유 표면**의 정의와 규약. 의존은 `app → reporter` 단방향(`reporter → app` = 0).

## 왜 필요한가

`reporter` 는 원래 텔레그램 브리핑 CLI 제품이지만, 크롤·GLM·파싱·도메인 로직이 웹 서비스에도
유용해 `api/app` 이 일부 모듈을 빌려 쓴다. 어떤 모듈이 "공유 표면"이고 어떤 게 "CLI 전용"인지
명시하지 않으면, 누군가 `app` 에서 발송 파이프라인(`pipeline`·`telegram` 등)을 직접 불러 두 제품이
결합돼도 막을 수 없다. 이 문서 + import-linter 계약이 그 경계를 고정한다.

## 공유 커널 (app 이 import 해도 되는 12모듈)

`api/app` 이 실제로 재사용하는 `reporter` 모듈. **이 모듈들의 시그니처를 바꿀 때는 `api/app`
사용처도 함께 확인·수정해야 한다.**

| 모듈 | 성격 | app 사용처(예) |
|---|---|---|
| `reporter.models` | 순수 도메인(데이터 타입) | 여러 서비스의 Report 등 |
| `reporter.market` | 순수 도메인(시장 유틸) | ingest·sector |
| `reporter.sector_etf` | 순수 도메인(섹터 ETF 매핑) | analysis·sector_flow·industry |
| `reporter.fallback` | 도메인 유틸(폴백 이벤트 로깅) | fallback_store·여러 계측 지점 |
| `reporter.ollama_client` | IO — GLM 클라이언트 | **adapters/llm 한 곳만**(LLMPort 뒤) |
| `reporter.crawler` | IO — 리서치 크롤 | ingest |
| `reporter.pdf` | IO — PDF 텍스트 추출 | ingest |
| `reporter.news` | IO — 뉴스 수집 | ingest·news_events |
| `reporter.judal` | IO — 섹터 테마 스크랩 | sector_ingest |
| `reporter.us_market` | IO — 미국/지수/환율 시세 | analysis·ingest·industry·us_universe |
| `reporter.analyzer` | IO — GLM 요약·종합 | ingest |
| `reporter.article` | IO — 기사 파싱 | ingest |

## CLI 전용 (app 이 import 하면 안 되는 모듈)

텔레그램 발송 제품에만 속하는 모듈. `app` 은 이들을 직접 부르지 않는다(계약으로 차단).

`cli` · `pipeline` · `afternoon` · `telegram` · `forum` · `shortener` · `archive` · `config` ·
`grouping` · `selector`

> 두 제품의 데이터 공유는 코드 결합이 아니라 **`broadcasts.jsonl` 스풀**로 한다: 발송 CLI 가
> 발송분을 스풀에 append → API 가 `broadcast` 테이블로 흡수(단방향, 느슨).

## 강제 방법

`api/.importlinter` 계약 **`app-no-cli-only`** 가 `app → CLI전용모듈` 직접 import 를 차단한다.
`root_packages = app, reporter` 로 두 패키지를 함께 그래프에 올려야 `reporter` 서브모듈을
forbidden 대상으로 지정할 수 있다(external 서브패키지는 forbidden 불가). 같은 이유로
**`llm-behind-port`** 계약이 `reporter.ollama_client` 를 `adapters/llm` 밖에서 못 쓰게 강제한다.

`make lint`(githook·CI 포함)가 매번 검증한다. 위반 주입 시 계약이 BROKEN 되는 것으로 실효 확인됨.

## 관련

- 재구조화 배경·대안 비교: [restructure-proposal.md](restructure-proposal.md) (대안 B 채택)
- 육각형 아키텍처 전반: `api/app` 의 domain·ports·adapters 계층 + `api/.importlinter` 11계약
