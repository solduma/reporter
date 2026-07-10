# reporter 📈🐱

매일 아침 증권사 리포트를 자동 수집·분석해 **텔레그램**으로 브리핑을 보내는 시스템.
AI 분석은 **Ollama Cloud의 GLM (`glm-5.2:cloud`)** 을 사용한다. (OpenClaw 미사용, 독립 실행형 Python + cron)

원본 아이디어([OpenClaw + Slack + Claude](ref.md))를 다음과 같이 대체했다.

| 원본 | 이 프로젝트 |
|---|---|
| 슬랙 + 텔레그램 | **텔레그램만** |
| Anthropic Claude (Haiku/Sonnet) | **Ollama Cloud GLM (`glm-5.2:cloud`)** |
| OpenClaw 에이전트 | **독립 Python 패키지 + cron** |

> 참고: 사용자가 요청한 "glm5.2"는 Ollama Cloud에 정확히 `glm-5.2:cloud` 태그로 존재한다(구 `glm-4.6`은 2026-06-16 은퇴). 그래서 기본값을 `glm-5.2:cloud`로 설정했다.

## 흐름

**오전 브리핑** (`--batch N` / `--all`)
1. 네이버 금융 리서치 6개 카테고리에서 당일 리포트 수집 (EUC-KR)
2. 조회수 정규화 + 주요 증권사 보너스(+30)로 카테고리별 상위 N개 선별
3. PDF 다운로드 → 앞 3페이지 텍스트 추출
4. **1차** GLM: 리포트별 150자 요약
5. **2차** GLM: 전체 요약 종합 인사이트 (핵심/테마/종목/리스크)
6. 텔레그램 발송 (4096자 초과 시 자동 분할) + 당일 로그 저장

**오후 능동 리서치** (`--afternoon`)
1. 오전 브리핑 로그에서 GLM이 핵심 키워드 5개 추출
2. 키워드별 Google News RSS 검색
3. 오전 대비 변화를 2차·3차 구조적 연결로 분석 → 키워드별 발송

## 설치

```bash
uv sync
cp .env.example .env
# .env 에 아래 값 채우기
```

> 루트에 `Makefile` 이 있어 자주 쓰는 작업을 단축 실행할 수 있다: `make help` 로 목록 확인.
> 예) `make tui`(Admin TUI), `make api`/`make web`/`make worker`, `make test`, `make lint`,
> `make fmt`(포매팅), `make hooks`(pre-commit 훅 활성화). api·web 디렉터리로 들어가지 않아도 된다.

### 환경변수 (`.env`)

```
OLLAMA_API_KEY=...           # https://ollama.com/settings/keys 에서 발급
OLLAMA_SUMMARY_MODEL=glm-5.2:cloud
OLLAMA_INSIGHT_MODEL=glm-5.2:cloud
TELEGRAM_BOT_TOKEN=...        # @BotFather /newbot 로 발급
TELEGRAM_CHAT_ID=...          # 아래 방법으로 확인
```

### 텔레그램 봇 만들기
1. `@BotFather` 에서 `/newbot` → 이름·username(반드시 `bot`으로 끝남) 설정 → 토큰 발급
2. 만든 봇과 대화 시작(아무 메시지 전송) — 봇은 먼저 말을 걸 수 없다
3. `.env` 에 토큰 저장 후 `uv run reporter --chat-id` 실행 → chat_id 확인 (또는 `getUpdates` 직접 호출)
4. 확인한 chat_id 를 `.env` 의 `TELEGRAM_CHAT_ID` 에 저장

## 실행

각 메시지는 최상단에 이모지 헤더로 종류를 표시한다.

```bash
# 07:00 미국증시 마감(지수 수치) + 간밤 뉴스 종합
uv run reporter --premarket

# 09:30 종목분석·산업분석 — 종목/산업 단위로 종합(단위별 리포트 링크 전부)
uv run reporter --per-entity

# 10:00~ 카테고리별 장문 종합 1건 + 인용 상위 5개 링크
uv run reporter --digest market_info   # 시황  (📈)
uv run reporter --digest invest        # 투자  (💡)
uv run reporter --digest economy       # 경제  (🌍)
uv run reporter --digest debenture     # 채권  (💵)

# 17:00 마감 시황 종합
uv run reporter --closing

# 09~16시 매시 — 장중 시장 뉴스 종합(본문 크롤+GLM, 링크 단축)
uv run reporter --news

# 14:00 오후 능동 리서치(키워드별 뉴스 연결)
uv run reporter --afternoon

# 기타
uv run reporter --reset-log           # 당일 로그 초기화
uv run reporter --chat-id             # 텔레그램 chat_id 조회
uv run reporter --per-report 1        # (레거시) 리포트당 1건 개별 발송
uv run reporter --batch 1 | --all     # (레거시) 종합 브리핑
```

