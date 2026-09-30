import { Suspense, lazy, useState, type ComponentType, type LazyExoticComponent } from "react";
import { Skeleton } from "../foundation/Skeleton";
import { VEHICLE_SYSTEMS, type VehicleRendererId, type VehicleRendererProps, type VehicleSystemId, type VehicleVisualizationProps } from "./types";

/**
 * Renderer registry. Renderers are code-split (loaded on first use), so a heavier renderer
 * (e.g. a future 3D one) never lands in the initial bundle. Register new renderers here only.
 */
export const RENDERERS: Partial<Record<VehicleRendererId, LazyExoticComponent<ComponentType<VehicleRendererProps>>>> = {
  svg: lazy(() => import("./SvgTruckRenderer")),
};

/** Register an additional renderer (e.g. a future 3D view) without touching pages. */
export const registerRenderer = (id: VehicleRendererId, loader: () => Promise<{ default: ComponentType<VehicleRendererProps> }>) => {
  RENDERERS[id] = lazy(loader);
};

function Unavailable({ caption }: { caption?: string }) {
  return (
    <div className="state inline" role="img" aria-label={caption ?? "Vehicle view"}>
      <p className="small muted" style={{ margin: 0 }}>Vehicle view not available in this build.</p>
    </div>
  );
}

/**
 * The single entry point pages use to show the vehicle. Owns hover state; selection is controlled
 * by the page via `activeSystem` / `onSystemSelect`.
 */
export default function VehicleVisualization({ renderer = "svg", variant = "inspect", ...props }: VehicleVisualizationProps) {
  const [hovered, setHovered] = useState<VehicleSystemId | null>(null);
  const Renderer = RENDERERS[renderer] ?? RENDERERS.svg;
  if (!Renderer) return <Unavailable caption={props.caption} />;
  return (
    <Suspense fallback={<Skeleton height={variant === "compact" ? 180 : 360} />}>
      <Renderer {...props} variant={variant} systems={VEHICLE_SYSTEMS} hoveredSystem={hovered} onSystemHover={setHovered} />
    </Suspense>
  );
}
