import React, { useState } from 'react';
import type { ParcelCollection, ParcelProperties, Tier } from '../types/parcel';
import { getUseDisplay } from '../utils/useCodeLabels';
import { sendParcelDxfToRhino } from '../utils/rhinoBridge';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export interface AnalysisState {
  status: string;
  progress: number;
  summary: string;
  explanations: Record<string, string>;
  configUpgradeNotes: Record<string, string>;
  error: string;
  parcelCount: number;
}

interface ShapeResultsPanelProps {
  results: ParcelProperties[];
  onParcelClick: (p: ParcelProperties) => void;
  onClose: () => void;
  analysis?: AnalysisState | null;
  /** Map of parcel features (one per row in `results`) used to look up
   * geometry for the inline "Send Linework to Rhino" button. Without this,
   * the button is hidden — geometry is required to build the DXF. */
  parcels?: ParcelCollection | null;
}

const TIER_COLORS: Record<number, string> = {
  1: '#5de0a0',
  2: '#f5c842',
  3: '#f0894a',
  4: '#e05d5d',
};

function tierColor(tier: Tier | null): string {
  return tier ? TIER_COLORS[tier] ?? '#444' : '#444';
}

const STATUS_LABELS: Record<string, string> = {
  queued: 'Starting analysis...',
  resolving: 'Identifying location...',
  fetching: 'Fetching parcels from GIS...',
  extracting: 'Extracting zoning rules from ordinance...',
  configuring: 'Loading zoning rules...',
  scoring: 'Scoring parcels...',
  explaining: 'Generating AI explanations...',
  complete: 'Analysis complete',
  error: 'Analysis failed',
};

