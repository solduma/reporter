"""브로드캐스트 스풀 아카이브 단위 테스트 — 파일 append·태깅·멱등키."""

from __future__ import annotations

import json
from pathlib import Path

from reporter import archive
from reporter.config import Config
from reporter.models import Report


def _config(tmp_path: Path) -> Config:
    return Config(
        ollama_host="https://ollama.com",
        ollama_api_key="key",
        summary_model="s",
        insight_model="i",
        telegram_bot_token="token",
        telegram_chat_id="123",
        root=tmp_path,
    )


def _read_spool(cfg: Config) -> list[dict]:
    lines = (cfg.logs_dir / "broadcasts.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(ln) for ln in lines if ln.strip()]


def test_record_appends_entry(tmp_path):
    cfg = _config(tmp_path)
    archive.record(cfg, "market_news", title="📰 뉴스", body="본문", ref_date="2026-07-09")

    entries = _read_spool(cfg)
    assert len(entries) == 1
    e = entries[0]
    assert e["kind"] == "market_news"
    assert e["ref_date"] == "2026-07-09"
    assert e["title"] == "📰 뉴스"
    assert e["dedup_key"].startswith("market_news|2026-07-09|")


def test_record_appends_multiple(tmp_path):
    cfg = _config(tmp_path)
    archive.record(cfg, "premarket", title="a", body="A")
    archive.record(cfg, "afternoon", title="b", body="B")
    assert len(_read_spool(cfg)) == 2


def test_dedup_key_stable_for_same_body():
    k1 = archive._dedup_key("digest_invest", "2026-07-09", "동일 본문")
    k2 = archive._dedup_key("digest_invest", "2026-07-09", "동일 본문")
    assert k1 == k2


def test_dedup_key_differs_for_different_body():
    k1 = archive._dedup_key("digest_invest", "2026-07-09", "본문 A")
    k2 = archive._dedup_key("digest_invest", "2026-07-09", "본문 B")
    assert k1 != k2


def test_record_entity_tags_company_stock_codes(tmp_path):
    cfg = _config(tmp_path)
    reports = [
        Report(category="company", title="t1", broker="A", date="26.07.09", views=1,
               stock_name="삼성전자", stock_code="005930", read_url="u1", pdf_url="p1"),
        Report(category="company", title="t2", broker="B", date="26.07.09", views=1,
               stock_name="삼성전자", stock_code="005930", read_url="u2"),
    ]
    archive.record_entity(cfg, "삼성전자", "company", "🏢 종목 브리핑 — 삼성전자", "종합 본문", reports)

    e = _read_spool(cfg)[0]
    assert e["kind"] == "per_entity"
    assert e["stock_codes"] == ["005930"]  # 중복 제거
    assert e["industries"] == []
    assert len(e["source_refs"]["reports"]) == 2
    assert e["source_refs"]["reports"][0]["url"] == "p1"  # pdf_url 우선


def test_record_entity_tags_industry(tmp_path):
    cfg = _config(tmp_path)
    reports = [
        Report(category="industry", title="반도체 업황", broker="A", date="26.07.09", views=1,
               industry="반도체", read_url="u1"),
    ]
    archive.record_entity(cfg, "반도체", "industry", "🏭 산업 브리핑 — 반도체", "본문", reports)

    e = _read_spool(cfg)[0]
    assert e["industries"] == ["반도체"]
    assert e["stock_codes"] == []


def test_record_digest_collects_sources(tmp_path):
    cfg = _config(tmp_path)
    sources = [
        Report(category="market_info", title="시황", broker="A", date="26.07.09", views=1,
               stock_code="000660", industry="반도체", read_url="u"),
    ]
    archive.record_digest(cfg, "digest_market", "📈 시황 종합", "본문", sources)

    e = _read_spool(cfg)[0]
    assert e["kind"] == "digest_market"
    assert e["stock_codes"] == ["000660"]
    assert e["industries"] == ["반도체"]
    assert len(e["source_refs"]["reports"]) == 1


def test_record_failure_is_swallowed(tmp_path, monkeypatch):
    # 스풀 기록 실패가 예외를 던지지 않아야 한다(발송은 이미 성공).
    cfg = _config(tmp_path)

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", _boom)
    archive.record(cfg, "market_news", title="t", body="b")  # 예외 없이 통과
