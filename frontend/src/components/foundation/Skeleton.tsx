/** Loading skeletons: the layout of what is coming, never fake numbers. */
import type { CSSProperties } from "react";

export function Skeleton({ width = "100%", height = 14, style }: { width?: number | string; height?: number | string; style?: CSSProperties }) {
  return <span className="skeleton" style={{ width, height, ...style }} aria-hidden="true" />;
}

export function KpiSkeleton() {
  return (
    <div className="kpi skeleton-card" aria-hidden="true">
      <Skeleton width="45%" height={11} />
      <Skeleton width="60%" height={28} style={{ marginTop: 12 }} />
      <Skeleton width="80%" height={10} style={{ marginTop: 10 }} />
    </div>
  );
}

export function PanelSkeleton({ height = 220 }: { height?: number }) {
  return (
    <div className="panel" aria-hidden="true">
      <Skeleton width="35%" height={13} />
      <Skeleton width="55%" height={10} style={{ marginTop: 8 }} />
      <Skeleton height={height} style={{ marginTop: 16 }} />
    </div>
  );
}

/** Generic page placeholder used while Gold data loads. */
export function PageSkeleton({ label = "Loading Gold data" }: { label?: string }) {
  return (
    <div className="page" role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">{label}…</span>
      <Skeleton width={180} height={11} />
      <Skeleton width={420} height={26} style={{ marginTop: 10 }} />
      <Skeleton width={620} height={12} style={{ marginTop: 12, maxWidth: "100%" }} />
      <div className="grid g4" style={{ marginTop: 26 }}>
        <KpiSkeleton /><KpiSkeleton /><KpiSkeleton /><KpiSkeleton />
      </div>
      <div className="grid g2 section">
        <PanelSkeleton /><PanelSkeleton />
      </div>
    </div>
  );
}
