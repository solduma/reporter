"""브로드캐스트 아카이브 스풀 — 발송한 텔레그램 메시지를 JSONL 로 append.

Postgres 는 API 가 단일 writer 이므로, CLI 는 발송 직후 stdlib 만으로 스풀 파일에
한 줄씩 남긴다. API 워커가 이 스풀을 읽어 `broadcast` 테이블에 멱등 적재한다.
발송이 진실의 원천이고 아카이브는 보조 기록이라, 기록 실패는 발송을 되돌리지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from .config import Config
from .models import Report

logger = logging.getLogger(__name__)

_SPOOL_NAME = "broadcasts.jsonl"


def _dedup_key(kind: str, ref_date: str, body: str) -> str:
    """재실행 멱등 키. 동일 콘텐츠 재발송은 같은 키(중복 적재 방지), 다른 콘텐츠는 새 키."""
    seq = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return f"{kind}|{ref_date}|{seq}"


def _report_ref(r: Report) -> dict:
    return {
        "broker": r.broker,
        "title": r.title,
        "url": r.pdf_url or r.read_url or "",
        "stock_code": r.stock_code,
        "stock_name": r.stock_name,
        "industry": r.industry,
    }


def record(
    config: Config,
    kind: str,
    *,
    title: str,
    body: str,
    ref_date: str | None = None,
    source_refs: dict | None = None,
    stock_codes: list[str] | None = None,
    industries: list[str] | None = None,
) -> None:
    """발송한 메시지 1건을 스풀에 append. 실패는 삼키고 로깅만 한다(발송은 이미 성공)."""
    try:
        now = datetime.now().astimezone()  # offset-aware: timestamptz 에 정확한 순간 저장
        day = ref_date or now.strftime("%Y-%m-%d")
        entry = {
            "kind": kind,
            "ref_date": day,
            "sent_at": now.isoformat(timespec="seconds"),
            "title": title,
            "body": body,
            "source_refs": source_refs or {},
            "stock_codes": sorted({c for c in (stock_codes or []) if c}),
            "industries": sorted({i for i in (industries or []) if i}),
            "dedup_key": _dedup_key(kind, day, body),
        }
        line = json.dumps(entry, ensure_ascii=False)
        with (config.logs_dir / _SPOOL_NAME).open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:  # 아카이브 실패가 발송 파이프라인을 깨뜨리지 않도록
        logger.warning("broadcast archive 기록 실패 (%s): %s", kind, e)


def record_entity(
    config: Config, entity: str, category: str, title: str, body: str, reports: list[Report]
) -> None:
    """종목/산업 단위 브리핑 기록 — 그룹의 리포트에서 종목코드·산업을 태깅한다."""
    stock_codes = [r.stock_code for r in reports if r.stock_code]
    industries = (
        [entity] if category == "industry" else [r.industry for r in reports if r.industry]
    )
    record(
        config,
        "per_entity",
        title=title,
        body=body,
        source_refs={"reports": [_report_ref(r) for r in reports]},
        stock_codes=stock_codes,
        industries=industries,
    )


def record_digest(config: Config, kind: str, title: str, body: str, sources: list[Report]) -> None:
    """카테고리 종합 기록 — 인용 소스(상위 리포트)를 source_refs 에 담는다."""
    record(
        config,
        kind,
        title=title,
        body=body,
        source_refs={"reports": [_report_ref(r) for r in sources]},
        stock_codes=[r.stock_code for r in sources if r.stock_code],
        industries=[r.industry for r in sources if r.industry],
    )
