"""딥다이브 실행 직전 재무 신선화 — stale 재무를 최신화하고 파생지표(EV/EBITDA·EBITDA 성장축)를 맞춘다.

딥다이브는 DB 에 저장된 재무를 읽기만 하므로(DART 직접 조회 안 함), 실행 시점에 재무가 오래됐으면
낡은 값으로 밸류에이션이 돌아간다. worker 컨텍스트에서 job 실행 직전에 한 번 신선화해 이를 막는다.

- 재무 TTL(12h) 경과 시: 네이버 최신 분기 동기(sync_financials) + 보고서 원문 백필(EV/EBITDA 재산출).
- 매 실행: EBITDA 성장축을 DB 재무 기준으로 재계산(financials 는 갱신됐는데 growth_metric 이 stale 한 경우 정합).
- 재무 지문(financials_fingerprint)을 반환해 재생성 판정(inputs_hash)에 최신성을 반영한다.

DART 한도 초과는 상위(run_job)가 처리하도록 전파하지 않고 신선화 실패로 흡수한다 — 낡은 값이라도
분석은 진행(중단보다 낫다). 백필 자체 실패도 마찬가지(로그만).
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Financial
from app.services import company_service, growth_ingest, report_ingest, sync_state

logger = logging.getLogger(__name__)


def refresh(db: Session, settings: Settings, code: str) -> None:
    """딥다이브 대상 종목의 재무를 신선화한다(stale 시 동기·백필 + 파생지표 재계산). 실패는 흡수."""
    if not company_service.financials_fresh(db, code):
        before_fp = financials_fingerprint(db, code)
        try:
            company_service.sync_financials(db, code)  # 네이버 최신 분기 → financials
        except Exception as e:
            logger.warning("deepdive freshness: sync_financials 실패 %s: %s", code, e)
        # 보고서 원문 백필(EV/EBITDA 재산출)은 종목당 document.xml(수MB) 다운로드라 무겁다.
        # 재무가 실제로 바뀌었거나(지문 변화) 아직 한 번도 백필 안 된 종목만 재실행한다 —
        # 야간 배치가 이미 채웠고 재무도 그대로면 재다운로드는 DART 콜·시간 낭비일 뿐이다.
        changed = financials_fingerprint(db, code) != before_fp
        if changed or not company_service.report_10y_done(db, code):
            try:
                if report_ingest.backfill_stock(db, settings, code):
                    sync_state.mark(db, "report_10y", code)
                    db.commit()
            except Exception as e:
                db.rollback()
                logger.warning("deepdive freshness: report backfill 실패 %s: %s", code, e)

    # financials 가 갱신됐거나 EBITDA 가 새로 채워진 경우 EBITDA 성장축 정합(외부 호출 없음).
    try:
        if growth_ingest.refresh_ebitda_axis(db, code):
            db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("deepdive freshness: EBITDA 성장축 재계산 실패 %s: %s", code, e)


def financials_fingerprint(db: Session, code: str) -> str:
    """재무 지문 — (period, updated_at) 목록 해시. 재무가 갱신되면 값이 바뀌어 재생성 판정에 반영된다."""
    rows = db.execute(
        select(Financial.period, Financial.updated_at)
        .where(Financial.stock_code == code)
        .order_by(Financial.period)
    ).all()
    payload = "|".join(f"{p}:{u.isoformat() if u else ''}" for p, u in rows)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
