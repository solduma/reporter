# reporter — 프로젝트 규칙

전역 규칙(응답 스타일·패키지 매니저·코드 품질)과 skills(dev-workflow·project-setup·hexagonal-architecture·browser-verify)는 `~/.claude/CLAUDE.md`를 따른다. 이 파일은 **reporter 고유** 사실만 담는다.

## 구성
- `api/` — FastAPI 백엔드(uv, 육각형 아키텍처). `web/` — Next.js 프론트(pnpm). `infra/` — docker-compose(postgres/redis/minio 격리 포트).

## 프론트엔드 검수 (필수)
`web/`에서 **사용자가 보는 것**(차트·오버레이·레이아웃·스타일)을 바꾸면, 머지/배포 전에 반드시 **실제 브라우저(headless Chrome 스크린샷)로 렌더 결과를 눈으로 확인**한다. lint/build 통과만으로 "완료"라고 하지 않는다 — 이 프로젝트는 lint/build는 통과했지만 화면이 깨진 회귀를 여러 번 냈다. 방법·포트·함정은 **browser-verify** skill 참조(로컬 무비번 웹 43100 + API 워밍 + 스크린샷).

## 런타임 포트
| 서비스 | 실행 | 포트 |
|---|---|---|
| API | launchd `com.reporter.server.api` (uvicorn 직접 실행) | 8010 |
| web(프로덕션) | launchd `com.reporter.server.web` (`pnpm start`, 로그인 게이트) | 43000 |
| web(로컬 검수용) | `LOGIN_PASSWORD= pnpm run start -p 43100` (게이트 열림) | 43100 |
| worker | docker `reporter-worker` (APScheduler) | — |

web은 API를 `127.0.0.1:8010`으로 프록시(`web/next.config.mjs`). `.env`에 라이브 자격증명(DART/Telegram/KIS/Ollama) — worker 재빌드/배치는 실동작이므로 주의.

## 배포 (대상별 상이)
- **worker**: 코드가 이미지에 내장 → **재빌드 필수**. `cd infra && docker compose --env-file .env up -d --build reporter-worker`.
- **API**: 워킹트리 직접 실행 → git pull 후 `launchctl kickstart -k gui/$(id -u)/com.reporter.server.api`.
- **web**: `pnpm start`가 프리빌드 `.next`를 서빙 → **`pnpm run build` 후** `launchctl kickstart -k gui/$(id -u)/com.reporter.server.web`.
- 어느 대상을 배포할지는 diff 위치로 판단: `api/domain·services`가 worker 도메인(stage/scheduler 등)이면 worker도, 라우터/스키마면 API, `web/`면 web. 재무 수집 코드는 worker+API 둘 다.

## 머지·배포 권한
- **PR 생성·머지는 사전 승인됨** — 사용자가 commit/PR/merge 전권을 위임했다. 로컬 검증(lint·build·test·`web/`이면 browser-verify)이 통과하면 `gh pr create` → `gh pr merge --merge --admin --delete-branch`까지 **묻지 말고** 진행한다. 머지 후 `--delete-branch`가 실패하거나 worktree/로컬 브랜치가 남으면 `merge-cleanup` skill을 호출해 로컬·원격 브랜치와 임시 worktree를 즉시 정리한다. CI(GitHub Actions)는 제거됐으므로 로컬 pre-commit·검증이 머지 근거다. 이는 반복 확인을 없애기 위한 사용자의 명시적 지시다.
- **프로덕션 배포만 사용자 확인 필요** — worker 재빌드·API/web 재시작은 라이브 영향이므로 배포 직전에만 확인한다(CLI select menu). 머지 ≠ 배포.
