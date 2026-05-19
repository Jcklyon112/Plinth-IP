// TypeScript types mirroring app/engine/datacenter/analyzer.py output.
// Intentionally permissive (lots of nullable fields) — the analyzer
// gracefully degrades when a layer hasn't been loaded yet.

export type DcGrade = 'A' | 'B' | 'C' | 'D' | 'F';

export type AcreageTier = 'edge' | 'colo' | 'hyperscale' | 'campus' | null;

export type ZoningCategory =
  | 'industrial'
  | 'heavy_commercial'
  | 'commercial'
  | 'agricultural'
  | 'residential'
  | 'mixed'
  | 'unknown';

export interface NearestSubstation {
  name: string | null;
  operator: string | null;
  maxVoltageKv: number | null;
  distanceMi: number;
  coords: [number, number];          // [lon, lat]
}

export interface NearestTransmissionLine {
  owner: string | null;
  voltageKv: number | null;
  distanceMi: number;
  coords: [number, number];          // snapped point on the line
}

export interface NearestFiber {
  sourceLabel: string | null;
  carrier: string | null;
  distanceMi: number;
  coords: [number, number];
}

export interface NearestGasPipeline {
  operator: string | null;
  distanceMi: number;
  coords: [number, number];
}

export interface IsoBlock {
  name: string;
  fullName: string | null;
  queueDashboardUrl: string | null;
  typicalQueueTimeline: string | null;
  currentPosture: string | null;
}

export interface GridBlock {
  nearestSubstation: NearestSubstation | null;
  nearestTransmissionSubstation: NearestSubstation | null;
  substationsWithin5Mi: Array<{
    id: string;
    name: string | null;
    operator: string | null;
    maxVoltageKv: number | null;
    distanceMi: number;
  }>;
  nearestTransmissionLine: NearestTransmissionLine | null;
  has230kvLineWithin1Mi: boolean;
  transmissionCorridorsWithin5Mi: number;
  dualFeedFeasible: boolean;
  iso: IsoBlock;
}

export interface NearestBaseload {
  name: string | null;
  fuel: string | null;
  capacityMw: number | null;
  distanceMi: number;
}

export interface GenerationBlock {
  nearestBaseload: NearestBaseload | null;
  capacityWithin25MiByFuel: Record<string, number>;
}

export interface PowerBlock {
  utility: string | null;
  industrialRateCentsPerKwh: number | null;
  rateTier: 'Low' | 'Medium' | 'High' | 'Very High' | null;
}

export interface InfrastructureBlock {
  fiberDistanceMi: number | null;
  nearestFiber: NearestFiber | null;
  gasPipelineDistanceMi: number | null;
  nearestGasPipeline: NearestGasPipeline | null;
  floodZone: string | null;
  wetlandCoveragePct: number | null;
  acreage: number | null;
  acreageTier: AcreageTier;
}

export interface Subscores {
  grid: number;
  power_cost: number;
  infrastructure: number;
  land: number;
  generation: number;
  iso: number;
}

export interface DcAnalysisResult {
  parcelId: string | null;
  municipalityId: string | null;
  address: string | null;
  parcelCentroid: [number, number];
  computedAt: string;
  overallScore: DcGrade;
  compositeScore: number;
  subscores: Subscores;
  scoreRationale: string;
  tierFit: string[];
  gatingIssues: string[];
  grid: GridBlock;
  generation: GenerationBlock;
  power: PowerBlock;
  infrastructure: InfrastructureBlock;
  zoning: ZoningCategory;
  warnings: string[];
}

// /grid/refresh-status response
export interface GridRefreshStatus {
  layers: Array<{
    layer_name: string;
    last_refresh_at: string | null;
    feature_count: number | null;
    source_url: string | null;
    source_label: string | null;
    notes: string | null;
  }>;
  grid_data_version: string;
}

// Grid layer keys for URL state and toggle UI
export const GRID_LAYER_KEYS = ['subs', 'lines', 'plants', 'iso', 'utility'] as const;
export type GridLayerKey = (typeof GRID_LAYER_KEYS)[number];

export const GRID_LAYER_LABELS: Record<GridLayerKey, string> = {
  subs: 'Substations',
  lines: 'Transmission Lines',
  plants: 'Power Plants',
  iso: 'ISO/RTO Boundary',
  utility: 'Utility Territory',
};
