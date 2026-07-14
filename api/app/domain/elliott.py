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
# 재귀 상위 등급 배수 — leg 피벗 위에 이 비율 이상 스윙만 상위 파동으로(nesting 보장).
MAJOR_FACTOR = 0.18
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
    """연결형 파동 체인의 한 파동(중단없이 이어짐).

    상승 추세면 motive=상승 5파·corrective=하락 3파가 번갈아 연결되고, 각 파동은 하위 등급 재귀
    라벨(points)을 가진다. degree=primary(최상위) | sub(하위 재귀).
    """

    start_date: str
    end_date: str
    start_price: float
    end_price: float
    degree: str  # primary | sub
    phase: str  # motive | corrective
    direction: str  # up | down (실제 가격 진행 방향)
    bars: int = 0  # 이 파동 소요 봉 수(기간 투영용)
    wave_label: str = ""  # 부모 안 순번: motive='1'..'5' 자식 or 'M'; corrective='A'..'C' or 'C'
    confidence: float = 0.0  # 0~1
    points: list[WavePoint] = field(default_factory=list)  # 내부 라벨 피벗(motive=1~5, corr=A~C)


@dataclass
class WaveProjection:
    """다음 파동 목표 — 가격 구간(zone) + 기간(봉 수) 투영. 파동 크기·속도 기반 피보 예측."""

    wave: str  # 투영 대상(예: '다음 조정' | '다음 추진')
    low: float  # 가격 하한
    high: float  # 가격 상한
    bars_low: int  # 예상 소요 봉 수 하한(직전 파동 기간 x 피보)
    bars_high: int  # 예상 소요 봉 수 상한
    basis: str  # 사람이 읽는 근거


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


def _correction_conf(window: list[Pivot], down: bool) -> float | None:
    """4피벗 A-B-C 지그재그 조정이 규칙을 만족하면 신뢰도, 아니면 None.

    down=True(상승 추세 속 하락 조정)면 고-저-고-저: A 하락, B 반등(A 를 100% 넘게 되돌리지 않음),
    C 하락(A 방향 재개). B 되돌림이 A 의 0.382~0.886 이면 정통 지그재그로 가점.
    """
    kinds = [x.kind for x in window]
    expect = ["high", "low"] if down else ["low", "high"]
    if kinds != [expect[i % 2] for i in range(4)]:
        return None
    p0, p1, p2, p3 = (x.price for x in window)
    s = 1.0 if down else -1.0  # 조정 진행 방향(하락 조정이면 하락이 +)
    a = s * (p0 - p1)  # A 폭
    b = s * (p2 - p1)  # B 되돌림
    c = s * (p2 - p3)  # C 폭
    if a <= 0 or c <= 0 or b <= 0:
        return None
    if b >= a:  # B 가 A 를 100% 이상 되돌리면 지그재그 아님(무효)
        return None
    return _near(b / a, 0.618, 0.4)  # B 되돌림이 0.618 근처일수록 정통 지그재그


# 조정으로 간주할 최소 되돌림(직전 추진 폭 대비). 이보다 작으면 추진의 연장으로 흡수.
_CORRECTION_RETRACE_MIN = 0.5


def _parse_chain(pivots: list[Pivot], up_trend: bool) -> list[tuple[int, int, str, str]]:
    """피벗열을 추진(motive)/조정(corrective) 파동으로 중단없이 교대 분할한다.

    추진은 순추세 방향으로 진행이 이어지는 한 확장하고, 극점 대비 되돌림이 그 진행폭의
    _CORRECTION_RETRACE_MIN 을 넘으면 극점에서 끊어 조정 구간으로 넘긴다. 반환: (start_i, end_i,
    phase, direction) 리스트. 항상 end_i=다음 start_i(gapless).
    """
    n = len(pivots)
    if n < 3:
        return []
    s = 1.0 if up_trend else -1.0
    out: list[tuple[int, int, str, str]] = []
    i = 0
    want_motive = True
    while i < n - 1:
        ext = i  # 현재 파동의 진행 극점(motive=순추세 극값, corrective=반대 극값)
        sgn = s if want_motive else -s  # 이 파동이 진행하는 방향 부호
        j = i
        while j < n - 1:
            j += 1
            if sgn * (pivots[j].price - pivots[ext].price) > 0:
                ext = j  # 진행 극점 갱신
            span = sgn * (pivots[ext].price - pivots[i].price)
            retr = sgn * (pivots[ext].price - pivots[j].price)
            if span > 0 and j > ext and retr >= _CORRECTION_RETRACE_MIN * span:
                break  # 유의미 되돌림 → 극점에서 파동 종료
        if ext <= i:
            ext = min(i + 1, n - 1)  # 진행 없으면 최소 한 다리
        direction = "up" if pivots[ext].price > pivots[i].price else "down"
        out.append((i, ext, "motive" if want_motive else "corrective", direction))
        i = ext
        want_motive = not want_motive
    return out


