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

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"

log()  { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[deploy] %s\033[0m\n' "$*" >&2; exit 1; }

# launchctl/pnpm/docker 를 self-hosted runner 환경에서도 찾도록 PATH 보강.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
UID_NUM="$(id -u)"

# ── 대상 결정 ────────────────────────────────────────────────────────────
declare -A WANT=([api]=0 [web]=0 [worker]=0)

if [[ $# -gt 0 ]]; then
  for t in "$@"; do
    case "$t" in
      api|web|worker) WANT[$t]=1 ;;
      *) die "알 수 없는 대상: $t (api|web|worker)" ;;
    esac
  done
  log "대상(명시): $*"
else
  base="${DEPLOY_BASE:-HEAD~1}"
  if ! git rev-parse --verify "$base" >/dev/null 2>&1; then
    warn "diff 기준 '$base' 없음 → 전체 배포로 폴백"
    WANT[api]=1; WANT[web]=1; WANT[worker]=1
  else
    changed="$(git diff --name-only "$base" HEAD)"
    log "변경 파일($base..HEAD):"; echo "$changed" | sed 's/^/    /'
    while IFS= read -r f; do
      [[ -z "$f" ]] && continue
      case "$f" in
        web/*)                              WANT[web]=1 ;;
        api/app/routers/*|api/app/schemas*) WANT[api]=1 ;;
        api/app/domain/*|api/app/services/*|src/*) WANT[api]=1; WANT[worker]=1 ;;
        infra/*|pyproject.toml|uv.lock)     WANT[worker]=1 ;;
        api/*)                              WANT[api]=1 ;;
      esac
    done <<< "$changed"
  fi
fi

targets=()
for t in api web worker; do [[ ${WANT[$t]} -eq 1 ]] && targets+=("$t"); done
if [[ ${#targets[@]} -eq 0 ]]; then
  log "배포 대상 없음 — 종료."
  exit 0
fi
log "배포 대상: ${targets[*]}"

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
[[ ${WANT[worker]} -eq 1 ]] && deploy_worker
[[ ${WANT[api]} -eq 1 ]]    && deploy_api
[[ ${WANT[web]} -eq 1 ]]    && deploy_web

# ── 헬스체크 ─────────────────────────────────────────────────────────────
log "헬스체크"
fail=0
if [[ ${WANT[api]} -eq 1 ]]; then
  sleep 6
  code="$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/api/screener?limit=1 || echo 000)"
  [[ "$code" == "200" ]] && log "api OK ($code)" || { warn "api 헬스 실패 ($code)"; fail=1; }
fi
if [[ ${WANT[web]} -eq 1 ]]; then
  sleep 2
  code="$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:43000/ || echo 000)"
  # 로그인 게이트로 307 리다이렉트가 정상.
  [[ "$code" == "200" || "$code" == "307" ]] && log "web OK ($code)" || { warn "web 헬스 실패 ($code)"; fail=1; }
fi
if [[ ${WANT[worker]} -eq 1 ]]; then
  status="$(docker inspect -f '{{.State.Status}}' reporter-worker 2>/dev/null || echo missing)"
  [[ "$status" == "running" ]] && log "worker OK ($status)" || { warn "worker 상태 이상 ($status)"; fail=1; }
fi

[[ $fail -eq 0 ]] && log "배포 성공 ✓" || die "배포 후 헬스체크 실패 — 로그 확인 필요"
