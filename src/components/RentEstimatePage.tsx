import React, { useEffect, useRef, useState } from 'react';
import maplibregl, { Map as MlMap, Marker } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

import { fetchRentEstimate } from '../api/client';
import type { RentEstimateResponse, RentComparable } from '../api/client';
import {
  PLINTH_MODELS,
  DEFAULT_MODEL_ID,
  ASSUMPTIONS,
  MONTH_LABELS,
  seasonalAnnualRevenue,
  type PlinthModel,
} from '../config';
import { ProFormaPanel } from './ProFormaPanel';

// ──────────────────────────────────────────────────────────────────────
// Map config
// ──────────────────────────────────────────────────────────────────────

const NYC_CENTER: [number, number] = [-74.006, 40.7128];
const NYC_ZOOM = 11;
const ADDRESS_ZOOM = 16.5;
const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json';

// ──────────────────────────────────────────────────────────────────────
// Address autocomplete (Photon)
// ──────────────────────────────────────────────────────────────────────

interface Suggestion { label: string; lat: number; lon: number; }

async function photonSuggest(query: string): Promise<Suggestion[]> {
  if (query.trim().length < 3) return [];
  const url = 'https://photon.komoot.io/api/?' + new URLSearchParams({
    q: query, limit: '6', lang: 'en',
  }).toString();
  try {
    const r = await fetch(url);
    if (!r.ok) return [];
    const data = await r.json();
    const features = Array.isArray(data?.features) ? data.features : [];
    const out: Suggestion[] = [];
    for (const f of features) {
      const p = f.properties || {};
      if (p.country !== 'United States') continue;
      const street = [p.housenumber, p.street].filter(Boolean).join(' ');
      const city = p.city || p.town || p.village || p.county || '';
      const region = p.state || '';
      const postcode = p.postcode || '';
      const head = street || p.name || '';
      const tail = [city, region, postcode].filter(Boolean).join(', ');
      const label = [head, tail].filter(Boolean).join(', ');
      if (!label) continue;
      const c = f.geometry?.coordinates;
      if (!Array.isArray(c) || c.length < 2) continue;
      out.push({ label, lat: c[1], lon: c[0] });
    }
    return out;
  } catch { return []; }
}

// ──────────────────────────────────────────────────────────────────────
// Net income math (sidebar preview — pro forma is canonical)
// ──────────────────────────────────────────────────────────────────────

function netAnnualIncome(annualGrossRent: number, modelPrice: number, funding: 'cash' | 'finance'): {
  net: number;
  expenses: { vacancy: number; tax: number; insurance: number; maintenance: number; management: number; debt: number };
} {
  const a = ASSUMPTIONS;
  const vacancy = annualGrossRent * a.vacancyPct;
  const tax = modelPrice * a.propertyTaxRate;
  const insurance = a.insuranceAnnual;
  const maintenance = annualGrossRent * a.maintenancePct;
  const management = annualGrossRent * a.managementPct;

  let debt = 0;
  if (funding === 'finance') {
    const principal = modelPrice * (1 - a.financeDownPct);
    const i = a.financeRate / 12;
    const n = a.financeTermYears * 12;
    const m = i === 0 ? principal / n : (principal * i) / (1 - Math.pow(1 + i, -n));
    debt = m * 12;
  }

  const net = annualGrossRent - vacancy - tax - insurance - maintenance - management - debt;
  return { net, expenses: { vacancy, tax, insurance, maintenance, management, debt } };
}

// ──────────────────────────────────────────────────────────────────────
// Formatting
// ──────────────────────────────────────────────────────────────────────

const fmtUsd = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

// ──────────────────────────────────────────────────────────────────────
// Page
// ──────────────────────────────────────────────────────────────────────

const ALL_MONTHS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];

