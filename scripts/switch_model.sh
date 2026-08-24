#!/bin/bash
# LLM 엔드포인트·모델 스위치 — reporter 의 LLM 설정(.env 3곳)을 프리셋으로 일괄 교체한다.
#
#   scripts/switch_model.sh --muse          # OpenCode Zen muse-spark-1.2-contributor-free
#   scripts/switch_model.sh --ox            # OpenCode Zen x-preview-f-free (Ox Alpha Free)
#   scripts/switch_model.sh --ollama        # Ollama Cloud 프록시 qwen3.5:cloud (기존)
#   scripts/switch_model.sh --status        # 현재 설정 + 연결 테스트
#
# 클라이언트(src/reporter/ollama_client.py)는 OpenAI 호환 POST {OLLAMA_HOST}/v1/chat/completions
# 으로 통일돼 있으므로, 전환은 값 교체만으로 끝난다.
#
# 적용 경로:
#   루트 .env   : OLLAMA_HOST · OLLAMA_API_KEY · OLLAMA_SUMMARY_MODEL · OLLAMA_INSIGHT_MODEL (CLI)
#   api/.env    : OLLAMA_HOST · OLLAMA_API_KEY · SUMMARY_MODEL · INSIGHT_MODEL (API 서버)
#   infra/.env  : 동일 키 (docker worker — 재생성 필요, --worker)
#
# 재시작:
#   CLI      : 다음 실행부터 자동 반영
#   API      : --restart 로 launchctl kickstart (또는 수동)
#   worker   : --worker 로 docker compose up -d --build (라이브 영향 — 명시적으로만)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_ROOT="$ROOT_DIR/.env"
ENV_API="$ROOT_DIR/api/.env"
ENV_INFRA="$ROOT_DIR/infra/.env"
AUTH_JSON="$HOME/.local/share/opencode/auth.json"

ZEN_HOST="https://opencode.ai/zen"
OLLAMA_HOST_LOCAL="http://127.0.0.1:43187"     # launchd API·CLI 가 도달하는 로컬 프록시
OLLAMA_HOST_CONTAINER="http://socat-bridge:43188"  # worker 컨테이너 전용(socat 브리지)

log()  { printf '\033[1;36m[switch]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[switch]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[switch] %s\033[0m\n' "$*" >&2; exit 1; }

