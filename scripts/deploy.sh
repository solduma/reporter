#!/bin/bash
# 프로덕션 배포 — release 브랜치에서 변경된 대상(worker/api/web)만 멱등 배포한다.
# self-hosted GitHub Actions runner(로컬 맥) 또는 사람이 직접 실행한다.
#
#   scripts/deploy.sh                 # HEAD~1..HEAD diff 로 대상 자동 판단
#   scripts/deploy.sh api web         # 대상 명시(자동 판단 무시)
#   DEPLOY_BASE=<sha> scripts/deploy.sh   # diff 기준 커밋 지정(CD 가 이전 배포 sha 를 넘김)
#
# 대상별 배포 방식(CLAUDE.md 와 동일):
#   worker : 코드가 이미지에 내장 → docker compose 재빌드
#   api    : 워킹트리 직접 실행 → launchctl kickstart(재시작)
#   web    : pnpm start 가 프리빌드 .next 서빙 → pnpm build 후 launchctl kickstart
#
# 배포 판단 규칙(경로 → 대상):
#   web/**                                  → web
#   api/app/routers·schemas                 → api
#   api/app/domain·services, src/**, api/** → api + worker(공유 도메인·재무수집은 양쪽)
#   infra/**, pyproject/uv.lock             → worker

set -euo pipefail

log()  { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[deploy] %s\033[0m\n' "$*" >&2; exit 1; }

# launchctl/pnpm/docker/uv 를 self-hosted runner 환경에서도 찾도록 PATH 보강.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
UID_NUM="$(id -u)"

# 실제 프로덕션 워킹트리 — launchd(api/web)·docker(worker)·.env 가 모두 이 경로를 기준으로 동작한다.
# CD runner 는 저장소를 자체 작업공간(_work/...)에 별도 clone 하므로, 배포는 반드시 이 실 워킹트리에서
# git 동기화 후 수행해야 실서비스 코드가 바뀐다(runner 작업공간엔 .env 도 없다). env 로 override 가능.
PROD_DIR="${PROD_DIR:-$HOME/workspace/reporter}"
PROD_BRANCH="${PROD_BRANCH:-release}"
[[ -d "$PROD_DIR/.git" ]] || die "프로덕션 워킹트리를 찾을 수 없습니다: $PROD_DIR (PROD_DIR 로 지정)"
cd "$PROD_DIR"

# 실 워킹트리를 release 최신으로 동기화(reset --hard — 배포 서버라 로컬 변경 없음 전제).
# 명시 대상(수동 배포) 시엔 이미 동기화됐다고 보고 건너뛸 수 있으나, 항상 맞추는 게 안전하다.
log "프로덕션 워킹트리 동기화: $PROD_DIR ($PROD_BRANCH)"
git fetch origin "$PROD_BRANCH" --quiet || die "git fetch 실패"
SYNC_OLD="$(git rev-parse HEAD)"
git reset --hard "origin/$PROD_BRANCH" --quiet || die "git reset 실패"
SYNC_NEW="$(git rev-parse HEAD)"
log "동기화 완료: ${SYNC_OLD:0:8} → ${SYNC_NEW:0:8}"

# ── 대상 결정 ────────────────────────────────────────────────────────────
# macOS 기본 /bin/bash 는 3.2 라 연관배열(declare -A)을 지원하지 않는다 → 일반 변수 3개로.
WANT_API=0
WANT_WEB=0
WANT_WORKER=0

if [[ $# -gt 0 ]]; then
  for t in "$@"; do
    case "$t" in
      api)    WANT_API=1 ;;
      web)    WANT_WEB=1 ;;
      worker) WANT_WORKER=1 ;;
      *) die "알 수 없는 대상: $t (api|web|worker)" ;;
    esac
  done
  log "대상(명시): $*"
