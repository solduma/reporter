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

// 국면별 색(반투명). 2=상승(빨강계)·4=하락(파랑계)·1=바닥(회색)·3=천정(노랑).
const STAGE_COLOR: Record<number, string> = {
  1: "rgba(120, 130, 140, 0.06)",
  2: "rgba(192, 43, 43, 0.08)",
  3: "rgba(232, 163, 61, 0.10)",
  4: "rgba(43, 108, 192, 0.09)",
};

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
  ) {}

  update(): void {
    const ts = this.chart.timeScale();
    const rects: { x1: number; x2: number; color: string }[] = [];
    for (const b of this.bands) {
      const x1 = ts.timeToCoordinate(b.from);
      const x2 = ts.timeToCoordinate(b.to);
      if (x1 !== null && x2 !== null) {
        rects.push({ x1: x1 as number, x2: x2 as number, color: STAGE_COLOR[b.stage] ?? "transparent" });
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

// 국면 구간 목록을 받아 배경밴드를 그리는 series primitive.
export class StageBands implements ISeriesPrimitive<Time> {
  private readonly views: StagePaneView[];

  constructor(chart: IChartApi, bands: StageBand[]) {
    this.views = [new StagePaneView(chart, bands)];
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
