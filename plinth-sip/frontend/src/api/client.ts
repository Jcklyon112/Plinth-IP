import axios from 'axios';

const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({ baseURL: BASE });

// ──────────────────────────────────────────────────────────────────────
// Rent estimate types
// ──────────────────────────────────────────────────────────────────────

export interface RentComparable {
  address: string;
  bedrooms: number | null;
  bathrooms: number | null;
  square_footage: number | null;
  rent_monthly: number | null;
  distance_mi: number | null;
  days_on_market: number | null;
  correlation: number | null;
}

export interface RentSpecEstimate {
  spec_key: string;
  spec_label: string;
  bedrooms: number;
  bathrooms: number;
  square_footage: number;
  rent: number | null;
  rent_low: number | null;
  rent_high: number | null;
  rent_psf_month: number | null;
  comparables: RentComparable[];
  error: string | null;
}

export interface RentEstimateResponse {
  address: string;
  applied_adu_premium: boolean;
  source: 'rentcast' | 'hud_fmr';
  source_note?: string | null;
  model_label?: string | null;
  estimate: RentSpecEstimate;
}

export interface RentEstimateRequest {
  address: string;
  bedrooms: number;
  bathrooms: number;
  square_footage: number;
  property_type?: string;       // default Single Family
  apply_adu_premium?: boolean;  // default false
  model_label?: string;
}

export async function fetchRentEstimate(req: RentEstimateRequest): Promise<RentEstimateResponse> {
  const res = await api.post('/rent-estimate', req);
  return res.data;
}
