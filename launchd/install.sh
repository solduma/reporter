#!/bin/bash
# launchd LaunchAgent 설치 — 한 번 실행하면 재부팅/재로그인 후에도 자동 유지된다.
# cron 과 달리 예약 시각에 슬립 중이었으면 깨어날 때 놓친 작업을 1회 실행한다.
#
#   ./launchd/install.sh          # 설치 (재실행 시 자동 갱신)
#   ./launchd/install.sh uninstall
#
# 라벨 규칙: com.reporter.<job>

set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$PROJECT/.venv/bin/python"
AGENTS="$HOME/Library/LaunchAgents"
LABEL_PREFIX="com.reporter"

# launchd 는 로그인 셸 PATH 를 상속하지 않으므로, 서버 서비스가 uv/pnpm/node 를 찾도록
# 실제 바이너리 위치를 현재 셸에서 찾아 PATH 에 합친다.
build_service_path() {
  local dirs="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
  local bin
  for tool in uv pnpm node; do
    bin="$(command -v "$tool" 2>/dev/null || true)"
    [[ -n "$bin" ]] && dirs="$(dirname "$bin"):$dirs"
  done
  echo "$dirs"
}
SERVICE_PATH="$(build_service_path)"

# job: 라벨접미사 | 시(hour) | 분(minute) | reporter 인자
# 월~금(Weekday 1-5) 만 실행.
# 07:00 미장마감+간밤뉴스 → 09:00 로그초기화 → 09:30 종목/산업 개별 →
# 10:00 시황/투자 종합 → 10:30 경제 → 11:00 채권 → 14:00 오후리서치 →
# 17:00 마감시황 / 09~16시 매시 장중뉴스.
JOBS=(
  "premarket|7|0|--premarket"
  "reset|9|0|--reset-log"
  "perentity|9|30|--per-entity"
  "digest_market|10|0|--digest market_info"
  "digest_invest|10|0|--digest invest"
  "digest_econ|10|30|--digest economy"
  "digest_bond|11|0|--digest debenture"
  "afternoon|14|0|--afternoon"
  "closing|17|0|--closing"
  "news09|9|0|--news"
  "news10|10|0|--news"
  "news11|11|0|--news"
  "news12|12|0|--news"
  "news13|13|0|--news"
  "news14|14|0|--news"
  "news15|15|0|--news"
  "news16|16|0|--news"
)

# 상시 실행 서버(웹/API) — 예약(cron) 잡과 달리 RunAtLoad+KeepAlive 로 항상 떠 있게 한다.
# label 접미사 | 서브디렉터리 | 실행커맨드(공백구분) | 사전조건파일(없으면 등록 스킵)
SERVERS=(
  "server.api|api|uv run uvicorn app.main:app --host 127.0.0.1 --port 8010|"
  "server.web|web|pnpm start -p 43000|web/.next/BUILD_ID"
)

# 이전 버전에서 설치했던 레거시 라벨(uninstall 시 함께 정리).
LEGACY_SUFFIXES=(batch1 batch2 batch3 batch4)

uninstall() {
  local suffixes=()
  for job in "${JOBS[@]}"; do suffixes+=("${job%%|*}"); done
  for srv in "${SERVERS[@]}"; do suffixes+=("${srv%%|*}"); done
  suffixes+=("${LEGACY_SUFFIXES[@]}")
  for suffix in "${suffixes[@]}"; do
    label="$LABEL_PREFIX.$suffix"
    plist="$AGENTS/$label.plist"
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    rm -f "$plist"
    echo "removed $label"
  done
  echo "완료: 모든 reporter LaunchAgent 제거"
}

