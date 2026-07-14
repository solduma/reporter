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
# 세부(minor) 등급 임계. 전 구간 세부 파동 흐름을 잡는 기본 등급.
MINOR_THRESHOLD = 0.05
# 상위(major) 등급 재귀 배수 — minor 피벗 위에서 이 비율 이상 스윙만 상위 파동으로 남긴다.
MAJOR_FACTOR = 0.13
# 세그먼트 채택 최소 피보나치 점수(하드룰 통과 후 소프트 게이트). 낮으면 노이즈, 높으면 커버리지↓.
MIN_SEGMENT_CONFIDENCE = 0.3
# 라벨을 '검출됨'으로 노출할 최소 세그먼트 수(하위호환 labeled 플래그용).
_MIN_LABELED_SEGMENTS = 1
# 마지막 세그먼트 이후 진행 레그가 이보다 많으면 복합/연장으로 보고 현재 위치 라벨을 유보한다.
_MAX_TRAILING_LEGS = 3


@dataclass
class Pivot:
    date: str  # YYYY-MM-DD
    price: float
    kind: str  # high | low
    label: str = ""  # '1'~'5' · 'A'~'C' 또는 '' (미라벨)


@dataclass
class WaveSegment:
    """전 구간 내 한 파동 세그먼트(임펄스 5레그 또는 조정 3레그)."""

    start_date: str
    end_date: str
    kind: str  # impulse | correction
    degree: str  # major | minor
    direction: str  # up | down (파동 진행 방향)
    labels: list[str]  # ['0','1','2','3','4','5'] 또는 ['0','A','B','C']
    confidence: float  # 0~1 (피보나치 근접 점수)


@dataclass
class ElliottResult:
    pivots: list[Pivot]  # 세부(minor) 피벗 — 라벨 in-place 부여됨(하위호환 오버레이용)
    labeled: bool  # 세그먼트를 하나라도 검출했는지
    confidence: float  # 대표(최근) 세그먼트 신뢰도
    direction: str  # up | down | none (최근 세그먼트 방향)
    segments: list[WaveSegment] = field(default_factory=list)  # 전 구간 파동 세그먼트(등급 혼합)
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


def recursive_zigzag(pivots: list[Pivot], factor: float = MAJOR_FACTOR) -> list[Pivot]:
    """하위 등급 피벗 위에 다시 ZigZag 를 돌려 상위 등급(major) 피벗을 만든다.

    원가격이 아니라 하위 피벗열을 입력으로 써 상위 피벗이 반드시 하위 피벗의 부분집합이 되게
    한다(엄격한 프랙탈 nesting 보장 — 독립 임계 2개는 이를 보장 못 함). factor 이상 되돌리는
    스윙만 상위 극점으로 남긴다.
    """
    if len(pivots) < 3:
        return list(pivots)
    seq = [(p.date, p.price, p.kind) for p in pivots]
    out: list[Pivot] = [pivots[0]]
    for i in range(1, len(pivots) - 1):
        prev = out[-1]
        move = abs(seq[i][1] - prev.price) / prev.price if prev.price else 0.0
        if move >= factor and pivots[i].kind != prev.kind:
            out.append(pivots[i])
    if out[-1].date != pivots[-1].date:
        out.append(pivots[-1])
    return out


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


def _correction_conf(window: list[Pivot], up: bool) -> float | None:
    """4피벗이 추세 반대 방향 ABC 조정이면 점수, 아니면 None.

    up 추세면 조정은 하락(고-저-고-저), 하락 추세면 상승(저-고-저-고). C·A 는 양(+)이어야 하고
    B 는 되돌림. 지그재그 기준 C≈A(1.0), B≈0.5~0.618 of A 에 가까울수록 고점.
    """
    kinds = [x.kind for x in window]
    expect = ["high", "low"] if up else ["low", "high"]
    if kinds != [expect[i % 2] for i in range(4)]:
        return None
    p0, p1, p2, p3 = (x.price for x in window)
    s = 1.0 if up else -1.0
    a = s * (p0 - p1)  # A 파(추세 반대) 폭
    b = s * (p2 - p1)  # B 파 되돌림
    c = s * (p2 - p3)  # C 파 폭
    if a <= 0 or c <= 0 or b <= 0:
        return None
    return 0.5 * _near(c / a, 1.0, 0.6) + 0.5 * _near(b / a, 0.6, 0.5)


