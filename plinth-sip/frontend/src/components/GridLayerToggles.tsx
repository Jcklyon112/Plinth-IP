import React from 'react';
import { GRID_LAYER_KEYS, GRID_LAYER_LABELS } from '../types/datacenter';
import type { GridLayerKey } from '../types/datacenter';

interface Props {
  enabled: Set<string>;
  onToggle: (key: string) => void;
  onClose?: () => void;
}

/**
 * Floating layer-toggle panel shown when DC mode is on. Visibility for
 * each layer is also URL-persisted (see useUrlState) so analyses are
 * shareable.
 */
export const GridLayerToggles: React.FC<Props> = ({ enabled, onToggle, onClose }) => {
  return (
    <div style={styles.root}>
      <div style={styles.header}>
        <span>DC Layers</span>
        {onClose && (
          <button style={styles.close} onClick={onClose}>×</button>
        )}
      </div>
      <div style={styles.list}>
        {GRID_LAYER_KEYS.map(key => (
          <label key={key} style={styles.item}>
            <input
              type="checkbox"
              checked={enabled.has(key)}
              onChange={() => onToggle(key)}
              style={styles.checkbox}
            />
            <span style={styles.swatch as React.CSSProperties}
                  data-layer={key}
                  // swatch color via inline style derived from layer key
            />
            <span style={styles.label}>{GRID_LAYER_LABELS[key as GridLayerKey]}</span>
          </label>
        ))}
      </div>
      <div style={styles.footnote}>
        Layers are bbox-filtered to the current map view.
      </div>
    </div>
  );
};


const SWATCH_COLORS: Record<GridLayerKey, string> = {
  subs: '#f59e0b',
  lines: '#a78bfa',
  plants: '#22c55e',
  iso: '#475569',
  utility: '#0ea5e9',
};

const styles: Record<string, React.CSSProperties> = {
  root: {
    position: 'absolute',
    top: 12,
    right: 12,
    zIndex: 800,
    background: 'rgba(15,15,15,0.95)',
    border: '1px solid #2a2a2a',
    borderRadius: 6,
    padding: '8px 12px',
    minWidth: 200,
    color: '#ccc',
    fontSize: 12,
    boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    fontWeight: 700,
    fontSize: 11,
    textTransform: 'uppercase' as const,
    letterSpacing: 0.5,
    color: '#888',
    paddingBottom: 6,
    borderBottom: '1px solid #2a2a2a',
    marginBottom: 6,
  },
  close: {
    background: 'transparent',
    border: 'none',
    color: '#666',
    fontSize: 18,
    cursor: 'pointer',
    padding: 0,
    lineHeight: 1,
    width: 18,
  },
  list: { display: 'flex', flexDirection: 'column', gap: 4 },
  item: { display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', padding: '2px 0' },
  checkbox: { margin: 0, accentColor: '#5de0a0' },
  swatch: { width: 12, height: 12, borderRadius: 2, flexShrink: 0, display: 'inline-block' },
  label: { fontSize: 11 },
  footnote: { fontSize: 10, color: '#555', marginTop: 6, paddingTop: 6, borderTop: '1px solid #2a2a2a', fontStyle: 'italic' as const },
};

// Apply swatch colors via a one-shot stylesheet (cheaper than per-render
// inline styles when each <span> has the same key->color mapping).
if (typeof document !== 'undefined' && !document.head.querySelector('style[data-dc-swatches]')) {
  const css = Object.entries(SWATCH_COLORS)
    .map(([k, c]) => `[data-layer="${k}"]{background:${c};border:1px solid ${c}}`)
    .join('\n');
  const el = document.createElement('style');
  el.setAttribute('data-dc-swatches', 'true');
  el.textContent = css;
  document.head.appendChild(el);
}
