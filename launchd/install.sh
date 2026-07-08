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

# 이전 버전에서 설치했던 레거시 라벨(uninstall 시 함께 정리).
LEGACY_SUFFIXES=(batch1 batch2 batch3 batch4)

uninstall() {
  local suffixes=()
  for job in "${JOBS[@]}"; do suffixes+=("${job%%|*}"); done
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

echo ""
echo "완료. 재부팅/재로그인 후에도 자동 유지됩니다."
echo "확인:   launchctl print gui/$(id -u)/$LABEL_PREFIX.perentity | grep -A3 state"
echo "목록:   launchctl list | grep $LABEL_PREFIX"
echo "제거:   $0 uninstall"
