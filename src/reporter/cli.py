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
from .config import load_config
from .models import BATCHES
from .pipeline import run_morning_briefing
from .telegram import resolve_chat_ids


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reporter", description="증권 리포트 텔레그램 브리핑")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--batch", type=int, choices=sorted(BATCHES), help="카테고리 묶음 (1~4)")
    group.add_argument("--all", action="store_true", help="전체 카테고리 오전 브리핑")
    group.add_argument("--afternoon", action="store_true", help="오후 능동 리서치")
    group.add_argument("--reset-log", action="store_true", help="당일 브리핑 로그 초기화")
    group.add_argument("--chat-id", action="store_true", help="getUpdates 로 텔레그램 chat_id 조회")
    parser.add_argument("--top-n", type=int, default=5, help="카테고리별 선별 개수 (기본 5)")
    args = parser.parse_args(argv)

    _setup_logging()
    config = load_config()

    if args.reset_log:
        (config.logs_dir / "today_briefing.txt").write_text("", encoding="utf-8")
        return 0

    if args.chat_id:
        found = resolve_chat_ids(config.telegram_bot_token)
        if not found:
            print("업데이트가 없습니다. 텔레그램에서 봇과 대화를 시작하고 메시지를 보낸 뒤 다시 실행하세요.")
        for cid, name in found:
            print(f"chat_id={cid}  ({name})")
        return 0

    if args.afternoon:
        run_afternoon_research(config)
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
