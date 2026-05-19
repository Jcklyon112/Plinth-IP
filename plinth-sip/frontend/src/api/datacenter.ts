import axios from 'axios';
import type { DcAnalysisResult, GridRefreshStatus } from '../types/datacenter';

const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const api = axios.create({ baseURL: BASE });

/** Run the data-center analyzer for a DB-resident parcel. Server-side cached. */
export async function analyzeDataCenter(
  municipalityId: string,
  parcelId: string,
  opts: { useCache?: boolean } = {},
): Promise<DcAnalysisResult> {
  const res = await api.post('/analysis/datacenter', {
    municipality_id: municipalityId,
    parcel_id: parcelId,
    use_cache: opts.useCache !== false,
  });
  return res.data as DcAnalysisResult;
}

/** Run the analyzer on an arbitrary polygon (no DB persistence, no cache). */
export async function analyzeDataCenterShape(
  geojson: GeoJSON.Geometry,
  label?: string,
): Promise<DcAnalysisResult> {
  const res = await api.post('/analysis/datacenter/by-shape', { geojson, label });
  return res.data as DcAnalysisResult;
}

// --- /grid/* -----------------------------------------------------------

function bboxParam(bbox: [number, number, number, number] | null): string {
  return bbox ? bbox.join(',') : '';
}

export interface GridFeatureCollection {
  type: 'FeatureCollection';
  features: Array<{
    type: 'Feature';
    geometry: GeoJSON.Geometry | null;
    properties: Record<string, unknown>;
  }>;
  count: number;
}

export async function fetchSubstations(
  bbox: [number, number, number, number] | null,
  opts: { minVoltageKv?: number; limit?: number } = {},
): Promise<GridFeatureCollection> {
  const params: Record<string, string | number> = { limit: opts.limit ?? 5000 };
  if (bbox) params.bbox = bboxParam(bbox);
  if (opts.minVoltageKv !== undefined) params.min_voltage_kv = opts.minVoltageKv;
  const res = await api.get('/grid/substations', { params });
  return res.data;
}

export async function fetchTransmissionLines(
  bbox: [number, number, number, number] | null,
  opts: { minVoltageKv?: number; limit?: number } = {},
): Promise<GridFeatureCollection> {
  const params: Record<string, string | number> = { limit: opts.limit ?? 5000 };
  if (bbox) params.bbox = bboxParam(bbox);
  if (opts.minVoltageKv !== undefined) params.min_voltage_kv = opts.minVoltageKv;
  const res = await api.get('/grid/transmission-lines', { params });
  return res.data;
}

export async function fetchPowerPlants(
  bbox: [number, number, number, number] | null,
  opts: { fuel?: string; limit?: number } = {},
): Promise<GridFeatureCollection> {
  const params: Record<string, string | number> = { limit: opts.limit ?? 2000 };
  if (bbox) params.bbox = bboxParam(bbox);
  if (opts.fuel) params.fuel = opts.fuel;
  const res = await api.get('/grid/power-plants', { params });
  return res.data;
}

export async function fetchIsoRto(
  bbox: [number, number, number, number] | null,
): Promise<GridFeatureCollection> {
  const params: Record<string, string | number> = {};
  if (bbox) params.bbox = bboxParam(bbox);
  const res = await api.get('/grid/iso-rto', { params });
  return res.data;
}

export async function fetchServiceTerritory(
  utilityIdEia: number | null,
  bbox: [number, number, number, number] | null = null,
): Promise<GridFeatureCollection> {
  const params: Record<string, string | number> = {};
  if (utilityIdEia !== null) params.utility_id_eia = utilityIdEia;
  if (bbox) params.bbox = bboxParam(bbox);
  const res = await api.get('/grid/service-territory', { params });
  return res.data;
}

export async function fetchRefreshStatus(): Promise<GridRefreshStatus> {
  const res = await api.get('/grid/refresh-status');
  return res.data;
}
