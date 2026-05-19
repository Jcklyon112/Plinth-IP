import React from 'react';
import type { FilterState } from '../types/parcel';
import type { ViewMode } from './Map';

interface FilterBarProps {
  filters: FilterState;
  onChange: (f: FilterState) => void;
  onExport: () => void;
  parcelCount: number;
  viewMode: ViewMode;
  onViewModeChange: (m: ViewMode) => void;
}

const TIER_LABELS: Record<number, string> = {
  1: 'Tier 1 — Immediate',
  2: 'Tier 2 — Review',
  3: 'Tier 3 — Conditional',
  4: 'Tier 4 — Low Priority',
};

export const FilterBar: React.FC<FilterBarProps> = ({
  filters,
  onChange,
  onExport,
  parcelCount,
  viewMode,
  onViewModeChange,
}) => {
  return (
    <div style={styles.bar}>
      <div style={styles.logo}>PLINTH SIP</div>

      {/* View mode toggle */}
      <div style={styles.group}>
        <label style={styles.label}>View</label>
        <div style={styles.toggleRow}>
          {(['tier', 'zoning'] as ViewMode[]).map(mode => (
            <button
              key={mode}
              style={{
                ...styles.toggleBtn,
                ...(viewMode === mode ? styles.toggleBtnActive : {}),
              }}
              onClick={() => onViewModeChange(mode)}
            >
              {mode === 'tier' ? 'Feasibility' : 'Zoning'}
            </button>
          ))}
        </div>
      </div>

      {/* Tier filter — only useful in tier view */}
      <div style={styles.group}>
        <label style={styles.label}>Tier</label>
        <select
          style={styles.select}
          value={filters.tier ?? ''}
          onChange={e => onChange({ ...filters, tier: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">All tiers</option>
          {[1, 2, 3, 4].map(t => (
            <option key={t} value={t}>{TIER_LABELS[t]}</option>
          ))}
        </select>
      </div>

      <div style={styles.group}>
        <label style={styles.label}>Min Score</label>
        <input
          style={styles.input}
          type="number"
          min={0}
          max={100}
          placeholder="0–100"
          value={filters.minScore ?? ''}
          onChange={e => onChange({ ...filters, minScore: e.target.value ? Number(e.target.value) : null })}
        />
      </div>

      <div style={styles.group}>
        <label style={styles.label}>Zoning</label>
        <input
          style={styles.input}
          type="text"
          placeholder="e.g. R-2"
          value={filters.zoningCode ?? ''}
          onChange={e => onChange({ ...filters, zoningCode: e.target.value || null })}
        />
      </div>

      <div style={styles.count}>{parcelCount.toLocaleString()} parcels</div>

      <button style={styles.exportBtn} onClick={onExport}>
        Export CSV
      </button>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  bar: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    padding: '10px 20px',
    background: '#111',
    borderBottom: '1px solid #222',
    flexShrink: 0,
    flexWrap: 'wrap',
  },
  logo: {
    fontWeight: 700,
    fontSize: 14,
    letterSpacing: 2,
    color: '#5de0a0',
    marginRight: 8,
  },
  group: {
    display: 'flex',
    flexDirection: 'column',
    gap: 3,
  },
  label: {
    fontSize: 9,
    color: '#555',
    textTransform: 'uppercase',
    letterSpacing: 1,
  },
  toggleRow: {
    display: 'flex',
    gap: 2,
  },
  toggleBtn: {
    background: '#1e1e1e',
    border: '1px solid #333',
    color: '#777',
    padding: '3px 10px',
    borderRadius: 3,
    fontSize: 12,
    cursor: 'pointer',
    transition: 'all 0.15s',
  },
  toggleBtnActive: {
    background: '#5de0a0',
    color: '#000',
    borderColor: '#5de0a0',
    fontWeight: 600,
  },
  select: {
    background: '#1e1e1e',
    border: '1px solid #333',
    color: '#ddd',
    padding: '4px 8px',
    borderRadius: 4,
    fontSize: 12,
    cursor: 'pointer',
  },
  input: {
    background: '#1e1e1e',
    border: '1px solid #333',
    color: '#ddd',
    padding: '4px 8px',
    borderRadius: 4,
    fontSize: 12,
    width: 86,
  },
  count: {
    marginLeft: 'auto',
    color: '#555',
    fontSize: 12,
  },
  exportBtn: {
    background: '#5de0a0',
    color: '#000',
    border: 'none',
    padding: '6px 16px',
    borderRadius: 4,
    fontWeight: 700,
    fontSize: 12,
    cursor: 'pointer',
    letterSpacing: 0.5,
  },
};