def _sub_label(pivots: list[Pivot], a: int, b: int, phase: str, up: bool) -> list[WavePoint]:
    """한 파동 구간(a..b)의 내부 전환점을 하위 파동으로 라벨한다 — 하드룰 검증 후에만.

    motive: 구간이 정확히 5개 하위 파동(6피벗)이고 3대 하드룰(R1/R2/R3)을 통과할 때만 1~5 를
    붙인다(진짜 재귀 임펄스). 미달이면 라벨 유보(억지 카운트 금지 — 정직).
    corrective: 3개 하위 파동(4피벗) 형태면 A~B~C. 파동 끝점은 상위 경계 마커와 겹쳐 제외(dedupe).
    """
    window = pivots[a : b + 1]
    if phase == "motive":
        # 구간 안에서 하드룰(R1/R2/R3) 통과하는 6피벗 5파를 찾는다(복합 파동은 부분구간이 통과).
        # 없으면 라벨 유보(억지 카운트 금지 — 정직). 시작 앵커 우선(앞쪽 6피벗부터).
        best: list[Pivot] | None = None
        best_conf = -1.0
        for i in range(len(window) - 5):
            w6 = window[i : i + 6]
            conf = _impulse_conf(w6, up)
            if conf is not None and conf > best_conf:
                best, best_conf = w6, conf
        if best is None:
            return []
        return [
            WavePoint(date=p.date, price=p.price, label=lab)
            for p, lab in zip(best[1:], ["1", "2", "3", "4", "5"], strict=True)
        ]
    # corrective: 구간에서 A-B-C 지그재그 규칙 통과하는 4피벗을 스캔해 검증 후에만 A·B 라벨(C=끝점,
    # 경계 마커와 겹쳐 제외). 미달이면 라벨 유보(motive 와 동일한 정직성). down=조정 자체 방향.
    best_c: list[Pivot] | None = None
    best_cc = -1.0
    for i in range(len(window) - 3):
        w4 = window[i : i + 4]
        cc = _correction_conf(w4, down=not up)  # 조정 파동 방향: 하락 조정이면 down=True
        if cc is not None and cc > best_cc:
            best_c, best_cc = w4, cc
    if best_c is None:
        return []
    return [
        WavePoint(date=best_c[1].date, price=best_c[1].price, label="A"),
        WavePoint(date=best_c[2].date, price=best_c[2].price, label="B"),
    ]


def _wave_confidence(pivots: list[Pivot], a: int, b: int, phase: str, up: bool) -> float:
    """파동 구간이 교과서 형태(motive=5파 하드룰·corrective=3파)에 얼마나 부합하는지 0~1.

    정확히 5파(6피벗)면 하드룰+피보로 채점(고신뢰), 아니면 다리 수 기반 중간 신뢰.
    """
    span = b - a
    if phase == "motive" and span == 5:
        conf = _impulse_conf(pivots[a : b + 1], up)
        if conf is not None:
            return round(max(conf, 0.6), 2)
    if phase == "corrective" and span == 3:
        return 0.55
    # 형태 미달 — 다리 수가 5(추진)/3(조정)에 가까울수록 소폭 신뢰.
    ideal = 5 if phase == "motive" else 3
    return round(0.3 * _near(span, ideal, ideal), 2)


def _build_segments(
    pivots: list[Pivot], sub_pivots: list[Pivot], bar_index: dict[str, int]
) -> list[WaveSegment]:
    """연결형 파동 체인(primary) + 각 파동 내부 하위 라벨(sub) 세그먼트를 만든다.

    bar_index: 날짜→봉 인덱스(파동 소요 봉 수 계산용, 기간 투영에 쓰임).
    """
    if len(pivots) < 3:
        return []
    up_trend = pivots[-1].price >= pivots[0].price
    chain = _parse_chain(pivots, up_trend)
    sub_idx = {p.date: k for k, p in enumerate(sub_pivots)}
    segs: list[WaveSegment] = []
    for a, b, phase, direction in chain:
        conf = _wave_confidence(pivots, a, b, phase, direction == "up")
        # 하위 라벨은 더 촘촘한 sub_pivots 로(프랙탈: 파동 안의 작은 파동).
        sa, sb = sub_idx.get(pivots[a].date), sub_idx.get(pivots[b].date)
        up = direction == "up"
        points = (
            _sub_label(sub_pivots, sa, sb, phase, up)
            if sa is not None and sb is not None and sb > sa
            else _sub_label(pivots, a, b, phase, up)
        )
        bars = bar_index.get(pivots[b].date, 0) - bar_index.get(pivots[a].date, 0)
        segs.append(
            WaveSegment(
                start_date=pivots[a].date, end_date=pivots[b].date,
                start_price=pivots[a].price, end_price=pivots[b].price, degree="primary",
                phase=phase, direction=direction, bars=max(bars, 1),
                wave_label="5파" if phase == "motive" else "3파",
                confidence=conf, points=points,
            )
        )
    return segs