function downloadCsv(results: ParcelProperties[]) {
  const headers = ['parcel_id', 'address', 'owner_name', 'zoning_code', 'lot_area_sqft', 'score', 'tier'];
  const rows = results.map(r =>
    headers.map(h => {
      const v = (r as any)[h];
      if (v == null) return '';
      return String(v).includes(',') ? `"${v}"` : String(v);
    }).join(',')
  );
  const csv = [headers.join(','), ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'shape_selection.csv';
  a.click();
  URL.revokeObjectURL(url);
}

async function downloadParcelPdf(parcel: ParcelProperties): Promise<void> {
  const res = await fetch(`${API_BASE}/reports/parcel-pdf`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(parcel),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || 'PDF generation failed');
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `plinth_report_${parcel.parcel_id || 'parcel'}.pdf`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export const ShapeResultsPanel: React.FC<ShapeResultsPanelProps> = ({
  results,
  onParcelClick,
  onClose,
  analysis,
  parcels,
}) => {
  const [expandedParcel, setExpandedParcel] = useState<string | null>(null);
  const [pdfLoading, setPdfLoading] = useState<string | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);
  // Per-parcel Rhino send status (keyed by parcel_id). Each row tracks its
  // own state so two adjacent expanded rows can both report independently.
  const [rhinoStatus, setRhinoStatus] = useState<
    Record<string, { kind: 'idle' | 'sending' | 'sent' | 'fallback' | 'error'; msg?: string }>
  >({});

  const findGeometry = (p: ParcelProperties): GeoJSON.Geometry | null => {
    if (!parcels) return null;
    const f = parcels.features.find(
      f => f.properties.parcel_id === p.parcel_id
        && f.properties.municipality_id === p.municipality_id,
    );
    return f?.geometry ?? null;
  };

  const tierCounts = { 1: 0, 2: 0, 3: 0, 4: 0 };
  results.forEach(r => {
    if (r.tier && r.tier >= 1 && r.tier <= 4) tierCounts[r.tier]++;
  });

  const isAnalyzing = analysis && analysis.status !== 'complete' && analysis.status !== 'error';
  const hasUpgrades = analysis && Object.keys(analysis.configUpgradeNotes || {}).some(
    k => (analysis.configUpgradeNotes[k] || '').includes('Upgraded')
  );

  return (
    <div style={styles.container}>
      <style>{`
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
      `}</style>
      {/* Header — show backend-reported parcel count as soon as it's known
          (right after the GIS fetch); fall back to scored results length once
          scoring completes. Either way the user sees a real number, never "0". */}
      <div style={styles.header}>
        <span>
          {(results.length || analysis?.parcelCount || 0)} parcels in selection
          {hasUpgrades && <span style={styles.upgradeBadge}> Config upgraded</span>}
        </span>
        <button onClick={onClose} style={styles.closeBtn}>&times;</button>
      </div>

      {/* Progress bar (during analysis — hide on complete and error) */}
      {analysis && analysis.status !== 'complete' && analysis.status !== 'error' && (
        <div style={styles.progressArea}>
          <div style={styles.progressBarBg}>
            <div
              style={{
                ...styles.progressBarFill,
                width: `${analysis.progress}%`,
              }}
            />
          </div>
          <div style={{
            ...styles.progressLabel,
            ...(analysis.status === 'fetching' ? { animation: 'pulse 1.5s ease-in-out infinite' } : {}),
          }}>
            {STATUS_LABELS[analysis.status] || analysis.status}
          </div>
        </div>
      )}

      {/* Error message */}
      {analysis && analysis.status === 'error' && (
        <div style={styles.errorBox}>
          {analysis.error || 'Analysis failed — unknown error'}
        </div>
      )}

      {/* Summary (when complete) */}
      {analysis && analysis.summary && analysis.status === 'complete' && (
        <div style={styles.summaryBox}>
          {analysis.summary}
        </div>
      )}

      {/* Tier summary */}
      <div style={styles.tierStrip}>
        {([1, 2, 3, 4] as const).map(t => (
          <span key={t} style={{ ...styles.tierPill, background: tierColor(t) }}>
            T{t}: {tierCounts[t]}
          </span>
        ))}
      </div>

      {/* Scrollable list */}
      <div style={styles.list}>
        {results.map((p, i) => (
          <div key={`${p.parcel_id}-${i}`}>
            <div
              style={styles.row}
              onClick={() => {
                onParcelClick(p);
                setExpandedParcel(expandedParcel === p.parcel_id ? null : p.parcel_id);
              }}
              onMouseEnter={e => (e.currentTarget.style.background = '#161616')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <div style={styles.rowTop}>
                <span style={styles.address}>
                  {p.address || p.parcel_id}
                </span>
                <span
                  style={{
                    ...styles.tierBadge,
                    background: tierColor(p.tier),
                    color: p.tier && p.tier <= 2 ? '#000' : '#fff',
                  }}
                >
                  {p.tier ?? '?'}
                </span>
              </div>
              <div style={styles.rowBottom}>
                <span style={styles.zoning} title={p.zoning_code ?? ''}>
                  {p.zoning_district_label || getUseDisplay(p.zoning_code)}
                </span>
                {p.lot_area_sqft && (
                  <span style={styles.acres}>
                    {(p.lot_area_sqft / 43560).toFixed(2)} ac
                  </span>
                )}
                <span style={styles.score}>
                  Score: {p.score?.toFixed(0) ?? '--'}
                </span>
              </div>
            </div>
            {/* Expanded deployment quick-view */}
            {expandedParcel === p.parcel_id && (
              <div style={styles.explanation}>
                {/* Site conditions strip — slope, soil, year built */}
                {(p.slope_stats || p.soil_septic_class || p.year_built) && (
                  <div style={styles.siteStrip}>
                    {p.slope_stats && p.slope_stats.count > 0 && (
                      <div style={styles.siteCell}>
                        <div style={styles.siteCellLabel}>Slope</div>
                        <div style={styles.siteCellValue}>
                          {p.slope_stats.mean.toFixed(0)}° avg
                          <span style={{ color: '#666', marginLeft: 4 }}>
                            / {p.slope_stats.max.toFixed(0)}° max
                          </span>
                        </div>
                      </div>
                    )}
                    {p.soil_septic_class && (
                      <div style={styles.siteCell}>
                        <div style={styles.siteCellLabel}>Septic soil</div>
                        <div style={{
                          ...styles.siteCellValue,
                          color: p.soil_septic_class === 'Very limited' ? '#e05d5d'
                            : p.soil_septic_class === 'Somewhat limited' ? '#f5c842'
                            : p.soil_septic_class === 'Not limited' ? '#5de0a0'
                            : '#888',
                        }}>
                          {p.soil_septic_class}
                        </div>
                      </div>
                    )}
                    {p.year_built && p.year_built > 0 && (
                      <div style={styles.siteCell}>
                        <div style={styles.siteCellLabel}>Built</div>
                        <div style={styles.siteCellValue}>{p.year_built}</div>
                      </div>
                    )}
                  </div>
                )}

                {/* Overlay hits — one per row, color-coded by level */}
                {p.overlay_hits && p.overlay_hits.length > 0 && (
                  <div style={{ marginBottom: 8 }}>
                    <div style={{ color: '#888', fontSize: 10, marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                      Overlays
                    </div>
                    {p.overlay_hits.map(h => {
                      const lvl = h.constraint_level === 'review_required' ? 'review' : h.constraint_level;
                      const color =
                        lvl === 'hard_block' ? '#e05d5d' :
                        lvl === 'review'     ? '#f5c842' :
                        '#888';
                      return (
                        <div key={h.layer_id} style={{ display: 'flex', gap: 6, marginTop: 2 }}>
                          <span style={{
                            color, fontWeight: 700, fontSize: 9, minWidth: 50,
                            textTransform: 'uppercase', letterSpacing: '0.04em',
                          }}>
                            {lvl.replace('_', ' ')}
                          </span>
                          <span style={{ color: '#ccc', fontSize: 10 }}>{h.label}</span>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Blockers */}
                {p.blockers && p.blockers.length > 0 && (
                  <div style={{ marginBottom: 6 }}>
                    <span style={{ color: '#e05d5d', fontWeight: 600 }}>⛔ Blockers: </span>
                    {p.blockers.map(b => (
                      <div key={b.rule_id} style={{ color: '#e05d5d', marginTop: 2 }}>
                        {b.rule_id}: {b.explanation}
                      </div>
                    ))}
                  </div>
                )}
                {/* Rule results summary */}
                {p.rule_results && p.rule_results.length > 0 && (
                  <div style={{ marginBottom: 6 }}>
                    {p.rule_results.map(r => {
                      const color = r.result === 'pass' ? '#5de0a0' : r.result === 'conditional' ? '#f5c842' : r.result === 'fail' ? '#e05d5d' : '#666';
                      return (
                        <div key={r.rule_id} style={{ display: 'flex', gap: 6, marginTop: 3, alignItems: 'flex-start' }}>
                          <span style={{ color, fontWeight: 700, flexShrink: 0, width: 14 }}>
                            {r.result === 'pass' ? '✓' : r.result === 'fail' ? '✗' : r.result === 'conditional' ? '~' : '?'}
                          </span>
                          <span style={{ color: '#999', fontSize: 10 }}>
                            <span style={{ color: '#ccc' }}>{r.rule_id.replace(/_/g, ' ')}</span>
                            {' — '}{r.explanation.slice(0, 120)}{r.explanation.length > 120 ? '…' : ''}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
                {/* AI explanation */}
                {analysis?.explanations?.[p.parcel_id] && (
                  <div style={{ color: '#888', fontStyle: 'italic', marginTop: 6, paddingTop: 6, borderTop: '1px solid #222' }}>
                    {analysis.explanations[p.parcel_id]}
                  </div>
                )}
                {/* Fallback if nothing to show */}
                {!p.blockers?.length && !p.rule_results?.length && !analysis?.explanations?.[p.parcel_id] && (
                  <span style={{ color: '#555' }}>No detail available.</span>
                )}
                {/* Send Linework to Rhino — uses geometry from the parent's
                    parcels FeatureCollection. Hidden if geometry is missing
                    (e.g., scoring finished but the geojson hasn't streamed
                    in yet) so the user isn't presented with a dead button. */}
                {(() => {
                  const geom = findGeometry(p);
                  if (!geom) return null;
                  const status = rhinoStatus[p.parcel_id] ?? { kind: 'idle' as const };
                  const sending = status.kind === 'sending';
                  return (
                    <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid #1e1e1e' }}>
                      <button
                        style={{
                          width: '100%',
                          background: '#0d1f24',
                          border: '1px solid #4ad6c4',
                          color: '#7aede0',
                          fontSize: 11,
                          fontWeight: 600,
                          padding: '7px 10px',
                          borderRadius: 4,
                          cursor: sending ? 'not-allowed' : 'pointer',
                          letterSpacing: '0.04em',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          gap: 6,
                          opacity: sending ? 0.6 : 1,
                        }}
                        disabled={sending}
                        onClick={async (e) => {
                          e.stopPropagation();
                          setRhinoStatus(s => ({ ...s, [p.parcel_id]: { kind: 'sending' } }));
                          try {
                            const res = await sendParcelDxfToRhino(geom, p);
                            if (res.status === 'sent') {
                              const env = res.envelope;
                              const zoneTag = env?.district_key
                                ? ` · zoning ${env.district_key}`
                                : '';
                              setRhinoStatus(s => ({
                                ...s,
                                [p.parcel_id]: { kind: 'sent', msg: `Sent ${res.fileName}${zoneTag}` },
                              }));
                            } else {
                              setRhinoStatus(s => ({
                                ...s,
                                [p.parcel_id]: {
                                  kind: 'fallback',
                                  msg: 'Browser does not support direct folder write — DXF downloaded.',
                                },
                              }));
                            }
                          } catch (err: any) {
                            const aborted = err && (err.name === 'AbortError' || /aborted|cancel/i.test(err.message || ''));
                            setRhinoStatus(s => ({
                              ...s,
                              [p.parcel_id]: aborted
                                ? { kind: 'idle' }
                                : { kind: 'error', msg: err.message || String(err) },
                            }));
                          }
                        }}
                      >
                        {sending ? '⏳ Sending…' : '⟶ Send Linework to Rhino'}
                      </button>
                      {status.kind === 'sent' && (
                        <div style={{ color: '#7aede0', fontSize: 10, marginTop: 4, textAlign: 'center' }}>
                          {status.msg}
                        </div>
                      )}
                      {status.kind === 'fallback' && (
                        <div style={{ color: '#f5c842', fontSize: 10, marginTop: 4, textAlign: 'center' }}>
                          {status.msg}
                        </div>
                      )}
                      {status.kind === 'error' && (
                        <div style={{ color: '#e05d5d', fontSize: 10, marginTop: 4, textAlign: 'center' }}>
                          Failed: {status.msg}
                        </div>
                      )}
                    </div>
                  );
                })()}
                {/* Download Report button */}
                <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid #1e1e1e' }}>
                  {pdfError && pdfLoading === null && (
                    <div style={{ color: '#e05d5d', fontSize: 10, marginBottom: 6 }}>{pdfError}</div>
                  )}
                  <button
                    style={{
                      width: '100%',
                      background: pdfLoading === p.parcel_id ? '#111' : '#0f2a1e',
                      border: '1px solid #5de0a0',
                      color: pdfLoading === p.parcel_id ? '#5de0a0' : '#5de0a0',
                      fontSize: 11,
                      fontWeight: 600,
                      padding: '7px 10px',
                      borderRadius: 4,
                      cursor: pdfLoading === p.parcel_id ? 'not-allowed' : 'pointer',
                      letterSpacing: '0.04em',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: 6,
                    }}
                    disabled={pdfLoading === p.parcel_id}
                    onClick={async (e) => {
                      e.stopPropagation();
                      setPdfLoading(p.parcel_id);
                      setPdfError(null);
                      try {
                        await downloadParcelPdf(p);
                      } catch (err: any) {
                        setPdfError(err.message || 'PDF download failed');
                      } finally {
                        setPdfLoading(null);
                      }
                    }}
                  >
                    {pdfLoading === p.parcel_id ? (
                      <>⏳ Generating report...</>
                    ) : (
                      <>↓ Download Feasibility Report</>
                    )}
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Footer actions */}
      <div style={styles.footer}>
        <button onClick={() => downloadCsv(results)} style={styles.exportBtn}>
          Export CSV
        </button>
        <div style={{ fontSize: 10, color: '#444', textAlign: 'center', marginTop: 6 }}>
          Click a parcel to expand · tap ↓ for full PDF report
        </div>
      </div>
    </div>
  );
};

const styles: Record<string, React.CSSProperties> = {
  container: {
    position: 'absolute',
    right: 0,
    top: 0,
    width: 340,
    height: '100%',
    background: '#0f0f0f',
    borderLeft: '1px solid #2a2a2a',
    zIndex: 1000,
    display: 'flex',
    flexDirection: 'column',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '14px 16px',
    borderBottom: '1px solid #2a2a2a',
    fontSize: 13,
    fontWeight: 600,
    color: '#e0e0e0',
    flexShrink: 0,
  },
  upgradeBadge: {
    fontSize: 10,
    color: '#5de0a0',
    fontWeight: 400,
    marginLeft: 6,
  },
  closeBtn: {
    background: 'none',
    border: 'none',
    color: '#666',
    fontSize: 20,
    cursor: 'pointer',
    padding: '0 4px',
    lineHeight: 1,
  },
  progressArea: {
    padding: '8px 16px',
    background: '#111',
    borderBottom: '1px solid #1e1e1e',
    flexShrink: 0,
  },
  progressBarBg: {
    width: '100%',
    height: 4,
    background: '#1a1a1a',
    borderRadius: 2,
    overflow: 'hidden',
  },
  progressBarFill: {
    height: '100%',
    background: '#5de0a0',
    transition: 'width 0.5s ease',
    borderRadius: 2,
  },
  progressLabel: {
    fontSize: 11,
    color: '#888',
    fontFamily: 'monospace',
    marginTop: 4,
  },
  errorBox: {
    background: '#1e0a0a',
    borderLeft: '3px solid #e05d5d',
    padding: 12,
    margin: '8px 12px',
    fontSize: 12,
    color: '#e05d5d',
    lineHeight: 1.5,
    flexShrink: 0,
    wordBreak: 'break-word' as const,
  },
  summaryBox: {
    background: '#141414',
    borderLeft: '3px solid #5de0a0',
    padding: 12,
    margin: '8px 12px',
    fontSize: 13,
    color: '#ccc',
    fontStyle: 'italic' as const,
    lineHeight: 1.5,
    flexShrink: 0,
  },
  tierStrip: {
    display: 'flex',
    gap: 6,
    padding: '8px 16px',
    background: '#141414',
    borderBottom: '1px solid #1e1e1e',
    flexShrink: 0,
  },
  tierPill: {
    fontSize: 11,
    fontWeight: 700,
    padding: '2px 8px',
    borderRadius: 10,
    color: '#000',
  },
  list: {
    flex: 1,
    overflowY: 'auto' as const,
  },
  row: {
    padding: '10px 16px',
    borderBottom: '1px solid #1a1a1a',
    cursor: 'pointer',
  },
  rowTop: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  address: {
    fontSize: 12,
    color: '#d0d0d0',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
    flex: 1,
    marginRight: 8,
  },
  tierBadge: {
    width: 20,
    height: 20,
    borderRadius: '50%',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 10,
    fontWeight: 700,
    flexShrink: 0,
  },
  rowBottom: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 11,
  },
  zoning: {
    color: '#888',
  },
  acres: {
    color: '#666',
  },
  score: {
    color: '#5de0a0',
    marginLeft: 'auto',
  },
  explanation: {
    padding: '8px 16px 12px',
    fontSize: 11,
    color: '#888',
    lineHeight: 1.5,
    borderBottom: '1px solid #1a1a1a',
    background: '#0d0d0d',
  },
  siteStrip: {
    display: 'flex',
    gap: 12,
    padding: '6px 0 10px',
    marginBottom: 8,
    borderBottom: '1px solid #1a1a1a',
  },
  siteCell: {
    display: 'flex',
    flexDirection: 'column' as const,
    minWidth: 60,
  },
  siteCellLabel: {
    fontSize: 9,
    color: '#666',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
    marginBottom: 1,
  },
  siteCellValue: {
    fontSize: 11,
    color: '#ccc',
    fontWeight: 600,
  },
  footer: {
    padding: 10,
    borderTop: '1px solid #2a2a2a',
    flexShrink: 0,
  },
  exportBtn: {
    width: '100%',
    background: '#1a1a1a',
    border: '1px solid #333',
    color: '#aaa',
    fontSize: 12,
    padding: 10,
    borderRadius: 4,
    cursor: 'pointer',
  },
};