install_job() {
  local suffix="$1" hour="$2" minute="$3" args="$4"
  local label="$LABEL_PREFIX.$suffix"
  local plist="$AGENTS/$label.plist"

  # 월~금(1-5) 각각에 대한 StartCalendarInterval 항목 생성
  local intervals=""
  for wd in 1 2 3 4 5; do
    intervals+="
    <dict>
      <key>Weekday</key><integer>$wd</integer>
      <key>Hour</key><integer>$hour</integer>
      <key>Minute</key><integer>$minute</integer>
    </dict>"
  done

  # args 를 <string> 배열로 전개
  local arg_strings=""
  for a in $args; do
    arg_strings+="
    <string>$a</string>"
  done

  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-m</string>
    <string>reporter.cli</string>$arg_strings
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT</string>
  <key>StartCalendarInterval</key>
  <array>$intervals
  </array>
  <key>StandardOutPath</key>
  <string>$PROJECT/logs/launchd.log</string>
  <key>StandardErrorPath</key>
  <string>$PROJECT/logs/launchd.log</string>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
PLIST

  # 기존 등록이 있으면 교체 후 재등록
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
  echo "installed $label  ($hour:$(printf '%02d' "$minute"), 월~금, reporter $args)"
}

install_server() {
  local suffix="$1" subdir="$2" cmd="$3" precond="$4"
  local label="$LABEL_PREFIX.$suffix"
  local plist="$AGENTS/$label.plist"
  local workdir="$PROJECT/$subdir"

  if [[ -n "$precond" && ! -e "$PROJECT/$precond" ]]; then
    echo "skip $label — 사전조건 없음 ($precond). web 은 'cd web && pnpm build' 필요."
    return 0
  fi

  # 실행 커맨드(공백구분)를 <string> 배열로 전개.
  # 중요: launchd 는 argv[0] 를 EnvironmentVariables.PATH 가 아닌 _PATH_STDPATH
  # (/usr/bin:/bin:/usr/sbin:/sbin)로만 해석한다. uv/pnpm 은 그 밖에 있으므로 첫 토큰(실행파일)은
  # 반드시 절대경로로 바꿔 넣는다. (uv/pnpm 이 자식으로 부르는 node 등은 아래 PATH 로 찾는다.)
  read -r -a _cmd_arr <<< "$cmd"
  local prog="${_cmd_arr[0]}"
  local abs_prog
  abs_prog="$(command -v "$prog" 2>/dev/null || true)"
  if [[ -z "$abs_prog" ]]; then
    echo "skip $label — 실행파일 '$prog' 을 PATH 에서 찾지 못함. 설치 후 다시 시도하세요." >&2
    return 0
  fi
  _cmd_arr[0]="$abs_prog"
  local prog_strings=""
  for a in "${_cmd_arr[@]}"; do
    prog_strings+="
    <string>$a</string>"
  done

  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>$prog_strings
  </array>
  <key>WorkingDirectory</key>
  <string>$workdir</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$SERVICE_PATH</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$PROJECT/logs/server_$(echo "$suffix" | sed 's/server\.//').log</string>
  <key>StandardErrorPath</key>
  <string>$PROJECT/logs/server_$(echo "$suffix" | sed 's/server\.//').log</string>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
PLIST

  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
  echo "installed $label  (RunAtLoad+KeepAlive, cwd=$subdir, $cmd)"
}

if [[ "${1:-}" == "uninstall" ]]; then
  uninstall
  exit 0
fi

if [[ ! -x "$PY" ]]; then
  echo "에러: $PY 가 없습니다. 먼저 'uv sync' 로 venv 를 만드세요." >&2
  exit 1
fi

mkdir -p "$AGENTS" "$PROJECT/logs"

# 설치 시 레거시 라벨(구 batch1~4 등)을 먼저 정리해 중복 발송을 막는다.
for suffix in "${LEGACY_SUFFIXES[@]}"; do
  label="$LABEL_PREFIX.$suffix"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  rm -f "$AGENTS/$label.plist"
done

for job in "${JOBS[@]}"; do
  IFS='|' read -r suffix hour minute args <<< "$job"
  install_job "$suffix" "$hour" "$minute" "$args"
done

# 상시 실행 서버(웹/API)
for srv in "${SERVERS[@]}"; do
  IFS='|' read -r suffix subdir cmd precond <<< "$srv"
  install_server "$suffix" "$subdir" "$cmd" "$precond"
done

echo ""
echo "완료. 재부팅/재로그인 후에도 자동 유지됩니다."
echo "확인:   launchctl print gui/$(id -u)/$LABEL_PREFIX.perentity | grep -A3 state"
echo "목록:   launchctl list | grep $LABEL_PREFIX"
echo "제거:   $0 uninstall"