export const RentEstimatePage: React.FC = () => {
  // Intake
  const [address, setAddress] = useState('');
  const [modelId, setModelId] = useState<PlinthModel['id']>(DEFAULT_MODEL_ID);
  const [yearRound, setYearRound] = useState(true);
  const [selectedMonths, setSelectedMonths] = useState<number[]>(ALL_MONTHS);
  const [funding, setFunding] = useState<'cash' | 'finance'>('cash');

  // Status + result
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RentEstimateResponse | null>(null);
  const [showProForma, setShowProForma] = useState(false);

  // Autocomplete
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [activeSuggest, setActiveSuggest] = useState(-1);
  const suggestReqRef = useRef(0);
  const debounceRef = useRef<number | null>(null);

  // Map refs
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const markerRef = useRef<Marker | null>(null);

  // Initialize map once
  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      style: MAP_STYLE,
      center: NYC_CENTER,
      zoom: NYC_ZOOM,
      attributionControl: { compact: true },
      pitchWithRotate: false,
      dragRotate: false,
    });
    map.scrollZoom.disable();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false, visualizePitch: false }), 'bottom-right');
    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  // Debounced autocomplete
  useEffect(() => {
    if (debounceRef.current != null) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    if (!suggestOpen || address.trim().length < 3) {
      setSuggestions([]);
      return;
    }
    const q = address;
    debounceRef.current = window.setTimeout(() => {
      const reqId = ++suggestReqRef.current;
      photonSuggest(q).then(s => {
        if (reqId === suggestReqRef.current) {
          setSuggestions(s);
          setActiveSuggest(-1);
        }
      });
    }, 250);
    return () => {
      if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    };
  }, [address, suggestOpen]);

  const flyTo = (lat: number, lon: number) => {
    const map = mapRef.current;
    if (!map) return;
    if (markerRef.current) markerRef.current.remove();
    const el = document.createElement('div');
    el.className = 'pl-marker';
    markerRef.current = new maplibregl.Marker({ element: el, anchor: 'center' })
      .setLngLat([lon, lat]).addTo(map);
    map.flyTo({ center: [lon, lat], zoom: ADDRESS_ZOOM, duration: 1800, essential: true });
  };

  const selectedModel = PLINTH_MODELS.find(m => m.id === modelId)!;

  const runEstimate = async (lat?: number, lon?: number, addressOverride?: string) => {
    const a = (addressOverride ?? address).trim();
    if (a.length < 5) {
      setError('Enter a full street address with city, state, and ZIP.');
      setStatus('error');
      return;
    }
    if (!yearRound && selectedMonths.length === 0) {
      setError('Pick at least one rental month, or switch to year-round.');
      setStatus('error');
      return;
    }

    setStatus('loading');
    setError(null);
    setSuggestOpen(false);

    // Map zoom in parallel
    if (lat != null && lon != null) {
      flyTo(lat, lon);
    } else {
      photonSuggest(a).then(s => { if (s[0]) flyTo(s[0].lat, s[0].lon); });
    }

    try {
      const data = await fetchRentEstimate({
        address: a,
        bedrooms: selectedModel.bedrooms,
        bathrooms: selectedModel.bathrooms,
        square_footage: selectedModel.squareFootage,
        property_type: 'Single Family',
        apply_adu_premium: false,
        model_label: selectedModel.label,
      });
      setResult(data);
      setStatus('ready');
    } catch (err: any) {
      console.error('Rent estimate failed', err);
      setError(err?.response?.data?.detail || err?.message || 'Unable to fetch rent estimate. Try again.');
      setStatus('error');
    }
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (activeSuggest >= 0 && suggestions[activeSuggest]) {
      const s = suggestions[activeSuggest];
      setAddress(s.label);
      runEstimate(s.lat, s.lon, s.label);
    } else {
      runEstimate();
    }
  };

  const pickSuggestion = (s: Suggestion) => {
    setAddress(s.label);
    runEstimate(s.lat, s.lon, s.label);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!suggestOpen || suggestions.length === 0) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActiveSuggest(Math.min(activeSuggest + 1, suggestions.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActiveSuggest(Math.max(activeSuggest - 1, -1)); }
    else if (e.key === 'Escape') { setSuggestOpen(false); }
  };

  const toggleMonth = (idx: number) => {
    setSelectedMonths(months =>
      months.includes(idx) ? months.filter(m => m !== idx) : [...months, idx].sort((a, b) => a - b)
    );
  };

  const resetToIntake = () => {
    setResult(null);
    setStatus('idle');
    setError(null);
  };

  const hasResult = result !== null;

  return (
    <div className="pl-shell">
      <style>{PAGE_CSS}</style>

      <div ref={mapContainerRef} className="pl-map" />

      <div className="pl-wordmark">
        <span className="pl-wordmark-dot" />
        PLINTH
        <span className="pl-wordmark-sub">/ ADU REVENUE</span>
      </div>

      {!hasResult && (
        <IntakeForm
          address={address}
          setAddress={setAddress}
          modelId={modelId}
          setModelId={setModelId}
          yearRound={yearRound}
          setYearRound={setYearRound}
          selectedMonths={selectedMonths}
          toggleMonth={toggleMonth}
          funding={funding}
          setFunding={setFunding}
          suggestions={suggestions}
          suggestOpen={suggestOpen}
          setSuggestOpen={setSuggestOpen}
          activeSuggest={activeSuggest}
          setActiveSuggest={setActiveSuggest}
          onSubmit={onSubmit}
          pickSuggestion={pickSuggestion}
          onKeyDown={onKeyDown}
          loading={status === 'loading'}
          error={status === 'error' ? error : null}
        />
      )}

      {hasResult && result && (
        <ResultSidebar
          result={result}
          model={selectedModel}
          yearRound={yearRound}
          selectedMonths={selectedMonths}
          funding={funding}
          onShowProForma={() => setShowProForma(true)}
          onReset={resetToIntake}
        />
      )}

      {showProForma && result && (
        <ProFormaPanel
          address={result.address}
          estimate={result.estimate}
          model={selectedModel}
          yearRound={yearRound}
          selectedMonths={selectedMonths}
          funding={funding}
          onClose={() => setShowProForma(false)}
        />
      )}
    </div>
  );
};

