import React, { useEffect, useState } from 'react';
import { analyzeDataCenter } from '../api/datacenter';
import type {
  DcAnalysisResult,
  DcGrade,
} from '../types/datacenter';

interface Props {
  parcelId: string;
  municipalityId: string;
  /** Notified after each successful analyze. App listens to drive
   *  AnalysisLines on the map. */
  onResult?: (result: DcAnalysisResult) => void;
}

const GRADE_COLORS: Record<DcGrade, string> = {
  A: '#22c55e',
  B: '#84cc16',
  C: '#eab308',
  D: '#f97316',
  F: '#ef4444',
};

export const DataCenterPanel: React.FC<Props> = ({ parcelId, municipalityId, onResult }) => {
  const [result, setResult] = useState<DcAnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setResult(null);
    analyzeDataCenter(municipalityId, parcelId)
      .then(r => {
        if (!cancelled) {
          setResult(r);
          onResult?.(r);
        }
      })
      .catch(e => {
        if (!cancelled) {
          const msg = e?.response?.data?.detail || e?.message || 'Analysis failed';
          setError(String(msg));
        }
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [parcelId, municipalityId, onResult]);

  if (loading) {
    return <div style={styles.loading}>Running data-center feasibility analysis…</div>;
  }
  if (error) {
    return <div style={styles.error}>Error: {error}</div>;
  }
  if (!result) {
    return null;
  }

  return (
    <div style={styles.root}>
      {/* Three caveats — surfaced verbatim per spec */}
      <CaveatBanner warnings={result.warnings} />

      <OverallScoreSection result={result} />

      <Section title="Grid" defaultOpen={true}>
        <GridSection result={result} />
      </Section>

      <Section title="Generation" defaultOpen={false}>
        <GenerationSection result={result} />
      </Section>

      <Section title="Power Cost" defaultOpen={false}>
        <PowerCostSection result={result} />
      </Section>

      <Section title="Infrastructure" defaultOpen={false}>
        <InfrastructureSection result={result} />
      </Section>

      <Section title="Land" defaultOpen={false}>
        <LandSection result={result} />
      </Section>

      <DataFreshnessFooter result={result} />
    </div>
  );
};


// --- caveats ----------------------------------------------------------

const CaveatBanner: React.FC<{ warnings: string[] }> = ({ warnings }) => {
  // The three standard caveats from analyzer.STANDARD_WARNINGS always
  // appear first. Any data-availability warnings come after.
  return (
    <div style={styles.caveatBanner}>
      <div style={styles.caveatTitle}>Caveats</div>
      <ul style={styles.caveatList}>
        {warnings.map((w, i) => (
          <li key={i} style={styles.caveatItem}>{w}</li>
        ))}
      </ul>
    </div>
  );
};


// --- collapsible section primitive -----------------------------------

const Section: React.FC<{
  title: string;
  defaultOpen: boolean;
  children: React.ReactNode;
}> = ({ title, defaultOpen, children }) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={styles.section}>
      <button style={styles.sectionHeader} onClick={() => setOpen(o => !o)}>
        <span style={styles.sectionChevron}>{open ? '▼' : '▶'}</span>
        <span>{title}</span>
      </button>
      {open && <div style={styles.sectionBody}>{children}</div>}
    </div>
  );
};


// --- overall score ---------------------------------------------------