else
  # diff 기준: DEPLOY_BASE(CD 가 이전 release HEAD 를 넘김) 우선, 없으면 이번 동기화 이전 커밋.
  base="${DEPLOY_BASE:-$SYNC_OLD}"
  # 브랜치 최초 생성 push 는 before 가 40개 0 · 동기화로 변경 없음 → 상황별 처리.
  if [[ "$base" =~ ^0+$ ]] || ! git rev-parse --verify "$base" >/dev/null 2>&1; then
    warn "diff 기준 '$base' 없음 → 전체 배포로 폴백"
    WANT_API=1; WANT_WEB=1; WANT_WORKER=1
  else
    changed="$(git diff --name-only "$base" HEAD)"
    if [[ -z "$changed" ]]; then
      log "변경 파일 없음($base..HEAD) — 이미 최신. 배포 생략."
      exit 0
    fi
    log "변경 파일($base..HEAD):"; echo "$changed" | sed 's/^/    /'
    # worker(app.scheduler)는 api/ 를 이미지에 내장해 도메인·services 를 실행하므로, 그쪽 변경은
    # worker 도 재빌드한다. 단 worker 가 안 쓰는 게 명백한 API 전용 파일(tui·server_control·routers·
    # schemas)은 worker 제외해 불필요한 docker 재빌드(수 분)를 피한다.
    while IFS= read -r f; do
      [[ -z "$f" ]] && continue
      case "$f" in
        web/*)                                        WANT_WEB=1 ;;
        api/app/tui.py|api/app/services/server_control.py) WANT_API=1 ;;
        api/app/routers/*|api/app/schemas*)           WANT_API=1 ;;
        # domain·services·adapters·db·config·src 는 worker(스케줄러)가 이미지에 내장해 실행하므로
        # 그쪽 변경은 worker 도 재빌드(재무·공시 파서·딥다이브 등이 adapters/db 를 쓴다).
        api/app/domain/*|api/app/services/*|api/app/adapters/*|api/app/db/*|api/app/config.py|src/*) WANT_API=1; WANT_WORKER=1 ;;
        infra/*|pyproject.toml|uv.lock)               WANT_WORKER=1 ;;
        api/*)                                        WANT_API=1 ;;
      esac
    done <<< "$changed"
  fi
fi

targets=""
[[ $WANT_API -eq 1 ]]    && targets="$targets api"
[[ $WANT_WEB -eq 1 ]]    && targets="$targets web"
[[ $WANT_WORKER -eq 1 ]] && targets="$targets worker"
if [[ -z "$targets" ]]; then
  log "배포 대상 없음 — 종료."
  exit 0
fi
log "배포 대상:$targets"

# ── 배포 동작 ────────────────────────────────────────────────────────────
deploy_worker() {
  log "worker: docker 이미지 재빌드 + 재기동"
  ( cd infra && docker compose --env-file .env up -d --build reporter-worker )
  log "worker: 완료"
}

deploy_api() {
  log "api: uv sync + launchctl 재시작"
  ( cd api && uv sync )
  launchctl kickstart -k "gui/$UID_NUM/com.reporter.server.api"
  log "api: 완료"
}

deploy_web() {
  log "web: pnpm install + build + launchctl 재시작"
  ( cd web && pnpm install --frozen-lockfile && pnpm run build )
  launchctl kickstart -k "gui/$UID_NUM/com.reporter.server.web"
  log "web: 완료"
}

# worker → api → web 순(도메인 코드가 worker/api 공유이므로 서버 먼저 안정화).
[[ $WANT_WORKER -eq 1 ]] && deploy_worker
[[ $WANT_API -eq 1 ]]    && deploy_api
[[ $WANT_WEB -eq 1 ]]    && deploy_web

# ── 헬스체크 ─────────────────────────────────────────────────────────────
# launchctl kickstart 후 서비스(특히 web=pnpm start)가 뜨는 데 수 초 걸린다. 고정 sleep 은
# 타이밍에 취약해 정상 배포도 오탐 실패로 표시됐다 → 최대 ~30초 재시도 폴링으로 바꾼다.
# want_codes: 성공으로 인정할 HTTP 코드(공백구분). 하나라도 맞으면 OK.
_wait_http() {
  local label="$1" url="$2" want_codes="$3" code
  for _ in $(seq 1 15); do  # 2초 x 15 = 최대 30초
    code="$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo 000)"
    for want in $want_codes; do
      if [[ "$code" == "$want" ]]; then
        log "$label OK ($code)"
        return 0
      fi
    done
    sleep 2
  done
  warn "$label 헬스 실패 (last=$code)"
  return 1
}

log "헬스체크"
fail=0
[[ $WANT_API -eq 1 ]] && { _wait_http "api" "http://127.0.0.1:8010/api/screener?limit=1" "200" || fail=1; }
# web 은 로그인 게이트로 307 리다이렉트가 정상(200 도 허용).
[[ $WANT_WEB -eq 1 ]] && { _wait_http "web" "http://127.0.0.1:43000/" "200 307" || fail=1; }
if [[ $WANT_WORKER -eq 1 ]]; then
  status="$(docker inspect -f '{{.State.Status}}' reporter-worker 2>/dev/null || echo missing)"
  [[ "$status" == "running" ]] && log "worker OK ($status)" || { warn "worker 상태 이상 ($status)"; fail=1; }
fi

[[ $fail -eq 0 ]] && log "배포 성공 ✓" || die "배포 후 헬스체크 실패 — 로그 확인 필요"