> 링크는 TinyURL 로 단축해 발송한다(캐시: `logs/url_cache.json`). 장중/미장 뉴스는
> 상위 기사 본문을 headless Chrome 으로 크롤해 GLM 이 서술형으로 종합한다(시스템 Chrome 필요).

### 포럼 토픽 (선택)

`TELEGRAM_USE_TOPICS=1` 이고 `TELEGRAM_CHAT_ID` 가 **포럼 모드 ON 슈퍼그룹**이면, 종목/산업
리포트(`--per-entity`)와 장중 뉴스(`--news`)를 일자별 토픽에 누적해 개별 메시지가 묻히지
않게 한다. 개별 메시지는 무음, 토픽 생성·갱신 시 헤더(마지막 업데이트 시각·건수)로 알림.

준비: ① 텔레그램에서 그룹 생성 → 설정에서 **Topics 켜기**(포럼 모드) → 슈퍼그룹으로 승격,
② 봇을 관리자로 추가하고 **manage_topics(토픽 관리)** 권한 부여, ③ `--chat-id` 로 그룹 id
확인 후 `TELEGRAM_CHAT_ID` 에 설정. (채널은 토픽을 지원하지 않는다.) 포럼이 아니거나 권한이
없으면 자동으로 일반 발송으로 폴백한다. 토픽 상태는 `logs/forum_topics.json` 에 보존된다.

## 자동 실행 (launchd — macOS 권장)

`launchd/install.sh` 를 **한 번** 실행하면 `~/Library/LaunchAgents/` 에 plist 가 설치되어
**재부팅·재로그인 후에도 자동 유지**된다(매번 다시 등록할 필요 없음).

등록 항목: ① CLI 텔레그램 예약 잡(premarket/digest/news/… — StartCalendarInterval),
② **웹서비스 상시 서버** `com.reporter.server.api`(:8010)·`com.reporter.server.web`(:43000)
— RunAtLoad+KeepAlive 로 부팅 시 자동 실행·죽으면 재시작. (web 서버는 `cd web && pnpm build`
산출물이 있어야 등록된다.) 상태 확인·재기동은 Admin TUI 에서, 또는 `launchctl kickstart -k`.

```bash
cd web && pnpm build && cd ..    # web 상시 서버를 쓸 거면 먼저 빌드
./launchd/install.sh             # CLI 잡 + 웹/API 서버 등록 (재실행 시 자동 갱신)
launchctl list | grep com.reporter   # 등록 확인
./launchd/install.sh uninstall   # 전체 제거(서버 포함)
```

cron 대비 장점: **예약 시각에 맥이 슬립 중이었으면 깨어날 때 놓친 작업을 1회 실행**한다
(cron 은 그냥 건너뛰어 리포트가 누락됨 — 원본에서 겪은 "왜 리포트 안 왔어?" 문제).
스케줄은 cron 과 동일(09:00 로그 초기화 → 09:30/10:00/10:30/11:00 배치 → 14:00 오후 리서치, 월~금).
로그: `logs/launchd.log`.

### 대안: cron

`crontab.example` 참고. cron 도 **한 번 `crontab` 으로 등록하면 재부팅 후 유지**되지만,
슬립 중 예약 시각은 건너뛴다. macOS 에서 `crontab -e` 실행 시 뜨는 보안 승인 UI는 사람이 직접 클릭해야 한다.

```bash
crontab crontab.example   # 경로 수정 후
```

## 테스트

```bash
uv run pytest       # 선별/분할/크롤링 파싱 로직 단위 테스트
uv run ruff check . # 린트
```

## 알아둘 것 (원본에서 겪은 함정 반영)
- **자동실행 유지**: launchd/cron 모두 한 번 등록하면 재부팅 후 유지됨. 매번 다시 올릴 필요 없음. 단 **슬립 중 예약 시각**은 cron 이 건너뛰므로, 노트북을 자주 닫는다면 launchd 권장(깨어날 때 놓친 작업 1회 실행).
- **스케줄 시간**: 리포트는 9시 이후 발행 → 8시에 돌리면 0개. batch로 시간차 실행.
- **주말**: 증권사도 쉼 → 월~금(Weekday 1-5)만 실행.
- **네이버 인코딩**: 페이지는 EUC-KR인데 head에 잘못된 `<meta charset=utf-8>`가 있음 → 강제 euc-kr 디코딩.
- **`.env` override**: `load_dotenv(override=False)` — 이미 export된 정상 값을 만료된 `.env` 값이 덮어쓰지 않게.
