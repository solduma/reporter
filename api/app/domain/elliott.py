"""엘리엇 파동 — 전 구간 연속 위상 교대 + 프랙탈 재귀 + 가격 투영. 순수 도메인.

엘리엇 파동은 주관적이라 유일 해가 없다(원저자도 복수 카운트 공존을 인정). 확립된 방법론을 따르되
실데이터가 교과서적 5-3 교대에 저항하는 현실을 신뢰도 차등으로 정직하게 표현한다:

1) ZigZag 로 스윙 피벗을 뽑고(그 자체가 지지/저항),
2) 재귀 ZigZag 로 상위 등급 피벗을 만들어 프랙탈(장기 파동 내 단기 파동)을 드러내며,
3) 전 구간을 motive/corrective 위상으로 **중단없이(gapless)** 교대 라벨하되, 하드룰+피보를 통과한
   구간은 고신뢰(진짜 5파), 미달 구간은 저신뢰로 차등 표시하고,
4) 다중 임계로 진짜 5파 임펄스를 검출해 강조하고, 최근 완성 파동으로 다음 파동 가격 목표(피보
   투영 zone)와 현재 위치·무효화가격을 낸다.

확정 카운트를 단정하지 않는다. I/O 없음(순수).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── 임계·상수 ───────────────────────────────────────────────────────────
ZIGZAG_THRESHOLD = 0.08
# 기본 다리(leg) 등급 임계 — 전 구간 위상 교대·스윙 흐름의 기본 해상도.
LEG_THRESHOLD = 0.06
# 다중 임계 임펄스 스캔 — 큰 5파는 굵은 임계에서만, 세부 5파는 가는 임계에서 잡힌다.
IMPULSE_THRESHOLDS = (0.05, 0.06, 0.08, 0.10, 0.13)
# 재귀 상위 등급 배수 — leg 피벗 위에 이 비율 이상 스윙만 상위 파동으로(nesting 보장).
MAJOR_FACTOR = 0.18
# 임펄스 강조 최소 피보 신뢰도(하드룰 통과 후).
IMPULSE_MIN_CONFIDENCE = 0.45
# 위상 교대에서 미달(저신뢰) 세그먼트에 줄 신뢰도.
_LOW_CONF = 0.25
# 현재 위치 판단에 쓰는 임펄스 종료 이후 진행 다리 상한.
_MAX_TRAILING_LEGS = 3
# 5파 완성 후 반대 조정 목표 되돌림 비율(피보) — 투영 zone 경계.
_CORRECTION_RETRACE = (0.382, 0.618)


@dataclass
class Pivot:
    date: str  # YYYY-MM-DD
    price: float
    kind: str  # high | low
    label: str = ""  # 미사용(하위호환 필드)


@dataclass
class WavePoint:
    """임펄스 세그먼트의 라벨 포인트(자체 피벗)."""

    date: str
    price: float
    label: str  # '0'~'5'


@dataclass
class WaveSegment:
    """파동 세그먼트.

    - layer='leg': 전 구간 위상 교대의 한 구간. phase(motive|corrective)·confidence 로 차등.
    - layer='impulse': 다중 임계로 검출한 진짜 5파(강조). points 6개(0~5) 보유.
    """

    start_date: str
    end_date: str
    layer: str  # leg | impulse
    direction: str  # up | down (실제 가격 진행 방향)
    phase: str = ""  # leg: motive | corrective , impulse: ''
    labels: list[str] = field(default_factory=list)  # impulse=['0'..'5']
    confidence: float = 0.0  # 0~1
    points: list[WavePoint] = field(default_factory=list)  # impulse 만: 라벨 6점


@dataclass
class WaveProjection:
    """다음 파동 가격 목표 구간(피보 투영). 단일 선이 아니라 zone."""

    wave: str  # '3' | '5' | 'C' (투영 대상)
    low: float
    high: float
    basis: str  # 사람이 읽는 근거(예: "1파 1.618~2.618배")


@dataclass
class ElliottResult:
    pivots: list[Pivot]  # 기본 다리 피벗(스윙 흐름)
    labeled: bool  # 고신뢰 임펄스를 하나라도 검출했는지
    confidence: float  # 최근 임펄스 신뢰도
    direction: str  # up | down | none
    segments: list[WaveSegment] = field(default_factory=list)  # leg(연속) + impulse(강조)
    current_position: str = ""
    invalidation_price: float | None = None
    projection: WaveProjection | None = None  # 다음 파동 가격 목표 zone
    note: str = ""


def zigzag(prices: list[tuple[str, float]], threshold: float = ZIGZAG_THRESHOLD) -> list[Pivot]:
    """(날짜, 종가) 시계열에서 임계 반전으로 스윙 고·저 피벗을 뽑는다.

    방향 미정 땐 앵커 대비 고·저를 함께 추적하다 극점에서 threshold 이상 되돌리면 그 극점을 확정하고
    방향을 정한다. 이후 진행 극점을 연장하다 반대로 threshold 이상 되돌릴 때마다 피벗 확정.
    """
    if len(prices) < 2:
        return []
    pivots: list[Pivot] = []
    hi_date, hi_price = prices[0]
    lo_date, lo_price = prices[0]
    direction = 0
    for d, p in prices[1:]:
        if direction == 0:
            if p > hi_price:
                hi_date, hi_price = d, p
            if p < lo_price:
                lo_date, lo_price = d, p
            if p <= hi_price * (1 - threshold):
                pivots.append(Pivot(date=hi_date, price=hi_price, kind="high"))
                direction, lo_date, lo_price = -1, d, p
            elif p >= lo_price * (1 + threshold):
                pivots.append(Pivot(date=lo_date, price=lo_price, kind="low"))
                direction, hi_date, hi_price = 1, d, p
        elif direction == 1:
            if p > hi_price:
                hi_date, hi_price = d, p
            elif p <= hi_price * (1 - threshold):
                pivots.append(Pivot(date=hi_date, price=hi_price, kind="high"))
                direction, lo_date, lo_price = -1, d, p
        else:
            if p < lo_price:
                lo_date, lo_price = d, p
            elif p >= lo_price * (1 + threshold):
                pivots.append(Pivot(date=lo_date, price=lo_price, kind="low"))
                direction, hi_date, hi_price = 1, d, p
    if direction == 1 and (not pivots or pivots[-1].date != hi_date):
        pivots.append(Pivot(date=hi_date, price=hi_price, kind="high"))
    elif direction == -1 and (not pivots or pivots[-1].date != lo_date):
        pivots.append(Pivot(date=lo_date, price=lo_price, kind="low"))
    return pivots


def recursive_zigzag(pivots: list[Pivot], factor: float = MAJOR_FACTOR) -> list[Pivot]:
    """하위 피벗 위에 재귀 ZigZag — factor 이상 되돌리는 스윙만 상위 등급으로. 상위⊆하위(nesting)."""
    if len(pivots) < 3:
        return list(pivots)
    out: list[Pivot] = [pivots[0]]
    for i in range(1, len(pivots) - 1):
        prev = out[-1]
        move = abs(pivots[i].price - prev.price) / prev.price if prev.price else 0.0
        if pivots[i].kind != prev.kind and move >= factor:
            out.append(pivots[i])
    if out[-1].date != pivots[-1].date:
        out.append(pivots[-1])
    return out


def _near(value: float, target: float, tol: float) -> float:
    """value 가 target 에 tol 이내로 가까우면 1, 멀수록 0(선형)."""
    return max(0.0, 1.0 - abs(value - target) / tol)


def _fib_score(w1: float, w2: float, w3: float, w4: float) -> float:
    """파동 길이가 피보나치 관계(2파~0.5-0.618, 3파~1.618, 4파~0.382)에 가까운 정도(0~1)."""
    parts = [
        _near(w2 / w1 if w1 > 0 else 0, 0.559, 0.25),
        _near(w3 / w1 if w1 > 0 else 0, 1.618, 0.6),
        _near(w4 / w3 if w3 > 0 else 0, 0.382, 0.25),
    ]
    return sum(parts) / len(parts)


def _impulse_conf(window: list[Pivot], up: bool) -> float | None:
    """6피벗이 5파 임펄스 3대 하드룰을 통과하면 피보 점수, 아니면 None. 상승/하락 미러.

    3대 하드룰: R1 2파는 1파 시작 비침범, R2 3파 최단 아님, R3 4파가 1파 끝 비중첩.
    """
    kinds = [x.kind for x in window]
    expect = ["low", "high"] if up else ["high", "low"]
    if kinds != [expect[i % 2] for i in range(6)]:
        return None
    p0, p1, p2, p3, p4, p5 = (x.price for x in window)
    s = 1.0 if up else -1.0
    w1, w2, w3, w4, w5 = (
        s * (p1 - p0), s * (p1 - p2), s * (p3 - p2), s * (p3 - p4), s * (p5 - p4)
    )
    if min(w1, w3, w5) <= 0:
        return None
    if w2 >= w1:  # R1
        return None
    if w3 < w1 and w3 < w5:  # R2
        return None
    if s * (p4 - p1) <= 0:  # R3
        return None
    return _fib_score(w1, w2, w3, w4)


def _find_impulses(prices: list[tuple[str, float]]) -> list[WaveSegment]:
    """여러 ZigZag 임계로 하드룰 통과 5파 임펄스를 양방향 검출, 날짜 겹침 없이 고신뢰부터 채택한다.

    임계마다 피벗 집합이 달라 각 세그먼트는 자기 라벨 6점(WavePoint)을 보유한다.
    """
    candidates: list[tuple[float, str, str, bool, list[Pivot]]] = []
    for th in IMPULSE_THRESHOLDS:
        piv = zigzag(prices, th)
        for i in range(len(piv) - 5):
            window = piv[i : i + 6]
            for up in (True, False):
                conf = _impulse_conf(window, up)
                if conf is not None and conf >= IMPULSE_MIN_CONFIDENCE:
                    candidates.append((conf, window[0].date, window[5].date, up, window))
    candidates.sort(key=lambda c: -c[0])
    chosen: list[tuple[float, str, str, bool, list[Pivot]]] = []
    for cand in candidates:
        _, sd, ed, _, _ = cand
        if any(not (ed < cs or sd > ce) for _, cs, ce, _, _ in chosen):
            continue
        chosen.append(cand)
    chosen.sort(key=lambda c: c[1])
    segs: list[WaveSegment] = []
    for conf, sd, ed, up, window in chosen:
        points = [WavePoint(date=p.date, price=p.price, label=str(k)) for k, p in enumerate(window)]
        segs.append(
            WaveSegment(
                start_date=sd, end_date=ed, layer="impulse", direction="up" if up else "down",
                labels=["0", "1", "2", "3", "4", "5"], confidence=round(conf, 2), points=points,
            )
        )
    return segs


def _phase_chain(pivots: list[Pivot], impulses: list[WaveSegment]) -> list[WaveSegment]:
    """전 구간을 motive/corrective 위상으로 중단없이(gapless) 교대 라벨한다.

    검출된 고신뢰 임펄스가 놓인 구간은 motive(고신뢰)로 고정하고, 그 사이 빈 구간은 남은 다리를
    corrective/motive 로 교대 배정(저신뢰)한다. 항상 이전 세그 끝=다음 시작(gap 없음).
    """
    n = len(pivots)
    if n < 2:
        return []
    # 임펄스가 차지한 피벗 인덱스 구간 [start_i, end_i].
    imp_spans: list[tuple[int, int, WaveSegment]] = []
    date_to_idx = {p.date: i for i, p in enumerate(pivots)}
    for imp in impulses:
        si = date_to_idx.get(imp.start_date)
        ei = date_to_idx.get(imp.end_date)
        if si is not None and ei is not None:
            imp_spans.append((si, ei, imp))
    imp_spans.sort()

    segs: list[WaveSegment] = []
    i = 0
    phase = "motive"  # 임펄스 밖 구간의 시작 위상(임펄스=motive 이므로 그 뒤는 corrective)

    def leg_seg(a: int, b: int, ph: str) -> WaveSegment:
        up = pivots[b].price > pivots[a].price
        return WaveSegment(
            start_date=pivots[a].date, end_date=pivots[b].date, layer="leg",
            direction="up" if up else "down", phase=ph, confidence=_LOW_CONF,
        )

    for si, ei, _imp in imp_spans:
        # 임펄스 이전 빈 구간을 다리 단위로 교대 채움(gapless).
        while i < si:
            segs.append(leg_seg(i, i + 1, phase))
            phase = "corrective" if phase == "motive" else "motive"
            i += 1
        # 임펄스 구간을 motive(고신뢰) 한 덩어리로.
        segs.append(
            WaveSegment(
                start_date=pivots[si].date, end_date=pivots[ei].date, layer="leg",
                direction="up" if pivots[ei].price > pivots[si].price else "down",
                phase="motive", confidence=0.7,
            )
        )
        i = ei
        phase = "corrective"  # 임펄스 다음은 조정 기대
    # 남은 꼬리 구간.
    while i < n - 1:
        segs.append(leg_seg(i, i + 1, phase))
        phase = "corrective" if phase == "motive" else "motive"
        i += 1
    return segs


def _project(impulses: list[WaveSegment]) -> WaveProjection | None:
    """가장 최근 임펄스가 진행 중이라 가정하고 다음 파동 가격 목표 zone 을 피보로 투영한다.

    완성된 최근 임펄스의 파동 폭으로 그 다음(반대 방향 조정 C, 또는 새 추진 3파)을 추정한다.
    실데이터에선 '완성 임펄스 다음 조정의 C' 투영이 가장 실용적이라 그걸 낸다.
    """
    if not impulses:
        return None
    last = max(impulses, key=lambda s: s.end_date)
    if len(last.points) < 6:
        return None
    p = [pt.price for pt in last.points]
    up = last.direction == "up"
    s = 1.0 if up else -1.0
    end = p[5]
    # 5파 완성 후 반대 방향 조정 → A 폭을 5파 전체(0→5)의 되돌림으로 추정하기 어려우니, 관용적으로
    # 직전 임펄스 전체 폭 대비 0.382~0.618 되돌림 zone 을 다음 조정 목표로 제시.
    span = s * (p[5] - p[0])
    r_lo, r_hi = _CORRECTION_RETRACE
    t1 = end - s * span * r_lo
    t2 = end - s * span * r_hi
    low, high = (min(t1, t2), max(t1, t2))
    kind = "상승" if up else "하락"
    return WaveProjection(
        wave="조정", low=round(low, 2), high=round(high, 2),
        basis=f"{kind} 5파 되돌림 {r_lo}~{r_hi}",
    )


def _current_position(
    pivots: list[Pivot], impulses: list[WaveSegment]
) -> tuple[str, float | None]:
    """가장 최근 임펄스 + 이후 진행 다리로 현재 파동 위치·무효화가격을 추정한다."""
    if not impulses:
        return "뚜렷한 5파 미검출 — 스윙 흐름만 참고", None
    last = max(impulses, key=lambda s: s.end_date)
    end_price = last.points[-1].price if last.points else None
    trailing = sum(1 for p in pivots if p.date > last.end_date)
    up = last.direction == "up"
    kind = "상승" if up else "하락"
    if trailing > _MAX_TRAILING_LEGS:
        return f"{kind} 5파 이후 복합 구간 — 라벨 유보", None
    phases = {
        0: f"{kind} 5파 완성 — 반대 조정 시작 가능",
        1: "조정 A파 진행",
        2: "조정 B파 되돌림",
        3: "조정 C파 진행 — 추세 재개 주시",
    }
    return phases[trailing], end_price


def analyze(
    prices: list[tuple[str, float]],
    leg_threshold: float = LEG_THRESHOLD,
) -> ElliottResult:
    """전 구간 연속 위상 교대 + 다중 임계 5파 강조 + 가격 투영으로 파동 구조를 분석한다."""
    pivots = zigzag(prices, leg_threshold)
    if len(pivots) < 3:
        return ElliottResult(
            pivots=pivots, labeled=False, confidence=0.0, direction="none",
            current_position="피벗 부족", note="피벗 부족",
        )

    impulses = _find_impulses(prices)  # 다중 임계 진짜 5파(강조)
    chain = _phase_chain(pivots, impulses)  # 전 구간 gapless 위상 교대(연속)
    segments = chain + impulses

    position, invalidation = _current_position(pivots, impulses)
    projection = _project(impulses)
    labeled = len(impulses) > 0
    last = max(impulses, key=lambda s: s.end_date) if impulses else None
    confidence = last.confidence if last else 0.0
    direction = last.direction if last else "none"

    if labeled:
        n_up = sum(1 for s in impulses if s.direction == "up")
        n_dn = len(impulses) - n_up
        note = f"5파 임펄스 상승 {n_up}·하락 {n_dn} · {position} — 참고용(확정 아님)"
    else:
        note = "뚜렷한 5파 미검출 — 상승/하락 스윙 흐름만 표시"

    return ElliottResult(
        pivots=pivots, labeled=labeled, confidence=confidence, direction=direction,
        segments=segments, current_position=position,
        invalidation_price=round(invalidation, 2) if invalidation else None,
        projection=projection, note=note,
    )
