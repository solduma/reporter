"""종목 분석 LLM 코멘트 — 캐시 우선 + 백그라운드 생성.

llm_comment(Ollama)는 ~17초 걸려 동기 생성하면 분석 화면이 그만큼 멈춘다. 이 모듈은
축 점수·지표 입력의 해시로 캐시해 (1) 입력이 같으면 저장분 즉시 반환, (2) 없거나 바뀌면
BackgroundTask 로 생성·저장(응답은 comment=None·pending 으로 즉시). 프론트가 재조회해 채운다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.adapters.llm import get_llm
from app.config import get_settings
from app.db.models import AnalysisComment
from app.db.session import SessionLocal
from app.services import analysis

logger = logging.getLogger(__name__)

# 같은 (code, hash) 코멘트 생성이 동시에 여러 번 돌지 않도록 하는 인프로세스 가드.
_inflight: set[str] = set()
_inflight_lock = threading.Lock()


def inputs_hash(axes: list[dict]) -> str:
    """코멘트 입력을 결정적으로 직렬화해 해시한다. 입력이 바뀌면 캐시가 무효화된다.

    **축 점수(key→score)만** 해시한다. metrics 값에는 장중 실시간 지수 등락률처럼 초 단위로
    바뀌는 값이 섞여 있어, 그대로 해시하면 장중 내내 캐시가 계속 미스되어 ~17초 재생성을
    반복한다. 코멘트는 점수 기반 종합이라 점수가 안 바뀌면 재생성이 불필요하다. 프롬프트
    변경 시에도 재생성되도록 시스템 텍스트를 포함한다.
    """
    scores = {ax["key"]: ax.get("score") for ax in axes}
    payload = json.dumps(
        {"scores": scores, "sys": analysis._COMMENT_SYSTEM}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def get_cached(db, code: str, h: str) -> str | None:
    """입력 해시가 일치하는 저장 코멘트를 반환한다. 없거나 입력이 바뀌었으면 None."""
    row = db.scalar(select(AnalysisComment).where(AnalysisComment.stock_code == code))
    if row and row.inputs_hash == h and row.comment:
        return row.comment
    return None


def generate_and_store(code: str, name: str, axes: list[dict], h: str) -> None:
    """백그라운드: LLM 코멘트를 생성해 캐시에 upsert 한다. 자체 세션·예외 흡수.

    같은 (code, hash) 가 이미 생성 중이면 건너뛴다(중복 LLM 호출 방지).
    """
    key = f"{code}|{h}"
    with _inflight_lock:
        if key in _inflight:
            return
        _inflight.add(key)
    try:
        settings = get_settings()
        comment = analysis.llm_comment(
            get_llm(settings), settings.insight_model, name, axes
        )
        if not comment:
            return  # 키 없음·실패 → 캐시하지 않음(다음 조회에서 재시도)
        db = SessionLocal()
        try:
            stmt = insert(AnalysisComment).values(
                stock_code=code, inputs_hash=h, comment=comment, model=settings.insight_model
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_analysis_comment_code",
                set_={
                    "inputs_hash": stmt.excluded.inputs_hash,
                    "comment": stmt.excluded.comment,
                    "model": stmt.excluded.model,
                },
            )
            db.execute(stmt)
            db.commit()
            logger.info("analysis comment cached for %s (%s)", code, h)
        finally:
            db.close()
    except Exception as e:  # 백그라운드 생성 실패가 조회를 깨지 않도록
        logger.warning("analysis comment generate failed for %s: %s", code, e)
    finally:
        with _inflight_lock:
            _inflight.discard(key)
