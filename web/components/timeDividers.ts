import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";

// 시간 구분 수직선(붉은 점선) 오버레이. lightweight-charts v5 primitives API 사용.
// 30분봉=일 경계, 일봉=월 경계, 주봉=연 경계처럼 경계 시각 목록을 받아 x좌표에 그린다.
const DIVIDER_COLOR = "rgba(192, 43, 43, 0.5)"; // COLOR_UP(#c02b2b) 반투명 — 캔들과 구분
const DASH: [number, number] = [3, 3];

// fancy-canvas 는 직접 의존이 아니므로(lightweight-charts 의 transitive) 타입 임포트 대신
// draw 가 실제로 쓰는 최소 형태만 구조적으로 정의한다.
interface BitmapScope {
  context: CanvasRenderingContext2D;
  bitmapSize: { width: number; height: number };
  horizontalPixelRatio: number;
  verticalPixelRatio: number;
}
interface RenderTarget {
  useBitmapCoordinateSpace(f: (scope: BitmapScope) => void): void;
}

class DividerRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly xs: number[]) {}

  draw(target: RenderTarget): void {
    if (this.xs.length === 0) {
      return;
    }
    target.useBitmapCoordinateSpace((scope) => {
      const ctx = scope.context;
      const ratio = scope.horizontalPixelRatio;
      ctx.save();
      ctx.strokeStyle = DIVIDER_COLOR;
      ctx.lineWidth = Math.max(1, Math.floor(scope.verticalPixelRatio));
      ctx.setLineDash(DASH.map((d) => d * scope.verticalPixelRatio));
      for (const x of this.xs) {
        const px = Math.round(x * ratio) + 0.5;
        ctx.beginPath();
        ctx.moveTo(px, 0);
        ctx.lineTo(px, scope.bitmapSize.height);
        ctx.stroke();
      }
      ctx.restore();
    });
  }
}

class DividerPaneView implements IPrimitivePaneView {
  private xs: number[] = [];

  constructor(
    private readonly chart: IChartApi,
    private readonly times: Time[],
  ) {}

  update(): void {
    const ts = this.chart.timeScale();
    const xs: number[] = [];
    for (const t of this.times) {
      const c = ts.timeToCoordinate(t);
      if (c !== null) {
        xs.push(c as number); // Coordinate 는 브랜디드 number
      }
    }
    this.xs = xs;
  }

  renderer(): IPrimitivePaneRenderer {
    return new DividerRenderer(this.xs);
  }

  // 캔들·거래량 아래(배경)에 그려 시세를 가리지 않는다.
  zOrder(): "bottom" {
    return "bottom";
  }
}

// 경계 시각 목록을 받아 수직 점선을 그리는 series primitive.
export class TimeDividers implements ISeriesPrimitive<Time> {
  private readonly views: DividerPaneView[];
  private requestUpdate?: () => void;

  constructor(chart: IChartApi, times: Time[]) {
    this.views = [new DividerPaneView(chart, times)];
  }

  attached(param: SeriesAttachedParameter<Time, SeriesType>): void {
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.requestUpdate = undefined;
  }

  updateAllViews(): void {
    for (const v of this.views) {
      v.update();
    }
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }
}
