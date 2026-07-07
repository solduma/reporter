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

# job: 라벨접미사 | 시(hour) | 분(minute) | reporter 인자
# 월~금(Weekday 1-5) 만 실행. 09:00 로그 초기화 → 배치 순차 → 14:00 오후 리서치.
JOBS=(
  "reset|9|0|--reset-log"
  "batch1|9|30|--batch 1"
  "batch2|10|0|--batch 2"
  "batch3|10|30|--batch 3"
  "batch4|11|0|--batch 4"
  "afternoon|14|0|--afternoon"
)

uninstall() {
  for job in "${JOBS[@]}"; do
    suffix="${job%%|*}"
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

if [[ "${1:-}" == "uninstall" ]]; then
  uninstall
  exit 0
fi

if [[ ! -x "$PY" ]]; then
  echo "에러: $PY 가 없습니다. 먼저 'uv sync' 로 venv 를 만드세요." >&2
  exit 1
fi

mkdir -p "$AGENTS" "$PROJECT/logs"
for job in "${JOBS[@]}"; do
  IFS='|' read -r suffix hour minute args <<< "$job"
  install_job "$suffix" "$hour" "$minute" "$args"
done

echo ""
echo "완료. 재부팅/재로그인 후에도 자동 유지됩니다."
echo "확인:   launchctl print gui/$(id -u)/$LABEL_PREFIX.batch1 | grep -A3 state"
echo "목록:   launchctl list | grep $LABEL_PREFIX"
echo "제거:   $0 uninstall"
