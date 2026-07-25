"""사업 개요 라우터 — 종목별 사업 개요 조회(cache-aside) + 단건 갱신 트리거.

데이터 접근·추출·조립은 services/business_ingest 가 담당. 라우터는 캐시를 먼저 보고(12h TTL),
miss 시 동기 조립 후 저장·반환(cache-aside). POST /refresh 는 단건 재조립(백필 진입점).
공시 → DB → Cache 흐름의 응답 edge.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_session
from app.schemas import BusinessOverviewOut
from app.services import business_ingest

router = APIRouter(prefix="/api/companies", tags=["business"])


def _to_out(payload: dict) -> BusinessOverviewOut:
    return BusinessOverviewOut.model_validate(payload)


@router.get("/{code}/business", response_model=BusinessOverviewOut | None)
def get_business_overview(
    code: str,
    db: Session = Depends(get_session),
    response: Response = Response(),
) -> BusinessOverviewOut | None:
    """종목 사업 개요 — 캐시 우선(12h TTL), miss 시 동기 조립 후 저장(cache-aside).

    사업보고서가 없거나 LLM 미설정으로 미조립이면 null. 응답 캐시 5분(원문 갱신은 배치 단위).
    """
    cached = business_ingest.get_cached_overview(db, code)
    if cached is not None:
        response.headers["Cache-Control"] = "public, max-age=300"
        return _to_out(cached)
    # miss — 동기 조립(백필 진입점과 동일). 느릴 수 있어 1회만.
    settings = get_settings()
    try:
        payload = business_ingest.assemble_overview(db, settings, code)
    except Exception as e:  # 외부 IO(DART·LLM) 경계 방어 — 502 로 명확히.
        raise HTTPException(status_code=502, detail=f"사업 개요 조립 실패: {e}") from e
    if payload is None:
        return None
    response.headers["Cache-Control"] = "public, max-age=300"
    return _to_out(payload)


@router.post("/{code}/business/refresh", response_model=BusinessOverviewOut | None)
def refresh_business_overview(
    code: str,
    db: Session = Depends(get_session),
) -> BusinessOverviewOut | None:
    """단건 사업 개요 재조립(원문 재추출 + LLM 정리 + 캐시 갱신). 백필/배치갱신의 수동 진입점.

    새 정기보고서 반영이 필요할 때 호출. 사업보고서 없거나 LLM 미설정 시 null.
    """
    settings = get_settings()
    business_ingest.invalidate_cache(db, code)
    try:
        payload = business_ingest.assemble_overview(db, settings, code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"사업 개요 재조립 실패: {e}") from e
    if payload is None:
        return None
    return _to_out(payload)
