"""CLI 진입점.

  reporter --batch 1        # 오전 브리핑 (batch 1~4 카테고리 묶음)
  reporter --all            # 오전 브리핑 (전체 카테고리)
  reporter --afternoon      # 오후 능동 리서치
  reporter --reset-log      # 당일 브리핑 로그 초기화
"""

from __future__ import annotations

import argparse
import logging
import sys

from .afternoon import run_afternoon_research
from .config import Config, load_config
from .models import BATCHES
from .pipeline import run_morning_briefing, run_per_report_briefing
from .telegram import resolve_chat_ids

# 모드별로 실제 사용하는 env. 존재하지 않는 값으로 API 를 호출하기 전에 미리 검증한다.
_OLLAMA = ("ollama_api_key",)
_TELEGRAM = ("telegram_bot_token", "telegram_chat_id")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _require(config: Config, *fields: str) -> int:
    """필수 env 누락 시 안내를 출력하고 비정상 종료 코드를, 충족 시 0 을 반환한다."""
    missing = config.missing(*fields)
    if missing:
        print(
            f"환경변수가 설정되지 않았습니다: {', '.join(missing)}\n"
            f".env 를 확인하세요 (.env.example 참고).",
            file=sys.stderr,
        )
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reporter", description="증권 리포트 텔레그램 브리핑")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--batch", type=int, choices=sorted(BATCHES), help="카테고리 묶음 (1~4)")
    group.add_argument("--all", action="store_true", help="전체 카테고리 오전 브리핑")
    group.add_argument("--afternoon", action="store_true", help="오후 능동 리서치")
    group.add_argument(
        "--per-report",
        type=int,
        choices=sorted(BATCHES),
        metavar="BATCH",
        help="해당 batch 리포트를 리포트당 1건씩 개별 요약 발송",
    )
    group.add_argument("--reset-log", action="store_true", help="당일 브리핑 로그 초기화")
    group.add_argument("--chat-id", action="store_true", help="getUpdates 로 텔레그램 chat_id 조회")
    parser.add_argument("--top-n", type=int, default=5, help="카테고리별 선별 개수 (기본 5)")
    parser.add_argument(
        "--date", help="크롤 대상 날짜 YY.MM.DD (기본: 오늘). 과거 발행분 발송용"
    )
    args = parser.parse_args(argv)

    _setup_logging()
    config = load_config()

    if args.reset_log:
        (config.logs_dir / "today_briefing.txt").write_text("", encoding="utf-8")
        return 0

    if args.chat_id:
        if err := _require(config, "telegram_bot_token"):
            return err
        found = resolve_chat_ids(config.telegram_bot_token)
        if not found:
            print("업데이트가 없습니다. 텔레그램에서 봇과 대화를 시작하고 메시지를 보낸 뒤 다시 실행하세요.")
        for cid, name in found:
            print(f"chat_id={cid}  ({name})")
        return 0

    if err := _require(config, *_OLLAMA, *_TELEGRAM):
        return err

    if args.afternoon:
        run_afternoon_research(config)
        return 0

    if args.per_report is not None:
        sent = run_per_report_briefing(config, BATCHES[args.per_report], target_date=args.date)
        if sent == 0:
            print("발송할 리포트가 없습니다.", file=sys.stderr)
        return 0

    categories = (
        [c for cats in BATCHES.values() for c in cats] if args.all else BATCHES[args.batch]
    )
    result = run_morning_briefing(config, categories, top_n=args.top_n)
    if result is None:
        print("발송할 리포트가 없습니다.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