show_help() {
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# .env 키 치환(없으면 추가). macOS bash/sed 호환.
update_env() {
    local file="$1" key="$2" value="$3"
    [ -f "$file" ] || touch "$file"
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i '' "s|^${key}=.*|${key}=${value}|" "$file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}

backup_env() {
    local file="$1"
    [ -f "$file" ] || return 0
    cp "$file" "${file}.bak-switch"
}

zen_key() {
    python3 - <<PY 2>/dev/null
import json
print(json.load(open("$AUTH_JSON"))["opencode"]["key"])
PY
}

mask() {
    local k="$1"
    if [ ${#k} -ge 12 ]; then
        echo "${k:0:6}...${k: -4}"
    else
        echo "$k"
    fi
}

apply_mode() {
    local label="$1" model="$2" host_api="$3" host_infra="$4" key="$5"
    log "[$label] 전환 중… (model=$model)"
    backup_env "$ENV_ROOT"; backup_env "$ENV_API"; backup_env "$ENV_INFRA"

    # 폴백 프로바이더(api 서버 전용) — 주 모델 rate limit 시 ResilientLLMAdapter 가 자동 전환.
    if [ "$MODE" = "ollama" ]; then
        FB_HOST="$ZEN_HOST"; FB_KEY="$(zen_key || true)"; FB_MODEL="muse-spark-1.2-contributor-free"
    else
        FB_HOST="$OLLAMA_HOST_LOCAL"; FB_KEY="dummy"; FB_MODEL="qwen3.5:cloud"
    fi

    update_env "$ENV_ROOT"  "OLLAMA_HOST"           "$host_api"
    update_env "$ENV_ROOT"  "OLLAMA_API_KEY"        "$key"
    update_env "$ENV_ROOT"  "OLLAMA_SUMMARY_MODEL"  "$model"
    update_env "$ENV_ROOT"  "OLLAMA_INSIGHT_MODEL"  "$model"

    update_env "$ENV_API"   "OLLAMA_HOST"           "$host_api"
    update_env "$ENV_API"   "OLLAMA_API_KEY"        "$key"
    update_env "$ENV_API"   "SUMMARY_MODEL"         "$model"
    update_env "$ENV_API"   "INSIGHT_MODEL"         "$model"

    update_env "$ENV_INFRA" "OLLAMA_HOST"           "$host_infra"
    update_env "$ENV_INFRA" "OLLAMA_API_KEY"        "$key"
    update_env "$ENV_INFRA" "SUMMARY_MODEL"         "$model"
    update_env "$ENV_INFRA" "INSIGHT_MODEL"         "$model"

    # 폴백(api/.env 만 — API 서버가 읽는다. worker·CLI는 미적용)
    if [ -n "$FB_HOST" ] && [ -n "$FB_KEY" ]; then
        update_env "$ENV_API" "FALLBACK_OLLAMA_HOST"      "$FB_HOST"
        update_env "$ENV_API" "FALLBACK_OLLAMA_API_KEY"   "$FB_KEY"
        update_env "$ENV_API" "FALLBACK_SUMMARY_MODEL"    "$FB_MODEL"
        update_env "$ENV_API" "FALLBACK_INSIGHT_MODEL"    "$FB_MODEL"
        log "폴백 프로바이더 기록: $FB_HOST ($FB_MODEL)"
    fi

    log ".env 3곳 갱신 완료 (루트·api·infra)"
}

read_env_val() {
    # 파일에서 첫 번째 KEY= 값을 읽는다(없으면 빈 문자열).
    grep -E "^$2=" "$1" 2>/dev/null | head -1 | cut -d= -f2-
}

show_status() {
    echo "=============================================="
    echo "📊 현재 LLM 설정"
    echo ""
    for entry in "루트 .env|$ENV_ROOT" "api/.env|$ENV_API" "infra/.env|$ENV_INFRA"; do
        name="${entry%%|*}"; f="${entry##*|}"
        echo "---- $name ($f) ----"
        if [ ! -f "$f" ]; then echo "  (파일 없음)"; continue; fi
        grep -E "^OLLAMA_HOST=|^OLLAMA_API_KEY=|^SUMMARY_MODEL=|^INSIGHT_MODEL=|^OLLAMA_SUMMARY_MODEL=|^OLLAMA_INSIGHT_MODEL=" "$f" \
            | sed -E 's/^(OLLAMA_API_KEY=)(.{6}).*(.{4})$/\1\2...\3/' | sed 's/^/  /'
    done
    echo ""

    # api/.env 기준 실연결 테스트
    local host key model url code body
    host="$(read_env_val "$ENV_API" OLLAMA_HOST)"
    key="$(read_env_val "$ENV_API" OLLAMA_API_KEY)"
    model="$(read_env_val "$ENV_API" SUMMARY_MODEL)"
    echo "---- 연결 테스트 (api/.env 기준) ----"
    if [ -z "$host" ] || [ -z "$model" ]; then
        warn "OLLAMA_HOST 또는 SUMMARY_MODEL 없음 — 테스트 생략"
        return 0
    fi
    url="${host%/}/v1/chat/completions"
    body="$(curl -sS -m 60 -w '\n%{http_code}' "$url" \
        -H "Authorization: Bearer $key" -H "Content-Type: application/json" \
        -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":200}" 2>&1)" || true
    code="$(printf '%s' "$body" | tail -1)"
    if [ "$code" = "200" ]; then
        log "✅ HTTP $code — $url (model=$model)"
    else
        warn "❌ HTTP ${code:-??} — $(printf '%s' "$body" | head -c 300)"
    fi
}

MODE=""
RESTART=0
WORKER=0

for arg in "$@"; do
    case "$arg" in
        --muse)   MODE="muse" ;;
        --ox)     MODE="ox" ;;
        --ollama) MODE="ollama" ;;
        --status) MODE="status" ;;
        --restart) RESTART=1 ;;
        --worker) WORKER=1 ;;
        --help|-h|help) show_help; exit 0 ;;
        *) die "알 수 없는 인자: $arg (--help 참조)" ;;
    esac
done

[ -n "$MODE" ] || { show_help; exit 1; }

if [ "$MODE" = "status" ]; then
    show_status
    exit 0
fi

case "$MODE" in
    muse)
        KEY="$(zen_key)" || die "auth.json 에서 opencode 키를 읽지 못함: $AUTH_JSON"
        apply_mode "OpenCode Zen · Muse Spark 1.2 Free" "muse-spark-1.2-contributor-free" \
            "$ZEN_HOST" "$ZEN_HOST" "$KEY"
        ;;
    ox)
        KEY="$(zen_key)" || die "auth.json 에서 opencode 키를 읽지 못함: $AUTH_JSON"
        apply_mode "OpenCode Zen · Ox Alpha Free" "x-preview-f-free" \
            "$ZEN_HOST" "$ZEN_HOST" "$KEY"
        ;;
    ollama)
        apply_mode "Ollama Cloud (프록시)" "qwen3.5:cloud" \
            "$OLLAMA_HOST_LOCAL" "$OLLAMA_HOST_CONTAINER" "dummy"
        ;;
esac

echo ""
show_status

if [ "$RESTART" = "1" ]; then
    log "API 서버 재시작 (launchctl kickstart com.reporter.server.api)…"
    launchctl kickstart -k "gui/$(id -u)/com.reporter.server.api"
else
    warn "API 서버에 반영하려면: scripts/switch_model.sh <모드> --restart (또는 launchctl kickstart)"
fi

if [ "$WORKER" = "1" ]; then
    warn "worker 재빌드·재생성은 라이브 배치에 영향 — 실행합니다 (infra/docker-compose)"
    (cd "$ROOT_DIR/infra" && docker compose --env-file .env up -d --build reporter-worker)
else
    warn "worker(docker)는 코드 내장형이라 재생성 필요: cd infra && docker compose --env-file .env up -d --build reporter-worker"
fi

log "web 은 프록시만 하므로 재시작 불필요. CLI 는 다음 실행부터 자동 반영."
