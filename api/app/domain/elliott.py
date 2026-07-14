"""엘리엇 파동 추정 — ZigZag 피벗 검출 + 5파 impulse 라벨링(실험적). 순수 도메인.

엘리엇 파동은 주관적이고 자동 라벨링 오류율이 높다. 그래서 이 모듈은:
1) ZigZag(임계 반전 필터)로 스윙 고·저점(피벗)을 뽑고 — 이 자체가 지지/저항으로 유용,
2) 최근 피벗 시퀀스가 5파 상승 impulse 3대 규칙을 만족할 때만 1~5 라벨을 시도하며,
3) 피보나치 되돌림·확장 근접도로 신뢰도(0~1)를 매긴다(낮으면 라벨을 감춘다).

확정 카운트를 단정하지 않는다. I/O 없음(순수).
"""

from __future__ import annotations

from dataclasses import dataclass

# ZigZag 반전 임계(비율). 한국 스몰캡 변동성 고려해 다소 크게(8%). 이보다 작은 되돌림은 무시.
ZIGZAG_THRESHOLD = 0.08
# 멀티 임계 스캔 — 단일 8%는 종목 변동성에 민감해 저변동/스몰캡의 유효 임펄스를 놓친다(엑사이엔씨
# 등). 여러 임계로 각각 검출해 최고 신뢰도 임펄스를 채택한다. 실측(상위 393종목): 71%→93% 검출,
# 평균신뢰 0.62→0.69(촘촘한 임계가 더 좋은 임펄스를 찾아 품질도 동반 상승).
ZIGZAG_THRESHOLDS = (0.05, 0.06, 0.08, 0.10)
# 라벨을 노출할 최소 신뢰도. 이 미만이면 피벗만 보여준다(억지 카운트 방지). 실측(상위 300종목):
# 0.4 게이트에서 ~73% 검출·평균신뢰 0.62·거의 완전 5파. 더 높이면 검출률만 떨어진다.
MIN_LABEL_CONFIDENCE = 0.4


@dataclass
class Pivot:
    date: str  # YYYY-MM-DD
    price: float
    kind: str  # high | low
    label: str = ""  # '1'~'5' 또는 '' (미라벨)


@dataclass
class ElliottResult:
    pivots: list[Pivot]
    labeled: bool  # 5파 라벨을 붙였는지(신뢰도 임계 통과)
    confidence: float  # 0~1 (라벨 신뢰도, 미라벨이면 0)
    direction: str  # up | down | none (검출된 임펄스 방향)
    note: str  # 사람이 읽는 요약(추정 위치 등)


def zigzag(prices: list[tuple[str, float]], threshold: float = ZIGZAG_THRESHOLD) -> list[Pivot]:
    """(날짜, 종가) 시계열에서 임계 반전으로 스윙 고·저 피벗을 뽑는다.

    방향이 정해지기 전엔 앵커(첫 점) 대비 고·저를 함께 추적하다, 어느 쪽이든 극점에서 threshold
    이상 되돌리면 그 극점을 피벗으로 확정하고 방향을 정한다. 이후엔 진행 방향 극점을 연장하다
    반대로 threshold 이상 되돌릴 때마다 피벗을 확정한다. 마지막 잠정 극점도(참고용) 넣는다.
    """
    if len(prices) < 2:
        return []

    pivots: list[Pivot] = []
    hi_date, hi_price = prices[0]
    lo_date, lo_price = prices[0]
    direction = 0  # +1 상승(고점 추적), -1 하락(저점 추적), 0 미정

    for d, p in prices[1:]:
        if direction == 0:
            if p > hi_price:
                hi_date, hi_price = d, p
            if p < lo_price:
                lo_date, lo_price = d, p
            if p <= hi_price * (1 - threshold):  # 고점에서 반전 하락 → 고점 확정
                pivots.append(Pivot(date=hi_date, price=hi_price, kind="high"))
                direction, lo_date, lo_price = -1, d, p
            elif p >= lo_price * (1 + threshold):  # 저점에서 반전 상승 → 저점 확정
                pivots.append(Pivot(date=lo_date, price=lo_price, kind="low"))
                direction, hi_date, hi_price = 1, d, p
        elif direction == 1:  # 고점 추적 중
            if p > hi_price:
                hi_date, hi_price = d, p
            elif p <= hi_price * (1 - threshold):
                pivots.append(Pivot(date=hi_date, price=hi_price, kind="high"))
                direction, lo_date, lo_price = -1, d, p
        else:  # direction == -1, 저점 추적 중
            if p < lo_price:
                lo_date, lo_price = d, p
            elif p >= lo_price * (1 + threshold):
                pivots.append(Pivot(date=lo_date, price=lo_price, kind="low"))
                direction, hi_date, hi_price = 1, d, p

    # 마지막 진행 중 극점(미확정, 가장 최근 스윙 — 참고용).
    if direction == 1 and (not pivots or pivots[-1].date != hi_date):
        pivots.append(Pivot(date=hi_date, price=hi_price, kind="high"))
    elif direction == -1 and (not pivots or pivots[-1].date != lo_date):
        pivots.append(Pivot(date=lo_date, price=lo_price, kind="low"))
    return pivots


