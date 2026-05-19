export type RuleResult = 'pass' | 'conditional' | 'fail' | 'unknown';

export type Tier = 1 | 2 | 3 | 4;

export interface RuleResultDetail {
  rule_id: string;
  rule_category: string;
  result: RuleResult;
  explanation: string;
  assumptions_used: Record<string, unknown>;
  confidence: number | null;
}

export interface ScoreBreakdownCategory {
  score: number;
  weight: number;
  rules_evaluated: number;
}

export interface TemplateFit {
  template_id: string;
  template_name: string;
  footprint_area_sqft: number;
  fit_status: 'fits' | 'does_not_fit' | 'unknown';
  notes: string;
}

export interface Blocker {
  rule_id: string;
  explanation: string;
}

export interface SlopeStats {
  min: number;
  max: number;
  mean: number;
  stddev: number;
  count: number;
  source?: string;
  pixel_size_m?: number;
}

export interface OverlayHit {
  layer_id: string;
  label: string;
  constraint_level: 'hard_block' | 'review' | 'review_required' | 'soft_constraint';
  buffer_ft?: number;
  attributes?: Record<string, unknown>;
}

export interface ParcelProperties {
  parcel_id: string;
  municipality_id: string;
  address: string | null;
  owner_name: string | null;
  zoning_code: string | null;
  zoning_district: string | null;
  zoning_district_label: string | null;
  lot_area_sqft: number | null;
  land_use_type: string | null;
  assessed_use: string | null;
  existing_structure_count: number | null;
  score: number | null;
  tier: Tier | null;
  confidence: number | null;
  score_breakdown: Record<string, ScoreBreakdownCategory> | null;
  blockers: Blocker[] | null;
  rule_results: RuleResultDetail[] | null;
  template_fits: TemplateFit[] | null;
  // Live GIS-derived signals (populated by shape_analyzer)
  year_built: number | null;
  slope_stats: SlopeStats | null;
  soil_septic_class: 'Not limited' | 'Somewhat limited' | 'Very limited' | 'Not rated' | null;
  overlay_hits: OverlayHit[] | null;
  constraints_flags: string[] | null;
}

export interface ParcelFeature {
  type: 'Feature';
  geometry: GeoJSON.Geometry;
  properties: ParcelProperties;
}

export interface ParcelCollection {
  type: 'FeatureCollection';
  features: ParcelFeature[];
  total: number;
}

export interface ParcelDetail {
  parcel: ParcelProperties;
  rule_results: RuleResultDetail[];
  analyst: AnalystRecord | null;
}

export interface AnalystRecord {
  review_status: string;
  outreach_status: string;
  next_step: string | null;
  notes: string | null;
  flagged: boolean;
  analyst: string | null;
  confidence_override: number | null;
  rule_overrides: Record<string, unknown> | null;
}

export interface FilterState {
  tier: number | null;
  minScore: number | null;
  zoningCode: string | null;
}
