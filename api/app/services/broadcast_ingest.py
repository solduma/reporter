"""브로드캐스트 스풀 적재 — CLI 가 남긴 logs/broadcasts.jsonl 을 broadcast 테이블로.

CLI(텔레그램 발송)와 API(DB 적재)는 별개 프로세스다. CLI 는 발송 직후 스풀에 append 하고,
API 워커/트리거가 이 서비스로 스풀을 읽어 멱등 적재한다(Postgres 단일 writer = API).

경합 방지: 스풀을 먼저 처리용 이름으로 원자적 rename 한 뒤 읽는다. rename 이후의 CLI append
는 새 스풀로 가므로 유실·중복 처리가 없다. 적재는 dedup_key UNIQUE + on_conflict_do_nothing.
호출자가 셋(스케줄러·admin·TUI)이라 동시 실행을 flock 논블로킹 락으로 직렬화한다(겹치면 skip).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Broadcast, BroadcastKind

logger = logging.getLogger(__name__)

# api/app/services/broadcast_ingest.py → parents[3] = repo root (CLI 의 logs 와 동일 위치)
_REPO_ROOT = Path(__file__).resolve().parents[3]
_VALID_KINDS = {k.value for k in BroadcastKind}


def _spool_path(settings: Settings) -> Path:
    return Path(settings.broadcast_spool) if settings.broadcast_spool else _REPO_ROOT / "logs" / "broadcasts.jsonl"


@contextlib.contextmanager
def _ingest_lock(spool: Path) -> Iterator[bool]:
    """스풀 처리 구간을 프로세스 간 직렬화한다(논블로킹). 이미 잡혀 있으면 False."""
    spool.parent.mkdir(parents=True, exist_ok=True)
    lock_path = spool.with_suffix(".jsonl.lock")
    fd = lock_path.open("w")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False  # 다른 ingest 가 처리 중 → 이번 회차는 건너뛴다
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        fd.close()


def _parse_entry(raw: dict) -> dict | None:
    """스풀 한 줄(dict)을 broadcast 컬럼 dict 로 변환. 형식 불량이면 None."""
    kind = raw.get("kind")
    dedup_key = raw.get("dedup_key")
    if kind not in _VALID_KINDS or not dedup_key:
        return None
    try:
        ref_date = date.fromisoformat(raw["ref_date"])
        sent_at = datetime.fromisoformat(raw["sent_at"])
    except (KeyError, ValueError, TypeError):
        # TypeError: ref_date/sent_at 가 문자열이 아닌 값(null·숫자)이면 fromisoformat 이 던진다.
        # 잡지 않으면 오염된 한 줄이 배치 전체를 영구 차단(working 보존 → 매 실행 재크래시).
        return None
    return {
        "kind": BroadcastKind(kind),
        "ref_date": ref_date,
        "sent_at": sent_at,
        "title": raw.get("title", ""),
        "body": raw.get("body", ""),
        "source_refs": raw.get("source_refs") or {},
        "stock_codes": raw.get("stock_codes") or [],
        "industries": raw.get("industries") or [],
        "dedup_key": dedup_key,
    }


def ingest_broadcasts(db: Session, settings: Settings) -> int:
    """스풀을 적재한다. 새로 저장된(중복 아닌) 브로드캐스트 수를 반환한다.

    동시 호출(스케줄러·admin·TUI)은 flock 으로 직렬화하고, 겹치면 이번 회차는 0 을 반환한다.
    """
    spool = _spool_path(settings)
    with _ingest_lock(spool) as acquired:
        if not acquired:
            logger.info("다른 broadcast ingest 가 처리 중 — 이번 회차 건너뜀")
            return 0
        return _ingest_locked(db, spool)


def _ingest_locked(db: Session, spool: Path) -> int:
    working = spool.with_suffix(".jsonl.processing")

    # 처리 대상을 working 으로 확보한다.
    # - 이전 실행이 중간에 죽어 stale working 이 있으면 그것부터 회수하고, 그 사이 CLI 가
    #   새로 쓴 spool 은 뒤에 이어붙인다(dedup_key 로 재처리 안전 → 유실 방지).
    # - stale 이 없으면 spool 을 원자적 rename(처리 중 CLI append 격리).
    if working.exists():
        if spool.exists():
            with working.open("a", encoding="utf-8") as w:
                w.write(spool.read_text(encoding="utf-8"))
            spool.unlink(missing_ok=True)
    elif spool.exists() and spool.stat().st_size > 0:
        spool.rename(working)
    else:
        return 0

    saved = 0
    try:
        for line in working.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                values = _parse_entry(json.loads(line))
            except json.JSONDecodeError:
                values = None
            if values is None:
                logger.warning("broadcast 스풀 줄 파싱 실패(건너뜀): %.120s", line)
                continue
            # RETURNING id: DO NOTHING 은 실제 삽입된 행만 반환하므로 드라이버 무관하게
            # 신규 건수를 정확히 센다(psycopg 의 rowcount 는 ON CONFLICT 에서 -1 을 줄 수 있음).
            stmt = (
                insert(Broadcast)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_broadcast_dedup")
                .returning(Broadcast.id)
            )
            if db.execute(stmt).first() is not None:
                saved += 1
        db.commit()
    except Exception:
        db.rollback()
        # 실패 시 그 사이 CLI 가 쓴 신규 spool 을 덮어쓰지 않도록 working 을 재시도용으로 남긴다
        # (다음 실행이 회수). append 되돌리기를 하지 않아 신규 spool 유실도 없다.
        raise

    # 성공분은 감사·디버깅용으로 누적 보관(재적재해도 dedup 로 무해).
    with (spool.parent / "broadcasts.processed.jsonl").open("a", encoding="utf-8") as f:
        f.write(working.read_text(encoding="utf-8"))
    working.unlink(missing_ok=True)

    logger.info("ingested %d new broadcasts from spool", saved)
    return saved