def _fib_score(w1: float, w2: float, w3: float, w4: float) -> float:
    """파동 길이가 피보나치 관계(2파~0.5-0.618, 3파~1.618, 4파~0.382)에 가까운 정도(0~1)."""
    parts: list[float] = []
    # 2파 되돌림 ≈ 0.5~0.618 of 1파
    r2 = w2 / w1 if w1 > 0 else 0
    parts.append(_near(r2, 0.559, 0.25))  # 0.5·0.618 중앙
    # 3파 확장 ≈ 1.618 of 1파
    r3 = w3 / w1 if w1 > 0 else 0
    parts.append(_near(r3, 1.618, 0.6))
    # 4파 되돌림 ≈ 0.382 of 3파
    r4 = w4 / w3 if w3 > 0 else 0
    parts.append(_near(r4, 0.382, 0.25))
    return sum(parts) / len(parts)


def _near(value: float, target: float, tol: float) -> float:
    """value 가 target 에 tol 이내로 가까우면 1, 멀수록 0(선형)."""
    return max(0.0, 1.0 - abs(value - target) / tol)


def _validate_impulse(window: list[Pivot], bull: bool) -> float | None:
    """6개 피벗(저-고-저-고-저-고 또는 반대)이 5파 impulse 3대 규칙을 만족하면 신뢰도, 아니면 None.

    3대 규칙: (1)2파는 1파를 100% 이상 되돌리지 않음, (2)3파가 1·3·5 중 최단 아님,
    (3)4파가 1파 영역 비침범. 상승/하락은 부호만 뒤집어 같은 규칙을 적용한다(폭은 항상 양수).
    """
    kinds = [x.kind for x in window]
    expect = ["low", "high"] if bull else ["high", "low"]
    if kinds != [expect[i % 2] for i in range(6)]:
        return None

    p0, p1, p2, p3, p4, p5 = (x.price for x in window)
    s = 1.0 if bull else -1.0  # 부호: 상승은 그대로, 하락은 뒤집어 폭을 양수로.
    w1 = s * (p1 - p0)  # 1파 추진폭
    w2 = s * (p1 - p2)  # 2파 되돌림폭
    w3 = s * (p3 - p2)  # 3파 추진폭
    w4 = s * (p3 - p4)  # 4파 되돌림폭
    w5 = s * (p5 - p4)  # 5파 추진폭
    if min(w1, w3, w5) <= 0:  # 각 추진파는 양(+)이어야
        return None
    if w2 >= w1:  # 규칙 1: 2파 되돌림 < 1파(시작점 비침범)
        return None
    if w3 < w1 and w3 < w5:  # 규칙 2: 3파가 최단 아님
        return None
    if s * (p4 - p1) <= 0:  # 규칙 3: 4파 끝이 1파 끝 너머(비중첩)
        return None
    return _fib_score(w1, w2, w3, w4)