def _project(segments: list[WaveSegment]) -> WaveProjection | None:
    """마지막 완성 파동의 크기로 다음 파동 가격 목표 zone 을 피보로 투영한다.

    마지막이 추진(5파)이면 다음은 조정 → 되돌림 0.382~0.618 zone. 마지막이 조정이면 다음은 추진
    → 직전 추진 크기의 0.618~1.618 연장 zone(파동 크기·속도 기반 예측).
    """
    primary = [s for s in segments if s.degree == "primary"]
    if not primary:
        return None
    last = primary[-1]
    span = abs(last.end_price - last.start_price)
    if span <= 0:
        return None
    # 기간 투영 — 직전 파동 소요 봉 수에 피보 배수(조정은 되돌림≈0.5~1.0배 기간, 추진은 0.618~1.618배).
    if last.phase == "motive":
        r_lo, r_hi = _CORRECTION_RETRACE
        t_lo, t_hi = 0.5, 1.0
        sgn = 1.0 if last.direction == "up" else -1.0
        t1, t2 = last.end_price - sgn * span * r_lo, last.end_price - sgn * span * r_hi
        wave, basis = "다음 조정", f"직전 추진 되돌림 {r_lo}~{r_hi}·기간 {t_lo}~{t_hi}배"
    else:
        r_lo, r_hi = 0.618, 1.618
        t_lo, t_hi = 0.618, 1.618
        sgn = 1.0 if last.direction == "down" else -1.0  # 조정 방향의 반대로 추진
        t1, t2 = last.end_price + sgn * span * r_lo, last.end_price + sgn * span * r_hi
        wave, basis = "다음 추진", f"직전 파동 크기 {r_lo}~{r_hi}배·기간 {t_lo}~{t_hi}배"
    return WaveProjection(
        wave=wave, low=round(min(t1, t2), 2), high=round(max(t1, t2), 2),
        bars_low=max(int(last.bars * t_lo), 1), bars_high=max(int(last.bars * t_hi), 1),
        basis=basis,
    )


def _current_position(segments: list[WaveSegment]) -> tuple[str, float | None]:
    """마지막 완성 파동으로 현재 위치·무효화가격을 낸다."""
    primary = [s for s in segments if s.degree == "primary"]
    if not primary:
        return "뚜렷한 파동 미검출", None
    last = primary[-1]
    kind = "상승" if last.direction == "up" else "하락"
    if last.phase == "motive":
        return f"{kind} 추진 파동 진행/완료 — 반대 조정 대비", last.start_price
    return f"{kind} 조정 진행 — 추세 재개 주시", last.start_price


def analyze(
    prices: list[tuple[str, float]],
    leg_threshold: float = LEG_THRESHOLD,
) -> ElliottResult:
    """전 구간을 상승 추진↔하락 조정으로 중단없이 연결한 파동 체인 + 하위 재귀 라벨 + 투영."""
    pivots = zigzag(prices, leg_threshold)
    if len(pivots) < 3:
        return ElliottResult(
            pivots=pivots, labeled=False, confidence=0.0, direction="none",
            current_position="피벗 부족", note="피벗 부족",
        )

    sub_pivots = zigzag(prices, leg_threshold * 0.6)  # 프랙탈 하위 등급(더 촘촘)
    bar_index = {d: i for i, (d, _) in enumerate(prices)}  # 날짜→봉 인덱스(기간 투영)
    segments = _build_segments(pivots, sub_pivots, bar_index)
    position, invalidation = _current_position(segments)
    projection = _project(segments)

    primary = [s for s in segments if s.degree == "primary"]
    labeled = len(primary) > 0
    n_mot = sum(1 for s in primary if s.phase == "motive")
    n_cor = sum(1 for s in primary if s.phase == "corrective")
    direction = "up" if pivots[-1].price >= pivots[0].price else "down"
    confidence = round(sum(s.confidence for s in primary) / len(primary), 2) if primary else 0.0
    note = (
        f"추진 {n_mot}·조정 {n_cor} 파동 연결 · {position} — 참고용(확정 아님)"
        if labeled else "파동 구조 미검출"
    )
    return ElliottResult(
        pivots=pivots, labeled=labeled, confidence=confidence, direction=direction,
        segments=segments, current_position=position,
        invalidation_price=round(invalidation, 2) if invalidation else None,
        projection=projection, note=note,
    )
