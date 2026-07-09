"""브로드캐스트 스풀 적재 단위 테스트 — 파싱 검증·스풀 회전·멱등 rowcount 집계.

DB 는 execute rowcount 만 필요하므로 가벼운 페이크 세션으로 대체(실 Postgres 미사용).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.db.models import BroadcastKind
from app.services import broadcast_ingest


@dataclass
class _FakeResult:
    """insert().returning() 결과 흉내 — 삽입 시 (id,) 행, 중복이면 None."""

    row: tuple | None

    def first(self):
        return self.row


@dataclass
class _FakeSession:
    """execute 마다 신규(id 반환)를 흉내내되, 지정 dedup_key 는 중복(None)으로 처리."""

    duplicates: set = field(default_factory=set)
    executed: list = field(default_factory=list)
    committed: bool = False
    rolled_back: bool = False
    _next_id: int = 1

    def execute(self, stmt):
        self.executed.append(stmt)
        # stmt 에서 dedup_key 를 꺼내 중복 여부 판단(compile 없이 파라미터 접근)
        params = stmt.compile().params
        key = params.get("dedup_key")
        if key in self.duplicates:
            return _FakeResult(row=None)
        self._next_id += 1
        return _FakeResult(row=(self._next_id,))

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _settings(spool: Path) -> Settings:
    return Settings(broadcast_spool=str(spool))


def _write_spool(spool: Path, entries: list[str]) -> None:
    spool.parent.mkdir(parents=True, exist_ok=True)
    spool.write_text("\n".join(entries) + "\n", encoding="utf-8")


_GOOD = (
    '{"kind":"per_entity","dedup_key":"per_entity|2026-07-09|abc",'
    '"ref_date":"2026-07-09","sent_at":"2026-07-09T09:30:00",'
    '"title":"🏢 종목 브리핑","body":"본문","stock_codes":["005930"],"industries":[]}'
)


def test_parse_entry_valid():
    import json

    v = broadcast_ingest._parse_entry(json.loads(_GOOD))
    assert v is not None
    assert v["kind"] == BroadcastKind.PER_ENTITY
    assert v["stock_codes"] == ["005930"]
    assert v["dedup_key"] == "per_entity|2026-07-09|abc"


def test_parse_entry_rejects_bad_kind():
    assert broadcast_ingest._parse_entry(
        {"kind": "nope", "dedup_key": "x", "ref_date": "2026-07-09", "sent_at": "2026-07-09T09:30:00"}
    ) is None


def test_parse_entry_rejects_missing_dedup():
    assert broadcast_ingest._parse_entry(
        {"kind": "per_entity", "ref_date": "2026-07-09", "sent_at": "2026-07-09T09:30:00"}
    ) is None


def test_parse_entry_rejects_bad_dates():
    assert broadcast_ingest._parse_entry(
        {"kind": "per_entity", "dedup_key": "x", "ref_date": "bad", "sent_at": "nope"}
    ) is None


def test_parse_entry_rejects_non_string_dates():
    # null·숫자 등 비문자열 날짜는 TypeError 를 유발 → None 으로 걸러 poison pill 을 막는다.
    assert broadcast_ingest._parse_entry(
        {"kind": "per_entity", "dedup_key": "x", "ref_date": None, "sent_at": 12345}
    ) is None


def test_ingest_empty_when_no_spool(tmp_path):
    db = _FakeSession()
    n = broadcast_ingest.ingest_broadcasts(db, _settings(tmp_path / "logs" / "broadcasts.jsonl"))
    assert n == 0
    assert not db.committed  # 아무 작업 없음


def test_ingest_counts_new_rows(tmp_path):
    spool = tmp_path / "logs" / "broadcasts.jsonl"
    _write_spool(spool, [_GOOD, _GOOD.replace("abc", "def")])
    db = _FakeSession()

    n = broadcast_ingest.ingest_broadcasts(db, _settings(spool))
    assert n == 2
    assert db.committed
    # 원본 스풀은 소비되고 processed 로 이동
    assert not spool.exists()
    assert (spool.parent / "broadcasts.processed.jsonl").exists()


def test_ingest_skips_duplicates(tmp_path):
    spool = tmp_path / "logs" / "broadcasts.jsonl"
    _write_spool(spool, [_GOOD, _GOOD.replace("abc", "def")])
    db = _FakeSession(duplicates={"per_entity|2026-07-09|abc"})

    n = broadcast_ingest.ingest_broadcasts(db, _settings(spool))
    assert n == 1  # 하나는 중복(rowcount=0)


def test_ingest_skips_malformed_lines(tmp_path):
    spool = tmp_path / "logs" / "broadcasts.jsonl"
    _write_spool(spool, ["not json", _GOOD, '{"kind":"bad"}'])
    db = _FakeSession()

    n = broadcast_ingest.ingest_broadcasts(db, _settings(spool))
    assert n == 1  # 유효한 1건만


def test_ingest_skips_poison_pill_typed_line(tmp_path):
    # 비문자열 날짜(null) 줄이 배치를 막지 않고 건너뛰어야 한다(commit 성공 → working 소비).
    spool = tmp_path / "logs" / "broadcasts.jsonl"
    poison = '{"kind":"per_entity","dedup_key":"p1","ref_date":null,"sent_at":null,"body":"x"}'
    _write_spool(spool, [poison, _GOOD])
    db = _FakeSession()

    n = broadcast_ingest.ingest_broadcasts(db, _settings(spool))
    assert n == 1  # 정상 1건, poison 은 건너뜀
    assert not spool.with_suffix(".jsonl.processing").exists()  # 배치 차단 없이 완료


def test_ingest_keeps_working_on_failure(tmp_path):
    # 실패 시 데이터는 .processing(working)에 남아 다음 실행이 회수한다(유실 방지).
    spool = tmp_path / "logs" / "broadcasts.jsonl"
    _write_spool(spool, [_GOOD])

    class _BoomSession(_FakeSession):
        def execute(self, stmt):
            raise RuntimeError("db down")

    db = _BoomSession()
    with contextlib.suppress(RuntimeError):
        broadcast_ingest.ingest_broadcasts(db, _settings(spool))

    working = spool.with_suffix(".jsonl.processing")
    assert working.exists()  # 데이터 보존
    assert _GOOD in working.read_text(encoding="utf-8")


def test_ingest_recovers_stale_working(tmp_path):
    # 이전 실행이 죽어 남긴 working 을 다음 실행이 회수하고, 그 사이 새 spool 도 합친다.
    spool = tmp_path / "logs" / "broadcasts.jsonl"
    working = spool.with_suffix(".jsonl.processing")
    working.parent.mkdir(parents=True, exist_ok=True)
    working.write_text(_GOOD + "\n", encoding="utf-8")  # stale
    _write_spool(spool, [_GOOD.replace("abc", "def")])  # 그 사이 CLI 가 새로 발송

    db = _FakeSession()
    n = broadcast_ingest.ingest_broadcasts(db, _settings(spool))

    assert n == 2  # stale 1건 + 신규 1건 모두 적재
    assert not spool.exists()
    assert not working.exists()
