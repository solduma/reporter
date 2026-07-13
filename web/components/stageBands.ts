import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";

// 와인스타인 국면 배경밴드 오버레이(lightweight-charts v5 primitives). 국면별 시간 구간을
// 반투명 색으로 캔들 뒤(zOrder bottom)에 칠해 "지금 어느 국면 구간인지"를 한눈에 보인다.
// timeDividers.ts 와 동일한 primitive 구조.

// 국면별 배경밴드 색(반투명). 2=상승(빨강계)·4=하락(파랑계)·1=바닥(회색)·3=천정(노랑).
const STAGE_COLOR: Record<number, string> = {
  1: "rgba(120, 130, 140, 0.06)",
  2: "rgba(192, 43, 43, 0.08)",
  3: "rgba(232, 163, 61, 0.10)",
  4: "rgba(43, 108, 192, 0.09)",
};

// 레전드용 국면 스와치(배경밴드보다 진한 대표색) + 라벨. 배경밴드 색과 톤을 맞춘 단일 소스.
export const STAGE_LEGEND: { stage: number; label: string; swatch: string }[] = [
  { stage: 1, label: "Stg 1", swatch: "rgba(120, 130, 140, 0.45)" },
  { stage: 2, label: "Stg 2", swatch: "rgba(192, 43, 43, 0.45)" },
  { stage: 3, label: "Stg 3", swatch: "rgba(232, 163, 61, 0.55)" },
  { stage: 4, label: "Stg 4", swatch: "rgba(43, 108, 192, 0.45)" },
];

export interface StageBand {
  stage: number;
  from: Time; // 구간 시작 시각(YYYY-MM-DD)
  to: Time;
}

interface BitmapScope {
  context: CanvasRenderingContext2D;
  bitmapSize: { width: number; height: number };
  horizontalPixelRatio: number;
  verticalPixelRatio: number;
}
interface RenderTarget {
  useBitmapCoordinateSpace(f: (scope: BitmapScope) => void): void;
}

class StageRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly rects: { x1: number; x2: number; color: string }[]) {}

  draw(target: RenderTarget): void {
    if (this.rects.length === 0) {
      return;
    }
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const ratio = scope.horizontalPixelRatio;
      ctx.save();
      for (const r of this.rects) {
        ctx.fillStyle = r.color;
        const left = Math.round(r.x1 * ratio);
        const width = Math.max(1, Math.round(r.x2 * ratio) - left);
        ctx.fillRect(left, 0, width, scope.bitmapSize.height);
      }
      ctx.restore();
    });
  }
}

class StagePaneView implements IPrimitivePaneView {
  private rects: { x1: number; x2: number; color: string }[] = [];

  constructor(
    private readonly chart: IChartApi,
    private readonly bands: StageBand[],
    // 차트 축에 실제로 존재하는 캔들 시각(오름차순). 밴드 경계를 이 축에 스냅하는 데 쓴다.
    private readonly axisTimes: number[],
  ) {}

  // 밴드 경계(임의의 일봉 날짜)를 차트 축 좌표로. 축에 정확히 없는 시각(주봉·분봉)이면
  // timeToCoordinate 가 null 을 주므로, 축 캔들 시각 중 가장 가까운 것으로 스냅한 뒤 좌표화한다.
  private coord(time: Time): number | null {
    const ts = this.chart.timeScale();
    const direct = ts.timeToCoordinate(time);
    if (direct !== null) {
      return direct as number;
    }
    if (this.axisTimes.length === 0) {
      return null;
    }
    const target = toEpoch(time);
    // 가장 가까운 축 시각 이진 탐색.
    let lo = 0;
    let hi = this.axisTimes.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (this.axisTimes[mid] < target) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    // lo 는 target 이상 첫 인덱스. 이웃과 비교해 더 가까운 쪽 선택.
    const cand = [this.axisTimes[lo]];
    if (lo > 0) {
      cand.push(this.axisTimes[lo - 1]);
    }
    let best: number | null = null;
    let bestDist = Infinity;
    for (const c of cand) {
      const x = ts.timeToCoordinate(epochToTime(c, time));
      if (x === null) {
        continue;
      }
      const dist = Math.abs(c - target);
      if (dist < bestDist) {
        bestDist = dist;
        best = x as number;
      }
    }
    return best;
  }

  update(): void {
    const rects: { x1: number; x2: number; color: string }[] = [];
    for (const b of this.bands) {
      const x1 = this.coord(b.from);
      const x2 = this.coord(b.to);
      if (x1 !== null && x2 !== null && x2 > x1) {
        rects.push({ x1, x2, color: STAGE_COLOR[b.stage] ?? "transparent" });
      }
    }
    this.rects = rects;
  }

  renderer(): IPrimitivePaneRenderer {
    return new StageRenderer(this.rects);
  }

  // 캔들·거래량 아래(배경)에 그려 시세를 가리지 않는다.
  zOrder(): "bottom" {
    return "bottom";
  }
}

// Time(YYYY-MM-DD 문자열 또는 UTCTimestamp 초) → epoch 초. 스냅 거리 비교용.
function toEpoch(time: Time): number {
  if (typeof time === "number") {
    return time;
  }
  if (typeof time === "string") {
    return Date.parse(`${time}T00:00:00Z`) / 1000;
  }
  // BusinessDay 객체.
  const d = time as { year: number; month: number; day: number };
  return Date.UTC(d.year, d.month - 1, d.day) / 1000;
}

// epoch 초 → 원본 Time 과 같은 표현(문자열축이면 YYYY-MM-DD, 숫자축이면 초)으로 되돌린다.
function epochToTime(epoch: number, sample: Time): Time {
  if (typeof sample === "number") {
    return epoch as Time;
  }
  return new Date(epoch * 1000).toISOString().slice(0, 10) as Time;
}

// 국면 구간 목록을 받아 배경밴드를 그리는 series primitive.
export class StageBands implements ISeriesPrimitive<Time> {
  private readonly views: StagePaneView[];

  constructor(chart: IChartApi, bands: StageBand[], axisTimes: number[] = []) {
    this.views = [new StagePaneView(chart, bands, axisTimes)];
  }

  attached(_param: SeriesAttachedParameter<Time, SeriesType>): void {}

  detached(): void {}

  updateAllViews(): void {
    for (const v of this.views) {
      v.update();
    }
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }
}