def _scan_impulse(pivots: list[Pivot], bull: bool) -> tuple[int, float] | None:
    """전 피벗을 슬라이딩하며 한 방향(bull/bear) 5파 impulse 중 신뢰도 최고를 찾는다.

    마지막 6개만 보던 기존 방식은 최신 피벗이 미확정 스윙이라 거의 안 맞았다. 여기선 모든
    6-피벗 창을 훑어 규칙 통과분 중 최고 신뢰도 창의 (시작 인덱스, 신뢰도)를 돌려준다.
    """
    best: tuple[int, float] | None = None
    for start in range(len(pivots) - 5):
        conf = _validate_impulse(pivots[start : start + 6], bull)
        if conf is not None and (best is None or conf > best[1]):
            best = (start, conf)
    return best


def label_impulse(pivots: list[Pivot]) -> tuple[bool, float, str]:
    """피벗 시퀀스에서 상승·하락 5파 impulse 를 슬라이딩 스캔해 최고 신뢰도를 라벨한다.

    양방향 후보 중 신뢰도가 높은 쪽을 고르고, MIN_LABEL_CONFIDENCE 이상이면 해당 6피벗에
    0..5 파동 라벨을 붙인다. 반환 (labeled, confidence, direction).
    """
    if len(pivots) < 6:
        return False, 0.0, "none"
    bull = _scan_impulse(pivots, bull=True)
    bear = _scan_impulse(pivots, bull=False)
    candidates = [(c, s, d) for (best, d) in ((bull, "up"), (bear, "down")) if best for (s, c) in (best,)]
    if not candidates:
        return False, 0.0, "none"
    confidence, start, direction = max(candidates)
    if confidence >= MIN_LABEL_CONFIDENCE:
        for i, x in enumerate(pivots[start : start + 6]):  # P0=0(시작), 1..5 추진/조정파
            x.label = str(i)
        return True, round(confidence, 2), direction
    return False, round(confidence, 2), "none"


def analyze(
    prices: list[tuple[str, float]], thresholds: tuple[float, ...] = ZIGZAG_THRESHOLDS
) -> ElliottResult:
    """종가 시계열을 여러 ZigZag 임계로 검출해 상승·하락 5파 중 최고 신뢰도 라벨을 채택한다.

    임계마다 피벗 집합이 달라지므로 승리한 임계의 피벗을 그대로 반환한다(라벨·연결선 정합).
    라벨이 하나도 안 붙으면 기본 임계(ZIGZAG_THRESHOLD)의 피벗을 지지/저항 표시용으로 준다.
    """
    best: tuple[float, str, list[Pivot]] | None = None  # (confidence, direction, labeled pivots)
    for th in thresholds:
        pivots = zigzag(prices, th)
        labeled, confidence, direction = label_impulse(pivots)  # 통과 시 pivots 를 in-place 라벨
        if labeled and (best is None or confidence > best[0]):
            best = (confidence, direction, pivots)

    if best is not None:
        confidence, direction, pivots = best
        kind = "상승" if direction == "up" else "하락"
        note = f"{kind} 5파 추정(신뢰도 {int(confidence * 100)}%) — 참고용"
        return ElliottResult(
            pivots=pivots, labeled=True, confidence=confidence, direction=direction, note=note
        )

    # 미검출 — 기본 임계 피벗만(지지/저항). 피벗 2개 미만이면 부족 안내.
    pivots = zigzag(prices, ZIGZAG_THRESHOLD)
    note = "뚜렷한 5파 패턴 미검출 — 스윙 고·저점만 표시" if len(pivots) >= 2 else "피벗 부족"
    return ElliottResult(
        pivots=pivots, labeled=False, confidence=0.0, direction="none", note=note
    )
