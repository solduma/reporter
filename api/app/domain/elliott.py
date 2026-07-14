"""엘리엇 파동 추정 — 전 구간 임펄스+조정 체인 라벨링 + 프랙탈 등급 + 현재 위치. 순수 도메인.

엘리엇 파동은 주관적이라 자동 라벨링 오류율이 높다. 그래서 이 모듈은 확립된 방법론(3대 하드룰
게이트 + 피보나치 소프트 점수 + 재귀 ZigZag 다중 등급 + 전 구간 교대 체인 파싱)을 따르되,
안 맞는 구간은 억지로 라벨하지 않고 유보한다(elastic relabeling 회피):

1) ZigZag(임계 반전 필터)로 스윙 피벗을 뽑고 — 그 자체가 지지/저항으로 유용,
2) 재귀 ZigZag 로 상위 등급(major) 피벗을 만들어 프랙탈(장기 파동 내 단기 파동)을 드러내며,
3) 각 등급 피벗열을 좌→우 DP 로 임펄스(1-5)·조정(A-B-C) 세그먼트 체인으로 분할하고,
4) 마지막 세그먼트로 현재 파동 위치와 무효화가격을 추정한다(확정 아님, 참고용).

확정 카운트를 단정하지 않는다. I/O 없음(순수).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ZigZag 반전 임계(비율). 한국 스몰캡 변동성 고려해 다소 크게(8%). 이보다 작은 되돌림은 무시.
ZIGZAG_THRESHOLD = 0.08
# 기본 다리(leg) 등급 임계. 전 구간 상승/하락 다리를 균형있게 잡는다(하락 도배 방지).
LEG_THRESHOLD = 0.06
# 임펄스 스캔 다중 임계 — 큰 5파(2년 상승 등)는 굵은 임계에서만 잡히고, 세부 5파는 가는 임계에서
# 잡힌다. 단일 임계는 종목 변동성에 민감해 인피니트의 상승 5파를 통째로 놓쳤다. 여러 임계를 훑어
# 날짜구간 겹침 없이 고신뢰부터 채택한다.
IMPULSE_THRESHOLDS = (0.05, 0.06, 0.08, 0.10)
# 엄격 임펄스(강조 레이어) 스캔용 최소 피보나치 신뢰도. 하드룰 통과 후 이 이상만 5파로 강조.
IMPULSE_MIN_CONFIDENCE = 0.45
# 마지막 다리 이후 위치 판단에 쓰는 최근 다리 수 상한.
_MAX_TRAILING_LEGS = 3


@dataclass
class Pivot:
    date: str  # YYYY-MM-DD
    price: float
    kind: str  # high | low
    label: str = ""  # '1'~'5' · 'A'~'C' 또는 '' (미라벨)


@dataclass
class WavePoint:
    """임펄스 세그먼트의 라벨 포인트(자체 피벗). 임계마다 피벗 집합이 달라 세그먼트가 직접 보유한다."""

    date: str  # YYYY-MM-DD
    price: float
    label: str  # '0'~'5'


@dataclass
class WaveSegment:
    """파동 세그먼트. 두 레이어로 구성:

    - layer='leg': 기본 다리(단일 상승/하락 스윙) — 전 구간 흐름을 균형있게 보여준다.
    - layer='impulse': 하드룰 통과 5파 임펄스(강조) — points 6개(0~5)를 자체 보유, 굵게 표시.
    """

    start_date: str
    end_date: str
    layer: str  # leg | impulse
    direction: str  # up | down (실제 가격 진행 방향)
    labels: list[str]  # leg=[] , impulse=['0'..'5']
    confidence: float  # 0~1 (impulse 만 유효, leg 는 0)
    points: list[WavePoint] = field(default_factory=list)  # impulse 만: 라벨 6점(자체 피벗)


@dataclass
class ElliottResult:
    pivots: list[Pivot]  # 기본 다리 등급 피벗 — 강조 임펄스 라벨 in-place 부여
    labeled: bool  # 강조 임펄스를 하나라도 검출했는지
    confidence: float  # 대표(최근) 임펄스 신뢰도
    direction: str  # up | down | none (최근 임펄스 방향)
    segments: list[WaveSegment] = field(default_factory=list)  # leg + impulse 세그먼트
    current_position: str = ""  # 현재 파동 위치(사람이 읽는 추정 문구)
    invalidation_price: float | None = None  # 현재 카운트 무효화 경계(있으면)
    note: str = ""  # 사람이 읽는 요약


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


def _near(value: float, target: float, tol: float) -> float:
    """value 가 target 에 tol 이내로 가까우면 1, 멀수록 0(선형)."""
    return max(0.0, 1.0 - abs(value - target) / tol)


def _fib_score(w1: float, w2: float, w3: float, w4: float) -> float:
    """파동 길이가 피보나치 관계(2파~0.5-0.618, 3파~1.618, 4파~0.382)에 가까운 정도(0~1)."""
    parts: list[float] = []
    r2 = w2 / w1 if w1 > 0 else 0  # 2파 되돌림 ≈ 0.5~0.618 of 1파
    parts.append(_near(r2, 0.559, 0.25))
    r3 = w3 / w1 if w1 > 0 else 0  # 3파 확장 ≈ 1.618 of 1파
    parts.append(_near(r3, 1.618, 0.6))
    r4 = w4 / w3 if w3 > 0 else 0  # 4파 되돌림 ≈ 0.382 of 3파
    parts.append(_near(r4, 0.382, 0.25))
    return sum(parts) / len(parts)


def _impulse_conf(window: list[Pivot], up: bool) -> float | None:
    """6피벗이 5파 임펄스 3대 하드룰을 통과하면 피보나치 점수, 아니면 None. 상승/하락 미러.

    3대 하드룰(절대 게이트): R1 2파는 1파 시작 비침범, R2 3파 최단 아님, R3 4파가 1파 끝 비중첩.
    """
    kinds = [x.kind for x in window]
    expect = ["low", "high"] if up else ["high", "low"]
    if kinds != [expect[i % 2] for i in range(6)]:
        return None
    p0, p1, p2, p3, p4, p5 = (x.price for x in window)
    s = 1.0 if up else -1.0  # 부호: 상승은 그대로, 하락은 뒤집어 폭을 양수로.
    w1, w2, w3, w4, w5 = (
        s * (p1 - p0), s * (p1 - p2), s * (p3 - p2), s * (p3 - p4), s * (p5 - p4)
    )
    if min(w1, w3, w5) <= 0:  # 각 추진파는 양(+)
        return None
    if w2 >= w1:  # R1: 2파 되돌림 < 1파
        return None
    if w3 < w1 and w3 < w5:  # R2: 3파 최단 아님
        return None
    if s * (p4 - p1) <= 0:  # R3: 4파 끝이 1파 끝 너머(비중첩)
        return None
    return _fib_score(w1, w2, w3, w4)


def _leg_segments(pivots: list[Pivot]) -> list[WaveSegment]:
    """인접 피벗을 잇는 기본 다리(단일 상승/하락 스윙) 세그먼트 — 전 구간 흐름을 균형있게 보여준다."""
    segs: list[WaveSegment] = []
    for i in range(len(pivots) - 1):
        a, b = pivots[i], pivots[i + 1]
        segs.append(
            WaveSegment(
                start_date=a.date, end_date=b.date, layer="leg",
                direction="up" if b.price > a.price else "down", labels=[], confidence=0.0,
            )
        )
    return segs


def _find_impulses(prices: list[tuple[str, float]]) -> list[WaveSegment]:
    """여러 ZigZag 임계로 하드룰 통과 5파 임펄스를 양방향 검출해 강조 세그먼트로 만든다.

    큰 5파는 굵은 임계에서만, 세부 5파는 가는 임계에서 잡히므로 임계마다 스캔한 뒤 날짜구간이
    겹치지 않게 신뢰도 높은 것부터 채택한다. 임계마다 피벗 집합이 다르므로 각 세그먼트는 자기
    라벨 6점(WavePoint)을 직접 보유한다(base pivots 에 라벨을 못 얹음).
    """
    # (신뢰도, 시작일, 끝일, up, 6점) 후보를 전 임계에서 모은다.
    candidates: list[tuple[float, str, str, bool, list[Pivot]]] = []
    for th in IMPULSE_THRESHOLDS:
        piv = zigzag(prices, th)
        for i in range(len(piv) - 5):
            window = piv[i : i + 6]
            for up in (True, False):
                conf = _impulse_conf(window, up)
                if conf is not None and conf >= IMPULSE_MIN_CONFIDENCE:
                    candidates.append((conf, window[0].date, window[5].date, up, window))
    candidates.sort(key=lambda c: -c[0])  # 최고 신뢰도 우선

    chosen: list[tuple[float, str, str, bool, list[Pivot]]] = []
    for cand in candidates:
        _, sd, ed, _, _ = cand
        # 이미 채택된 임펄스와 날짜구간이 겹치면 건너뛴다(중복·다른 임계 중복 방지).
        if any(not (ed < cs or sd > ce) for _, cs, ce, _, _ in chosen):
            continue
        chosen.append(cand)
    chosen.sort(key=lambda c: c[1])  # 시간순

    segs: list[WaveSegment] = []
    for conf, sd, ed, up, window in chosen:
        points = [
            WavePoint(date=p.date, price=p.price, label=str(k)) for k, p in enumerate(window)
        ]
        segs.append(
            WaveSegment(
                start_date=sd, end_date=ed, layer="impulse",
                direction="up" if up else "down", labels=["0", "1", "2", "3", "4", "5"],
                confidence=round(conf, 2), points=points,
            )
        )
    return segs


def _current_position(
    pivots: list[Pivot], impulses: list[WaveSegment]
) -> tuple[str, float | None]:
    """가장 최근 임펄스 + 이후 진행 다리로 현재 파동 위치·무효화가격을 추정한다.

    진행 다리(임펄스 끝 이후 leg 피벗 수)가 _MAX_TRAILING_LEGS 초과면 복합/연장으로 보고 유보한다.
    """
    if not impulses:
        return "뚜렷한 5파 미검출 — 스윙 흐름만 참고", None
    last = max(impulses, key=lambda s: s.end_date)  # 가장 최근에 끝난 임펄스
    end_price = last.points[-1].price if last.points else None
    trailing = sum(1 for p in pivots if p.date > last.end_date)  # 임펄스 종료 이후 leg 피벗
    up = last.direction == "up"
    kind = "상승" if up else "하락"
    if trailing > _MAX_TRAILING_LEGS:
        return f"{kind} 5파 이후 복합 구간 — 라벨 유보", None
    # 5파 완성 후엔 반대 방향 조정(A·B·C)이 진행된다.
    phases = {
        0: f"{kind} 5파 완성 — 반대 조정 시작 가능",
        1: "조정 A파 진행",
        2: "조정 B파 되돌림",
        3: "조정 C파 진행 — 추세 재개 주시",
    }
    return phases[trailing], end_price  # 5파 극점 재돌파 시 조정 무효


def analyze(
    prices: list[tuple[str, float]],
    leg_threshold: float = LEG_THRESHOLD,
) -> ElliottResult:
    """종가 시계열을 두 레이어로 분석: 기본 다리(전 구간 상승/하락 흐름) + 강조 5파 임펄스.

    기본 다리는 상승/하락을 균형있게 드러내고(하락 도배 방지), 그 위에 다중 임계로 검출한 하드룰
    통과 임펄스(양방향)를 강조로 얹는다. 최근 임펄스로 현재 파동 위치·무효화가격을 추정한다.
    """
    pivots = zigzag(prices, leg_threshold)
    if len(pivots) < 3:
        return ElliottResult(
            pivots=pivots, labeled=False, confidence=0.0, direction="none",
            current_position="피벗 부족", note="피벗 부족",
        )

    leg_segs = _leg_segments(pivots)
    impulses = _find_impulses(prices)  # 다중 임계 스캔, 세그먼트가 자체 라벨 포인트 보유
    segments = leg_segs + impulses  # 다리(기본) + 임펄스(강조)

    position, invalidation = _current_position(pivots, impulses)
    labeled = len(impulses) > 0
    last = max(impulses, key=lambda s: s.end_date) if impulses else None  # 가장 최근 임펄스
    confidence = last.confidence if last else 0.0
    direction = last.direction if last else "none"

    if labeled:
        n_up = sum(1 for s in impulses if s.direction == "up")
        n_dn = len(impulses) - n_up
        note = f"5파 임펄스 상승 {n_up}·하락 {n_dn} · {position} — 참고용(확정 아님)"
    else:
        note = "뚜렷한 5파 미검출 — 상승/하락 스윙 흐름만 표시"

    return ElliottResult(
        pivots=pivots,
        labeled=labeled,
        confidence=confidence,
        direction=direction,
        segments=segments,
        current_position=position,
        invalidation_price=round(invalidation, 2) if invalidation else None,
        note=note,
    )
