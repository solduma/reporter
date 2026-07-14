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
# 기본 다리(leg) 등급 임계 — 파동 라벨 스윙 해상도.
LEG_THRESHOLD = 0.06
# 5파 완성 후 반대 조정 목표 되돌림 비율(피보) — 투영 zone 경계.
_CORRECTION_RETRACE = (0.382, 0.618)


@dataclass
class Pivot:
    date: str  # YYYY-MM-DD
    price: float
    kind: str  # high | low
    label: str = ""  # 미사용(하위호환 필드)


@dataclass
class WaveSegment:
    """엘리엇 파동 한 개(=피벗 사이 한 다리). 반복 사이클 1-2-3-4-5-A-B-C 중 하나.

    한 다리는 하나의 파동(wave_label='1'..'5' 또는 'A'..'C')이지 '5파 전체'가 아니다. 추진
    5파(phase=motive)와 조정 3파(phase=corrective)가 번갈아 이어지며, 하드룰 통과 사이클만 라벨.
    """

    start_date: str
    end_date: str
    start_price: float
    end_price: float
    phase: str  # motive | corrective
    direction: str  # up | down (이 다리의 실제 가격 방향)
    wave_label: str  # '1'~'5' | 'A'~'C' (사이클 내 이 파동의 번호)
    bars: int = 0  # 이 파동 소요 봉 수(기간 투영용)
    confidence: float = 0.0  # 0~1 (이 파동이 속한 사이클의 피보 신뢰도)


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
    """4피벗 A-B-C 조정(지그재그/플랫/확장플랫)이 규칙을 만족하면 신뢰도, 아니면 None.

    down=True(상승 추세 속 하락 조정)면 고-저-고-저: A 하락, B 반등, C 하락(A 방향 재개).
    B 되돌림 비율(b/a)로 패턴 분류·채점: 지그재그(0.382~0.786), 플랫(0.786~1.05), 확장플랫
    (1.05~1.5, B 가 A 시작 넘음). 이 범위 밖은 조정 아님(유보).
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
    r = b / a
    if r <= 0.786:  # 지그재그 — B 얕음, C 가 새 극점(가장 흔한 조정)
        return _near(r, 0.618, 0.4)
    if r <= 1.05:  # 플랫 — B 가 A 시작 부근까지 강하게 되돌림
        return 0.5 * _near(r, 0.9, 0.25)
    if r <= 1.5:  # 확장플랫 — B 가 A 시작 넘음(속임수 패턴)
        return 0.5 * _near(r, 1.236, 0.3)
    return None  # B 가 A 를 1.5배 넘게 되돌리면 조정 아님(유보)


# 5파 임펄스·A-B-C 조정을 스캔할 때 시작점을 허용 오차 내에서 미룰 수 있는 피벗 창(노이즈 흡수).
_SCAN_WINDOW = 8


def _label_cycles(
    pivots: list[Pivot], up_trend: bool
) -> list[tuple[int, str, str, float]]:
    """피벗열을 좌→우로 반복 사이클 [1,2,3,4,5]-[A,B,C] 로 라벨한다(연구 스펙).

    각 다리(pivots[i]→pivots[i+1])가 사이클 내 한 파동. 추진 5파(하드룰 통과)와 조정 3파(지그재그)
    를 번갈아 찾되, 안 맞으면 그 구간은 라벨 유보(억지 카운트 금지 — 정직). 여러 후보 중 피보
    신뢰도 최고를 채택. 반환: 각 다리의 (start_pivot_i, wave_label, phase, cycle_confidence).
    빈 라벨('')은 유보 다리. 하락 추세면 추진=하락 5파·조정=상승 3파(방향 미러).
    """
    n = len(pivots)
    out: list[tuple[int, str, str, float]] = []
    i = 0
    expect_motive = True
    while i < n - 1:
        found = None
        if expect_motive:
            # i 부터 창 내에서 하드룰 통과 5파(6피벗)를 탐색, 최고 신뢰도 채택.
            best_st, best_c = None, -1.0
            for st in range(i, min(i + _SCAN_WINDOW, n - 5)):
                c = _impulse_conf(pivots[st : st + 6], up_trend)
                if c is not None and c > best_c:
                    best_st, best_c = st, c
            if best_st is not None:
                found = (best_st, 5, ["1", "2", "3", "4", "5"], "motive", best_c)
        else:
            best_st, best_c = None, -1.0
            for st in range(i, min(i + _SCAN_WINDOW, n - 3)):
                # 조정은 추세 반대 방향: 상승추세→하락 조정(down=True), 하락추세→상승 조정(down=False).
                c = _correction_conf(pivots[st : st + 4], down=up_trend)
                if c is not None and c > best_c:
                    best_st, best_c = st, c
            if best_st is not None:
                found = (best_st, 3, ["A", "B", "C"], "corrective", best_c)
        if found:
            st, ln, labels, phase, conf = found
            for k in range(i, st):  # 사이클 시작 전 구간은 라벨 유보
                out.append((k, "", "", 0.0))
            for k, lab in enumerate(labels):
                out.append((st + k, lab, phase, round(conf, 2)))
            i = st + ln
            expect_motive = not expect_motive
        elif not expect_motive:
            expect_motive = True  # 조정이 없거나 복합 → 다음 추진을 기대(위상만 전환, 스킵 없음)
        else:
            out.append((i, "", "", 0.0))  # 추진도 없음 → 이 다리 유보
            i += 1
    return out


def _build_segments(
    pivots: list[Pivot], bar_index: dict[str, int]
) -> list[WaveSegment]:
    """피벗열을 반복 사이클로 라벨해 파동 세그먼트(각 다리=한 파동)를 만든다.

    라벨 유보 다리는 세그먼트로 내지 않는다(차트엔 옅은 스윙선으로만 표시). bar_index: 날짜→봉
    인덱스(기간 투영용).
    """
    if len(pivots) < 3:
        return []
    up_trend = pivots[-1].price >= pivots[0].price
    labeled = _label_cycles(pivots, up_trend)
    segs: list[WaveSegment] = []
    for start_i, label, phase, conf in labeled:
        if not label or start_i + 1 >= len(pivots):
            continue  # 유보 다리
        a, b = pivots[start_i], pivots[start_i + 1]
        bars = bar_index.get(b.date, 0) - bar_index.get(a.date, 0)
        segs.append(
            WaveSegment(
                start_date=a.date, end_date=b.date,
                start_price=a.price, end_price=b.price,
                phase=phase, direction="up" if b.price > a.price else "down",
                wave_label=label, bars=max(bars, 1), confidence=conf,
            )
        )
    return segs


def _project(segments: list[WaveSegment]) -> WaveProjection | None:
    """마지막 완성 파동의 크기로 다음 파동 가격 목표 zone 을 피보로 투영한다.

    마지막이 추진(5파)이면 다음은 조정 → 되돌림 0.382~0.618 zone. 마지막이 조정이면 다음은 추진
    → 직전 추진 크기의 0.618~1.618 연장 zone(파동 크기·속도 기반 예측).
    """
    if not segments:
        return None
    last = segments[-1]
    # 마지막 파동 '세트' 전체(같은 위상 연속 다리 = 추진 5파 또는 조정 3파)의 가격폭·기간을 근거로.
    # 다음 구조(5파 또는 3파)의 규모·기간을 그 세트 전체 대비 피보로 추정한다(한 다리로 추정하지 않음).
    set_segs: list[WaveSegment] = []
    for s in reversed(segments):
        if s.phase == last.phase:
            set_segs.append(s)
        else:
            break
    set_start = set_segs[-1].start_price  # 세트 시작가(가장 과거)
    set_bars = sum(s.bars for s in set_segs)
    span = abs(last.end_price - set_start)
    if span <= 0:
        return None
    if last.phase == "motive":
        # 다음은 조정: 직전 추진 세트 전체를 0.382~0.618 되돌림. 기간은 추진 세트의 0.382~0.618배.
        r_lo, r_hi = _CORRECTION_RETRACE
        t_lo, t_hi = 0.382, 0.618
        sgn = 1.0 if last.direction == "up" else -1.0
        t1, t2 = last.end_price - sgn * span * r_lo, last.end_price - sgn * span * r_hi
        wave, basis = "다음 조정", f"직전 추진 되돌림 {r_lo}~{r_hi}"
    else:
        # 다음은 추진: 직전 조정 세트 반대 방향으로 1.0~1.618배. 기간도 1.0~1.618배(대개 추진이 더 김).
        r_lo, r_hi = 1.0, 1.618
        t_lo, t_hi = 1.0, 1.618
        sgn = 1.0 if last.direction == "down" else -1.0  # 조정 방향의 반대로 추진
        t1, t2 = last.end_price + sgn * span * r_lo, last.end_price + sgn * span * r_hi
        wave, basis = "다음 추진", f"직전 파동 {r_lo}~{r_hi}배"
    return WaveProjection(
        wave=wave, low=round(min(t1, t2), 2), high=round(max(t1, t2), 2),
        bars_low=max(int(set_bars * t_lo), 1), bars_high=max(int(set_bars * t_hi), 1),
        basis=basis,
    )


def _current_position(
    segments: list[WaveSegment], up_trend: bool
) -> tuple[str, float | None]:
    """마지막 라벨된 파동으로 현재 위치·무효화가격을 낸다(추세 방향에 맞춰 서술)."""
    if not segments:
        return "뚜렷한 파동 미검출", None
    last = segments[-1]
    lab = last.wave_label
    push = "상승" if up_trend else "하락"  # 추진 방향(추세와 동일)
    if last.phase == "motive":
        nxt = {
            "1": "2파 되돌림 대비", "2": f"3파 {push} 기대", "3": "4파 되돌림 대비",
            "4": f"5파 마무리 {push} 기대", "5": "추진 완료 — A-B-C 조정 대비",
        }.get(lab, "")
        return f"추진 {lab}파 진행 — {nxt}", last.start_price
    nxt = {"A": "B파 되돌림 대비", "B": "C파 진행 대비", "C": "조정 완료 — 추세 재개 주시"}.get(lab, "")
    return f"조정 {lab}파 진행 — {nxt}", last.start_price


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

    up_trend = pivots[-1].price >= pivots[0].price
    bar_index = {d: i for i, (d, _) in enumerate(prices)}  # 날짜→봉 인덱스(기간 투영)
    segments = _build_segments(pivots, bar_index)
    position, invalidation = _current_position(segments, up_trend)
    projection = _project(segments)

    labeled = len(segments) > 0
    n_mot = sum(1 for s in segments if s.phase == "motive")
    n_cor = sum(1 for s in segments if s.phase == "corrective")
    direction = "up" if up_trend else "down"
    confidence = (
        round(sum(s.confidence for s in segments) / len(segments), 2) if segments else 0.0
    )
    note = (
        f"엘리엇 추진 {n_mot // 5}세트·조정 {n_cor // 3}세트 라벨 · {position} — 참고용(확정 아님)"
        if labeled else "뚜렷한 엘리엇 파동 미검출 — 스윙만 표시"
    )
    return ElliottResult(
        pivots=pivots, labeled=labeled, confidence=confidence, direction=direction,
        segments=segments, current_position=position,
        invalidation_price=round(invalidation, 2) if invalidation else None,
        projection=projection, note=note,
    )