const OverallScoreSection: React.FC<{ result: DcAnalysisResult }> = ({ result }) => {
  const color = GRADE_COLORS[result.overallScore] ?? '#888';
  return (
    <div style={styles.scoreBlock}>
      <div style={{ ...styles.scoreBadge, color, borderColor: color }}>
        {result.overallScore}
      </div>
      <div style={styles.scoreSide}>
        <div style={styles.scoreComposite}>
          {result.compositeScore.toFixed(1)} / 100 composite
        </div>
        <div style={styles.scoreRationale}>{result.scoreRationale}</div>
        {result.gatingIssues.length > 0 && (
          <div style={styles.gatingBox}>
            <div style={styles.gatingTitle}>Gating issues</div>
            <ul style={styles.gatingList}>
              {result.gatingIssues.map((g, i) => <li key={i}>{g}</li>)}
            </ul>
          </div>
        )}
        {result.tierFit.length > 0 && (
          <div style={styles.tierFit}>
            Fits: {result.tierFit.map(t => (
              <span key={t} style={styles.tierPill}>{t}</span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};


// --- grid -------------------------------------------------------------

const GridSection: React.FC<{ result: DcAnalysisResult }> = ({ result }) => {
  const g = result.grid;
  return (
    <>
      <RowGroup>
        <Row label="Nearest >=115kV substation" value={
          g.nearestTransmissionSubstation
            ? `${g.nearestTransmissionSubstation.name ?? '(unnamed)'} — ${g.nearestTransmissionSubstation.distanceMi} mi @ ${g.nearestTransmissionSubstation.maxVoltageKv ?? '?'} kV (${g.nearestTransmissionSubstation.operator ?? 'unknown operator'})`
            : 'None within dataset.'
        } />
        <Row label="Nearest substation (any voltage)" value={
          g.nearestSubstation
            ? `${g.nearestSubstation.name ?? '(unnamed)'} — ${g.nearestSubstation.distanceMi} mi @ ${g.nearestSubstation.maxVoltageKv ?? '?'} kV`
            : '—'
        } />
        <Row label="Nearest transmission line" value={
          g.nearestTransmissionLine
            ? `${g.nearestTransmissionLine.distanceMi} mi @ ${g.nearestTransmissionLine.voltageKv ?? '?'} kV (${g.nearestTransmissionLine.owner ?? 'unknown'})`
            : '—'
        } />
        <Row label=">=230kV line within 1 mi" value={g.has230kvLineWithin1Mi ? 'Yes' : 'No'} />
        <Row label="Distinct corridors within 5 mi" value={String(g.transmissionCorridorsWithin5Mi)} />
        <Row label="Dual-feed feasible" value={
          <span style={{ color: g.dualFeedFeasible ? '#22c55e' : '#888' }}>
            {g.dualFeedFeasible ? 'Yes — two substations on disjoint corridors' : 'No — single corridor or insufficient redundancy'}
          </span>
        } />
      </RowGroup>

      <div style={styles.subSectionTitle}>Substations within 5 mi</div>
      {g.substationsWithin5Mi.length === 0 ? (
        <div style={styles.empty}>No substations within 5 mi.</div>
      ) : (
        <table style={styles.table}>
          <thead>
            <tr>
              <th>Name</th><th>Operator</th><th style={{ textAlign: 'right' }}>kV</th><th style={{ textAlign: 'right' }}>Distance (mi)</th>
            </tr>
          </thead>
          <tbody>
            {g.substationsWithin5Mi.map(s => (
              <tr key={s.id}>
                <td>{s.name ?? '—'}</td>
                <td>{s.operator ?? '—'}</td>
                <td style={{ textAlign: 'right' }}>{s.maxVoltageKv ?? '—'}</td>
                <td style={{ textAlign: 'right' }}>{s.distanceMi}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div style={styles.subSectionTitle}>ISO / RTO context</div>
      <RowGroup>
        <Row label="ISO" value={g.iso.fullName ? `${g.iso.fullName} (${g.iso.name})` : g.iso.name} />
        <Row label="Typical queue timeline" value={g.iso.typicalQueueTimeline ?? '—'} />
        <Row label="Current posture" value={g.iso.currentPosture ?? '—'} />
        {g.iso.queueDashboardUrl && (
          <Row label="Queue dashboard" value={
            <a href={g.iso.queueDashboardUrl} target="_blank" rel="noreferrer" style={styles.link}>
              {g.iso.queueDashboardUrl}
            </a>
          } />
        )}
      </RowGroup>
    </>
  );
};


// --- generation -------------------------------------------------------

const GenerationSection: React.FC<{ result: DcAnalysisResult }> = ({ result }) => {
  const g = result.generation;
  // Sort fuels by capacity desc, drop zeros
  const fuelEntries = Object.entries(g.capacityWithin25MiByFuel)
    .filter(([, mw]) => mw > 0)
    .sort(([, a], [, b]) => b - a);
  return (
    <>
      <RowGroup>
        <Row label="Nearest baseload (nuclear or large gas)" value={
          g.nearestBaseload
            ? `${g.nearestBaseload.name ?? '(unnamed)'} — ${g.nearestBaseload.distanceMi} mi @ ${g.nearestBaseload.capacityMw} MW (${g.nearestBaseload.fuel})`
            : '—'
        } />
      </RowGroup>
      <div style={styles.subSectionTitle}>Capacity within 25 mi by fuel</div>
      {fuelEntries.length === 0 ? (
        <div style={styles.empty}>No generation within 25 mi.</div>
      ) : (
        <table style={styles.table}>
          <thead>
            <tr><th>Fuel</th><th style={{ textAlign: 'right' }}>MW</th></tr>
          </thead>
          <tbody>
            {fuelEntries.map(([fuel, mw]) => (
              <tr key={fuel}><td>{fuel}</td><td style={{ textAlign: 'right' }}>{mw.toLocaleString()}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
};


// --- power cost -------------------------------------------------------

const PowerCostSection: React.FC<{ result: DcAnalysisResult }> = ({ result }) => {
  const p = result.power;
  const tierColor = p.rateTier === 'Low' ? '#22c55e'
    : p.rateTier === 'Medium' ? '#84cc16'
    : p.rateTier === 'High' ? '#f97316'
    : p.rateTier === 'Very High' ? '#ef4444'
    : '#888';
  return (
    <RowGroup>
      <Row label="Serving utility" value={p.utility ?? '—'} />
      <Row label="Industrial rate (latest EIA 861)" value={
        p.industrialRateCentsPerKwh != null ? `${p.industrialRateCentsPerKwh.toFixed(2)} c/kWh` : 'unknown'
      } />
      <Row label="Rate tier" value={
        p.rateTier ? <span style={{ color: tierColor, fontWeight: 600 }}>{p.rateTier}</span> : 'unknown'
      } />
    </RowGroup>
  );
};


// --- infrastructure ---------------------------------------------------

const InfrastructureSection: React.FC<{ result: DcAnalysisResult }> = ({ result }) => {
  const i = result.infrastructure;
  return (
    <RowGroup>
      <Row label="Fiber distance" value={
        i.fiberDistanceMi != null ? `${i.fiberDistanceMi} mi` : 'unknown'
      } />
      <Row label="Gas pipeline distance" value={
        i.gasPipelineDistanceMi != null ? `${i.gasPipelineDistanceMi} mi` : 'unknown'
      } />
      <Row label="FEMA flood zone" value={i.floodZone ?? 'unknown'} />
      <Row label="Wetland coverage" value={
        i.wetlandCoveragePct != null ? `${i.wetlandCoveragePct}%` : 'unknown (USFWS NWI)'
      } />
      <Row label="Acreage" value={
        i.acreage != null ? `${i.acreage} ac` : '—'
      } />
      <Row label="Acreage tier" value={i.acreageTier ?? '—'} />
    </RowGroup>
  );
};


// --- land -------------------------------------------------------------

const LandSection: React.FC<{ result: DcAnalysisResult }> = ({ result }) => {
  return (
    <RowGroup>
      <Row label="Zoning category (heuristic)" value={result.zoning} />
      <Row label="Tier fit" value={
        result.tierFit.length > 0 ? result.tierFit.join(', ') : '—'
      } />
    </RowGroup>
  );
};


// --- footer -----------------------------------------------------------

const DataFreshnessFooter: React.FC<{ result: DcAnalysisResult }> = ({ result }) => {
  const d = new Date(result.computedAt);
  return (
    <div style={styles.footer}>
      Analyzed at {d.toLocaleString()}
    </div>
  );
};


// --- row primitives ---------------------------------------------------

const RowGroup: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={styles.rowGroup}>{children}</div>
);

const Row: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div style={styles.row}>
    <div style={styles.rowLabel}>{label}</div>
    <div style={styles.rowValue}>{value}</div>
  </div>
);


// --- styles -----------------------------------------------------------

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    padding: '12px 16px',
  },
  loading: {
    padding: 24,
    color: '#888',
    fontSize: 13,
    textAlign: 'center',
  },
  error: {
    padding: 16,
    background: '#1e0a0a',
    border: '1px solid #5a2020',
    color: '#e05c5c',
    borderRadius: 6,
    fontSize: 12,
  },
  caveatBanner: {
    background: '#1c1606',
    border: '1px solid #5a4520',
    borderRadius: 6,
    padding: '10px 12px',
    color: '#d8c47a',
    fontSize: 11,
    lineHeight: 1.4,
  },
  caveatTitle: {
    fontSize: 10,
    fontWeight: 700,
    textTransform: 'uppercase' as const,
    letterSpacing: 0.5,
    marginBottom: 6,
  },
  caveatList: { margin: 0, paddingLeft: 16 },
  caveatItem: { marginBottom: 3 },

  scoreBlock: {
    display: 'flex',
    gap: 16,
    alignItems: 'flex-start',
    background: '#0e0e0e',
    border: '1px solid #1e1e1e',
    borderRadius: 6,
    padding: 14,
  },
  scoreBadge: {
    fontSize: 56,
    fontWeight: 800,
    lineHeight: 1,
    width: 80,
    height: 80,
    border: '3px solid',
    borderRadius: 12,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  scoreSide: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  scoreComposite: { fontSize: 12, color: '#aaa' },
  scoreRationale: { fontSize: 12, color: '#ccc', lineHeight: 1.5 },
  tierFit: { fontSize: 11, color: '#888', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' as const },
  tierPill: {
    background: '#1a1a1a',
    border: '1px solid #333',
    color: '#5de0a0',
    borderRadius: 12,
    padding: '2px 8px',
    fontSize: 10,
    fontWeight: 600,
  },
  gatingBox: {
    background: '#1e0a0a',
    border: '1px solid #5a2020',
    borderRadius: 4,
    padding: '6px 10px',
  },
  gatingTitle: {
    fontSize: 10,
    fontWeight: 700,
    color: '#e05c5c',
    textTransform: 'uppercase' as const,
    letterSpacing: 0.5,
    marginBottom: 3,
  },
  gatingList: { margin: 0, paddingLeft: 16, fontSize: 11, color: '#e8a0a0' },

  section: { background: '#0e0e0e', border: '1px solid #1e1e1e', borderRadius: 6 },
  sectionHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    width: '100%',
    background: 'transparent',
    border: 'none',
    color: '#ccc',
    padding: '10px 14px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    textAlign: 'left' as const,
  },
  sectionChevron: { fontSize: 9, color: '#666', width: 10 },
  sectionBody: { padding: '0 14px 12px 14px' },
  subSectionTitle: {
    fontSize: 10,
    fontWeight: 700,
    color: '#666',
    textTransform: 'uppercase' as const,
    letterSpacing: 0.5,
    marginTop: 12,
    marginBottom: 6,
  },

  rowGroup: { display: 'flex', flexDirection: 'column', gap: 4 },
  row: { display: 'flex', alignItems: 'flex-start', gap: 12, fontSize: 12, padding: '4px 0', borderBottom: '1px solid #161616' },
  rowLabel: { color: '#666', flex: '0 0 200px' },
  rowValue: { color: '#ddd', flex: 1, wordBreak: 'break-word' as const },
  link: { color: '#5de0a0', textDecoration: 'none' },
  empty: { color: '#666', fontSize: 11, fontStyle: 'italic' as const, padding: '8px 0' },

  table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: 11, color: '#ccc' },
  footer: { fontSize: 10, color: '#444', textAlign: 'right', paddingTop: 8 },
};

// Table cell coloring for the dark theme
const styleSheet = document.createElement('style');
styleSheet.textContent = `
  .dc-panel-table th { color: #666; text-transform: uppercase; font-size: 10px; letter-spacing: 0.5px; padding: 4px 8px; border-bottom: 1px solid #2a2a2a; text-align: left; }
  .dc-panel-table td { padding: 4px 8px; border-bottom: 1px solid #161616; }
`;
if (typeof document !== 'undefined' && !document.head.querySelector('style[data-dc-panel]')) {
  styleSheet.setAttribute('data-dc-panel', 'true');
  document.head.appendChild(styleSheet);
}