// ──────────────────────────────────────────────────────────────────────
// Intake form
// ──────────────────────────────────────────────────────────────────────

interface IntakeFormProps {
  address: string;
  setAddress: (s: string) => void;
  modelId: PlinthModel['id'];
  setModelId: (id: PlinthModel['id']) => void;
  yearRound: boolean;
  setYearRound: (v: boolean) => void;
  selectedMonths: number[];
  toggleMonth: (idx: number) => void;
  funding: 'cash' | 'finance';
  setFunding: (f: 'cash' | 'finance') => void;
  suggestions: Suggestion[];
  suggestOpen: boolean;
  setSuggestOpen: (o: boolean) => void;
  activeSuggest: number;
  setActiveSuggest: (i: number) => void;
  onSubmit: (e: React.FormEvent) => void;
  pickSuggestion: (s: Suggestion) => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  loading: boolean;
  error: string | null;
}

const IntakeForm: React.FC<IntakeFormProps> = (p) => (
  <div className="pl-hero">
    <form onSubmit={p.onSubmit} className="pl-intake">
      <div className="pl-eyebrow">ADU revenue estimate</div>
      <h1 className="pl-intake-title">
        See what an ADU could earn
        <br />
        on your property.
      </h1>

      {/* 1. Address */}
      <div className="pl-field">
        <label className="pl-field-label">Property address</label>
        <div className="pl-search-wrap">
          <input
            type="text"
            value={p.address}
            onChange={e => { p.setAddress(e.target.value); p.setSuggestOpen(true); }}
            onFocus={() => p.setSuggestOpen(true)}
            onBlur={() => { window.setTimeout(() => p.setSuggestOpen(false), 150); }}
            onKeyDown={p.onKeyDown}
            placeholder="Start typing an address…"
            className="pl-input"
            autoFocus
            disabled={p.loading}
            autoComplete="off"
            spellCheck={false}
          />
          {p.suggestOpen && p.suggestions.length > 0 && (
            <ul className="pl-suggest">
              {p.suggestions.map((s, i) => (
                <li
                  key={i}
                  className={`pl-suggest-item ${i === p.activeSuggest ? 'pl-suggest-active' : ''}`}
                  onMouseDown={e => e.preventDefault()}
                  onMouseEnter={() => p.setActiveSuggest(i)}
                  onClick={() => p.pickSuggestion(s)}
                >
                  {s.label}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* 2. Model */}
      <div className="pl-field">
        <label className="pl-field-label">Plinth model</label>
        <div className="pl-model-grid">
          {PLINTH_MODELS.map(m => (
            <button
              key={m.id}
              type="button"
              className={`pl-model-card ${p.modelId === m.id ? 'pl-model-active' : ''}`}
              onClick={() => p.setModelId(m.id)}
            >
              <div className="pl-model-id">{m.label}</div>
              <div className="pl-model-spec">{m.squareFootage} SF · {m.dimensions}</div>
              <div className="pl-model-spec">{m.bedrooms} BR · {m.bathrooms} BA</div>
              <div className="pl-model-price mono">{fmtUsd(m.price)}</div>
            </button>
          ))}
        </div>
      </div>

      {/* 3. Year-round */}
      <div className="pl-field">
        <label className="pl-field-label">Rental season</label>
        <div className="pl-radio-row">
          <RadioPill
            label="Year-round"
            checked={p.yearRound}
            onClick={() => p.setYearRound(true)}
          />
          <RadioPill
            label="Specific months only"
            checked={!p.yearRound}
            onClick={() => p.setYearRound(false)}
          />
        </div>
        {!p.yearRound && (
          <div className="pl-months">
            {MONTH_LABELS.map((m, i) => (
              <button
                key={m}
                type="button"
                className={`pl-month ${p.selectedMonths.includes(i) ? 'pl-month-on' : ''}`}
                onClick={() => p.toggleMonth(i)}
              >
                {m}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 4. Funding */}
      <div className="pl-field">
        <label className="pl-field-label">How will you fund it?</label>
        <div className="pl-radio-row">
          <RadioPill label="Pay cash" checked={p.funding === 'cash'} onClick={() => p.setFunding('cash')} />
          <RadioPill
            label={`Finance (${Math.round(ASSUMPTIONS.financeDownPct * 100)}% down, ${ASSUMPTIONS.financeTermYears}yr)`}
            checked={p.funding === 'finance'}
            onClick={() => p.setFunding('finance')}
          />
        </div>
      </div>

      <button type="submit" className="pl-submit" disabled={p.loading}>
        {p.loading ? 'Estimating…' : 'Generate estimate →'}
      </button>

      {p.error && <div className="pl-error">{p.error}</div>}
    </form>
  </div>
);

const RadioPill: React.FC<{ label: string; checked: boolean; onClick: () => void }> = ({ label, checked, onClick }) => (
  <button type="button" onClick={onClick} className={`pl-radio ${checked ? 'pl-radio-on' : ''}`}>
    <span className="pl-radio-dot" />
    {label}
  </button>
);

// ──────────────────────────────────────────────────────────────────────
// Result sidebar
// ──────────────────────────────────────────────────────────────────────

const ResultSidebar: React.FC<{
  result: RentEstimateResponse;
  model: PlinthModel;
  yearRound: boolean;
  selectedMonths: number[];
  funding: 'cash' | 'finance';
  onShowProForma: () => void;
  onReset: () => void;
}> = ({ result, model, yearRound, selectedMonths, funding, onShowProForma, onReset }) => {
  const est = result.estimate;
  const monthly = est.rent ?? 0;
  const monthlyLow = est.rent_low ?? null;
  const monthlyHigh = est.rent_high ?? null;

  const months = yearRound ? 12 : selectedMonths.length;

  const annualMid = yearRound ? monthly * 12 : seasonalAnnualRevenue(monthly, selectedMonths);
  const annualLow = monthlyLow != null ? (yearRound ? monthlyLow * 12 : seasonalAnnualRevenue(monthlyLow, selectedMonths)) : null;
  const annualHigh = monthlyHigh != null ? (yearRound ? monthlyHigh * 12 : seasonalAnnualRevenue(monthlyHigh, selectedMonths)) : null;

  const { net } = netAnnualIncome(annualMid, model.price, funding);

  const seasonLabel = yearRound
    ? 'Year-round'
    : `${months} month${months !== 1 ? 's' : ''}: ${selectedMonths.map(i => MONTH_LABELS[i]).join(', ')}`;
  const fundingLabel = funding === 'cash' ? 'Pay cash' : `Financed · ${Math.round(ASSUMPTIONS.financeDownPct * 100)}% down`;

  return (
    <aside className="pl-panel">
      <button className="pl-panel-back" onClick={onReset}>← New estimate</button>

      <section className="pl-panel-section">
        <div className="pl-eyebrow">Subject</div>
        <div className="pl-panel-address">{result.address}</div>
        <div className="pl-panel-spec">
          {model.label} · {model.squareFootage} SF · {model.bedrooms} BR / {model.bathrooms} BA
        </div>
        <div className="pl-panel-meta">
          {seasonLabel}
          <br />
          {fundingLabel}
        </div>
      </section>

      <section className="pl-panel-section pl-panel-hero">
        <div className="pl-eyebrow">Projected annual income</div>
        <div className="pl-hero-number mono">
          {annualLow != null && annualHigh != null ? (
            <>
              {fmtUsd(annualLow)}<span className="pl-hero-dash"> – </span>{fmtUsd(annualHigh)}
            </>
          ) : (
            fmtUsd(annualMid)
          )}
        </div>
        <div className="pl-hero-caption mono">
          {monthlyLow != null && monthlyHigh != null
            ? `${fmtUsd(monthlyLow)} – ${fmtUsd(monthlyHigh)} / mo`
            : `${fmtUsd(monthly)} / mo`}
        </div>
        {result.source_note && (
          <div className="pl-source-note">{result.source_note}</div>
        )}
      </section>

      <section className="pl-panel-section">
        <div className="pl-eyebrow">What you keep each year</div>
        <div className="pl-net-number mono">{fmtUsd(net)}</div>
        <div className="pl-net-caption">After taxes, insurance, upkeep{funding === 'finance' ? ', and loan payments' : ''}.</div>
      </section>

      <section className="pl-panel-section">
        <div className="pl-eyebrow">Comparable listings</div>
        <CompsList comps={est.comparables} />
      </section>

      <section className="pl-panel-section">
        <button onClick={onShowProForma} className="pl-secondary-cta">
          Revenue Calculator →
        </button>
        <div className="pl-panel-fineprint">
          10-year cash flow, returns, sensitivity, JV waterfall. Editable assumptions.
        </div>
      </section>

      <section className="pl-panel-section pl-disclaimer">
        Estimates are based on long-term residential rental comps for the
        property type and unit size. Actual rent varies by finish, parking,
        and local conditions. Not investment advice.
      </section>
    </aside>
  );
};

const CompsList: React.FC<{ comps: RentComparable[] }> = ({ comps }) => {
  if (!comps || comps.length === 0) {
    return <div className="pl-comps-empty">No live comparables for this area.</div>;
  }
  return (
    <ul className="pl-comps">
      {comps.slice(0, 5).map((c, i) => (
        <li key={i} className="pl-comp">
          <div className="pl-comp-addr">{c.address}</div>
          <div className="pl-comp-meta mono">
            {c.bedrooms != null ? `${c.bedrooms} BR` : '—'}
            {c.square_footage ? ` · ${c.square_footage} SF` : ''}
            {c.distance_mi != null ? ` · ${c.distance_mi.toFixed(2)} mi` : ''}
          </div>
          <div className="pl-comp-rent mono">
            {c.rent_monthly != null ? `${fmtUsd(c.rent_monthly)}/mo` : '—'}
          </div>
        </li>
      ))}
    </ul>
  );
};

// ──────────────────────────────────────────────────────────────────────
// Styles
// ──────────────────────────────────────────────────────────────────────

const PAGE_CSS = `
.pl-shell { position: relative; width: 100%; height: 100vh; overflow: hidden; }
.pl-map { position: absolute; inset: 0; background: var(--paper); }

.pl-shell .maplibregl-ctrl-attrib {
  background: rgba(250,250,247,0.85);
  font-family: 'Inter', sans-serif; font-size: 10px;
}
.pl-shell .maplibregl-ctrl-attrib a { color: var(--ink-soft); }
.pl-shell .maplibregl-ctrl-group {
  border: 1px solid var(--rule); background: var(--paper);
  box-shadow: none; border-radius: 0;
}
.pl-shell .maplibregl-ctrl-group button { border-radius: 0; }

.pl-marker {
  width: 14px; height: 14px; border-radius: 50%;
  background: var(--accent); border: 2px solid var(--paper);
  box-shadow: 0 0 0 1px var(--accent), 0 2px 8px rgba(199,93,58,0.35);
  transform: translate(-50%, -50%);
}

.pl-wordmark {
  position: absolute; top: 24px; left: 28px; z-index: 10;
  display: inline-flex; align-items: center; gap: 10px;
  padding: 10px 16px; background: var(--paper);
  border: 1px solid var(--rule);
  font-size: 12px; font-weight: 700; letter-spacing: 0.16em;
  color: var(--ink); text-transform: uppercase;
}
.pl-wordmark-dot { width: 8px; height: 8px; background: var(--accent); display: inline-block; }
.pl-wordmark-sub {
  font-weight: 400; color: var(--ink-faint);
  letter-spacing: 0.14em; margin-left: 4px;
}

/* ── Intake form (idle) ── */
.pl-hero {
  position: absolute; inset: 0; z-index: 5;
  display: flex; align-items: center; justify-content: center;
  padding: 80px 24px 24px; pointer-events: none;
  overflow-y: auto;
}
.pl-intake {
  pointer-events: auto;
  width: 100%; max-width: 560px;
  background: var(--paper); border: 1px solid var(--rule);
  padding: 36px 44px 32px;
  display: flex; flex-direction: column; gap: 22px;
}
.pl-eyebrow {
  font-size: 10px; letter-spacing: 0.18em;
  text-transform: uppercase; color: var(--accent);
  font-weight: 600;
}
.pl-intake-title {
  font-family: 'Inter', sans-serif;
  font-size: 30px; line-height: 1.1; font-weight: 500;
  letter-spacing: -0.02em; color: var(--ink); margin: -8px 0 4px;
}

.pl-field { display: flex; flex-direction: column; gap: 10px; }
.pl-field-label {
  font-size: 10px; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--ink-faint);
  font-weight: 600;
}

.pl-input {
  width: 100%; padding: 12px 14px;
  font-size: 14px; font-family: inherit;
  background: var(--paper); color: var(--ink);
  border: 1px solid var(--rule); outline: none;
  transition: border-color 0.15s;
}
.pl-input:focus { border-color: var(--ink); }
.pl-input::placeholder { color: var(--ink-faint); }

.pl-search-wrap { position: relative; }
.pl-suggest {
  position: absolute; top: 100%; left: 0; right: 0;
  background: var(--paper); border: 1px solid var(--ink); border-top: none;
  list-style: none; max-height: 240px; overflow-y: auto; z-index: 30;
}
.pl-suggest-item {
  padding: 10px 14px; font-size: 13px; line-height: 1.4;
  color: var(--ink); cursor: pointer;
  border-bottom: 1px solid var(--rule);
}
.pl-suggest-item:last-child { border-bottom: none; }
.pl-suggest-active, .pl-suggest-item:hover { background: var(--paper-soft); color: var(--accent); }

/* Model picker */
.pl-model-grid {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;
}
.pl-model-card {
  padding: 14px 12px;
  border: 1px solid var(--rule); background: var(--paper);
  text-align: left; font-family: inherit; cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
  display: flex; flex-direction: column; gap: 4px;
}
.pl-model-card:hover { border-color: var(--ink-soft); }
.pl-model-active { border: 1.5px solid var(--ink); background: var(--paper-soft); }
.pl-model-id { font-size: 11px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: var(--ink); }
.pl-model-spec { font-size: 11px; color: var(--ink-soft); }
.pl-model-price { font-size: 13px; font-weight: 500; color: var(--ink); margin-top: 4px; }

/* Radio pills */
.pl-radio-row { display: flex; gap: 8px; flex-wrap: wrap; }
.pl-radio {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 9px 14px; font-size: 12px;
  font-family: inherit; cursor: pointer;
  background: var(--paper); border: 1px solid var(--rule);
  color: var(--ink-soft); transition: border-color 0.15s, color 0.15s;
}
.pl-radio:hover { border-color: var(--ink-soft); }
.pl-radio-on { border-color: var(--ink); color: var(--ink); }
.pl-radio-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--paper); border: 1px solid var(--ink-faint);
  transition: background 0.15s, border-color 0.15s;
}
.pl-radio-on .pl-radio-dot { background: var(--accent); border-color: var(--accent); }

/* Month chips */
.pl-months {
  display: grid; grid-template-columns: repeat(12, 1fr); gap: 4px;
}
.pl-month {
  padding: 8px 0; font-size: 10px;
  font-family: inherit; font-weight: 600; letter-spacing: 0.08em;
  background: var(--paper); border: 1px solid var(--rule);
  color: var(--ink-soft); cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  text-transform: uppercase;
}
.pl-month:hover { border-color: var(--ink-soft); }
.pl-month-on { background: var(--ink); color: var(--paper); border-color: var(--ink); }

/* Submit */
.pl-submit {
  width: 100%; padding: 14px;
  background: var(--ink); color: var(--paper);
  border: 1px solid var(--ink);
  font-family: inherit; font-size: 12px; font-weight: 600;
  letter-spacing: 0.14em; text-transform: uppercase;
  cursor: pointer; transition: background 0.15s;
}
.pl-submit:hover:not(:disabled) { background: var(--accent); border-color: var(--accent); }
.pl-submit:disabled { background: var(--ink-faint); border-color: var(--ink-faint); cursor: wait; }

.pl-error {
  margin-top: 4px; padding: 10px 14px;
  border: 1px solid var(--accent); border-left-width: 3px;
  background: var(--accent-soft); color: #5a2410;
  font-size: 12px; line-height: 1.5;
}

/* ── Result sidebar ── */
.pl-panel {
  position: absolute; top: 0; right: 0; bottom: 0;
  width: 400px;
  background: var(--paper); border-left: 1px solid var(--rule);
  overflow-y: auto; z-index: 8;
  animation: pl-slide-in 0.35s ease-out;
}
@keyframes pl-slide-in {
  from { transform: translateX(100%); }
  to   { transform: translateX(0); }
}

.pl-panel-back {
  width: 100%; padding: 14px 28px;
  background: var(--paper); border: none; border-bottom: 1px solid var(--rule);
  text-align: left; font-family: inherit;
  font-size: 11px; font-weight: 600; letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--ink-soft);
  cursor: pointer; transition: color 0.15s;
}
.pl-panel-back:hover { color: var(--accent); }

.pl-panel-section {
  padding: 22px 28px;
  border-bottom: 1px solid var(--rule);
}
.pl-panel-section:last-child { border-bottom: none; }

.pl-panel-address {
  font-size: 17px; font-weight: 500; line-height: 1.3;
  color: var(--ink); margin-top: 8px;
}
.pl-panel-spec { font-size: 12px; color: var(--ink-soft); margin-top: 4px; }
.pl-panel-meta {
  font-size: 11px; color: var(--ink-faint); margin-top: 8px;
  line-height: 1.5;
}

.pl-panel-hero { background: var(--paper-soft); }
.pl-hero-number {
  font-size: 32px; font-weight: 500; color: var(--ink);
  line-height: 1.05; letter-spacing: -0.02em;
  margin-top: 10px;
}
.pl-hero-dash { color: var(--ink-faint); }
.pl-hero-caption { font-size: 12px; color: var(--ink-soft); margin-top: 8px; }
.pl-source-note { font-size: 11px; color: var(--ink-faint); margin-top: 10px; line-height: 1.4; }

.pl-net-number {
  font-size: 22px; font-weight: 500; color: var(--accent);
  margin-top: 8px; line-height: 1.1;
}
.pl-net-caption { font-size: 11px; color: var(--ink-faint); margin-top: 6px; line-height: 1.5; }

.pl-comps { list-style: none; margin-top: 10px; }
.pl-comp {
  padding: 11px 0;
  border-bottom: 1px solid var(--rule);
  display: grid; grid-template-columns: 1fr auto;
  grid-template-rows: auto auto; gap: 2px 12px;
}
.pl-comp:last-child { border-bottom: none; }
.pl-comp-addr {
  grid-column: 1; grid-row: 1;
  font-size: 12px; color: var(--ink); line-height: 1.35;
}
.pl-comp-meta {
  grid-column: 1; grid-row: 2;
  font-size: 11px; color: var(--ink-faint);
}
.pl-comp-rent {
  grid-column: 2; grid-row: 1 / span 2; align-self: center;
  font-size: 13px; color: var(--ink); font-weight: 500;
  white-space: nowrap;
}
.pl-comps-empty {
  font-size: 12px; color: var(--ink-faint);
  font-style: italic; margin-top: 10px;
}

.pl-secondary-cta {
  width: 100%; padding: 13px;
  background: transparent; border: 1px solid var(--ink);
  color: var(--ink); font-family: inherit;
  font-size: 11px; font-weight: 600; letter-spacing: 0.12em;
  text-transform: uppercase; cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.pl-secondary-cta:hover { background: var(--ink); color: var(--paper); }
.pl-panel-fineprint { font-size: 11px; color: var(--ink-faint); margin-top: 10px; line-height: 1.5; }

.pl-disclaimer {
  font-size: 10px; color: var(--ink-faint);
  line-height: 1.6; background: var(--paper-soft);
}

@media (max-width: 900px) {
  .pl-panel {
    width: 100%; border-left: none; border-top: 1px solid var(--rule);
    top: auto; height: 65vh;
  }
  .pl-intake { padding: 28px 24px; }
  .pl-model-grid { grid-template-columns: 1fr; }
  .pl-months { grid-template-columns: repeat(6, 1fr); }
}
`;