def _chain_segments(pivots: list[Pivot], up: bool, degree: str) -> list[WaveSegment]:
    """피벗열을 좌→우 DP 로 임펄스(5레그)·조정(3레그) 세그먼트 체인으로 분할한다.

    각 위치에서 임펄스/조정 세그먼트를 놓아 (커버리지 + 신뢰도) 총점을 최대화한다. 세그먼트는
    끝피벗=다음 시작피벗으로 연결한다. 안 맞는 구간은 skip(작은 페널티)해 라벨 유보한다.
    """
    n = len(pivots)
    if n < 4:
        return []
    neg = float("-inf")
    best = [neg] * n
    back: list[tuple[int, str, float] | None] = [None] * n
    best[0] = 0.0
    for i in range(n):
        if best[i] == neg:
            continue
        for kind, length in (("impulse", 5), ("correction", 3)):
            j = i + length
            if j >= n:
                continue
            conf = (
                _impulse_conf(pivots[i : j + 1], up)
                if kind == "impulse"
                else _correction_conf(pivots[i : j + 1], up)
            )
            if conf is None or conf < MIN_SEGMENT_CONFIDENCE:
                continue
            score = best[i] + length + conf
            if score > best[j]:
                best[j] = score
                back[j] = (i, kind, conf)
        # 한 피벗 건너뛰기(gap) — 안 맞는 구간을 유보. 작은 페널티로 남발 방지.
        if i + 1 < n and best[i] - 0.5 > best[i + 1]:
            best[i + 1] = best[i] - 0.5
            back[i + 1] = (i, "skip", 0.0)

    end = max(range(n), key=lambda k: best[k])
    segments: list[WaveSegment] = []
    k = end
    while back[k] is not None:
        i, kind, conf = back[k]  # type: ignore[misc]
        if kind != "skip":
            labels = ["0", "1", "2", "3", "4", "5"] if kind == "impulse" else ["0", "A", "B", "C"]
            segments.append(
                WaveSegment(
                    start_date=pivots[i].date,
                    end_date=pivots[k].date,
                    kind=kind,
                    degree=degree,
                    direction="up" if up else "down",
                    labels=labels,
                    confidence=round(conf, 2),
                )
            )
            # 세부(minor) 등급이면 라벨을 피벗에 in-place 부여(하위호환 오버레이).
            if degree == "minor":
                for offset, lab in enumerate(labels):
                    pivots[i + offset].label = lab
        k = i
    segments.reverse()
    return segments


def _current_position(
    pivots: list[Pivot], segments: list[WaveSegment], up: bool
) -> tuple[str, float | None]:
    """마지막 세그먼트 + 이후 진행 레그로 현재 파동 위치와 무효화가격을 추정한다.

    진행 레그가 _MAX_TRAILING_LEGS 초과면 복합/연장으로 보고 라벨을 유보한다(정직).
    """
    minor = [s for s in segments if s.degree == "minor"]
    if not minor:
        return "구조 불명 — 스윙만 표시", None
    last = minor[-1]
    end_idx = next((i for i, p in enumerate(pivots) if p.date == last.end_date), None)
    start_idx = next((i for i, p in enumerate(pivots) if p.date == last.start_date), None)
    if end_idx is None or start_idx is None:
        return "진행 중", None
    trailing = (len(pivots) - 1) - end_idx  # 마지막 세그 끝 이후 진행 레그
    if trailing > _MAX_TRAILING_LEGS:
        return f"{'추진' if last.kind == 'impulse' else '조정'} 이후 복합 구간 — 라벨 유보", None
    if last.kind == "impulse":
        phases = {
            0: "추진 5파 완성 — 조정 시작 가능",
            1: "조정 A파 진행",
            2: "조정 B파 되돌림",
            3: "조정 C파 진행 — 추세 재개 주시",
        }
        return phases[trailing], pivots[end_idx].price  # 5파 극점 재돌파 시 조정 무효
    phases = {
        0: "조정 완료 — 새 추진 임박",
        1: "추진 1파 진행",
        2: "추진 2파 되돌림 — 얕을수록 강세",
        3: "추진 3파 진행 추정",
    }
    return phases[trailing], pivots[start_idx].price  # 조정 시작 이탈 시 추진 무효


def analyze(
    prices: list[tuple[str, float]],
    minor_threshold: float = MINOR_THRESHOLD,
    major_factor: float = MAJOR_FACTOR,
) -> ElliottResult:
    """종가 시계열을 다중 등급으로 전 구간 파동 세그먼트 체인 + 현재 위치까지 분석한다.

    세부(minor) 피벗 → 재귀로 상위(major) 피벗을 만들어 각 등급을 체인 파싱하고, 최근 세그먼트로
    현재 파동 위치·무효화가격을 추정한다. 안 맞는 구간은 라벨 유보(억지 카운트 안 함).
    """
    minor = zigzag(prices, minor_threshold)
    if len(minor) < 4:
        return ElliottResult(
            pivots=minor, labeled=False, confidence=0.0, direction="none",
            current_position="피벗 부족", note="피벗 부족",
        )

    up = prices[-1][1] >= prices[0][1]  # 전 구간 순추세 방향
    minor_segs = _chain_segments(minor, up, "minor")  # minor 라벨 in-place 부여
    major = recursive_zigzag(minor, major_factor)
    major_segs = _chain_segments(major, up, "major") if len(major) >= 4 else []
    segments = major_segs + minor_segs  # major 먼저(굵게), minor 겹쳐

    position, invalidation = _current_position(minor, segments, up)
    labeled = len(minor_segs) >= _MIN_LABELED_SEGMENTS
    last = minor_segs[-1] if minor_segs else None
    confidence = last.confidence if last else 0.0
    direction = last.direction if last else "none"

    if labeled:
        n_imp = sum(1 for s in minor_segs if s.kind == "impulse")
        n_cor = sum(1 for s in minor_segs if s.kind == "correction")
        note = f"추진 {n_imp}·조정 {n_cor} 세그먼트 · {position} — 참고용(확정 아님)"
    else:
        note = "뚜렷한 파동 구조 미검출 — 스윙 고·저점만 표시"

    return ElliottResult(
        pivots=minor,
        labeled=labeled,
        confidence=confidence,
        direction=direction,
        segments=segments,
        current_position=position,
        invalidation_price=round(invalidation, 2) if invalidation else None,
        note=note,
    )
