import React, { useState, useEffect, useCallback } from 'react';
import type L from 'leaflet';
import { Map } from './components/Map';
import type { ViewMode } from './components/Map';
import { FilterBar } from './components/FilterBar';
import { ParcelDetailPanel } from './components/ParcelDetailPanel';
import { ScanSearchBar } from './components/ScanSearchBar';
import { fetchParcels, fetchMunicipalities, exportCsvUrl, startShapeAnalysis, getShapeAnalysis } from './api/client';
import type { ParcelCollection, ParcelProperties, FilterState } from './types/parcel';
import { ShapeResultsPanel } from './components/ShapeResultsPanel';
import type { AnalysisState } from './components/ShapeResultsPanel';
import { GridLayers } from './components/GridLayers';
import { GridLayerToggles } from './components/GridLayerToggles';
import { AnalysisLines } from './components/AnalysisLines';
import { useUrlState } from './hooks/useUrlState';
import type { DcAnalysisResult } from './types/datacenter';

const DEFAULT_MUNICIPALITY = 'ma_acton';

export default function App() {
  const [municipalityId, setMunicipalityId] = useState(DEFAULT_MUNICIPALITY);
  const [municipalities, setMunicipalities] = useState<{ municipality_id: string; name: string }[]>([]);
  const [parcels, setParcels] = useState<ParcelCollection | null>(null);
  const [selectedParcel, setSelectedParcel] = useState<ParcelProperties | null>(null);
  const [selectedGeometry, setSelectedGeometry] = useState<GeoJSON.Geometry | null>(null);
  const [filters, setFilters] = useState<FilterState>({ tier: null, minScore: null, zoningCode: null });
  const [viewMode, setViewMode] = useState<ViewMode>('tier');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [focusParcelId, setFocusParcelId] = useState<string | null>(null);
  const [shapeResults, setShapeResults] = useState<ParcelProperties[] | null>(null);
  const [shapeHighlightIds, setShapeHighlightIds] = useState<Set<string>>(new Set());
  const [analysisState, setAnalysisState] = useState<AnalysisState | null>(null);
  const [analysisId, setAnalysisId] = useState<string | null>(null);

  // Data-center mode + URL state. DC mode controls the grid-layer
  // overlays on the map; the panel-level tab is independent (see
  // ParcelDetailPanel.tsx).
  const { state: urlState, setDcMode, toggleLayer, setDcParcel } = useUrlState();
  const [mapInstance, setMapInstance] = useState<L.Map | null>(null);
  const [dcAnalysis, setDcAnalysis] = useState<DcAnalysisResult | null>(null);

  useEffect(() => {
    fetchMunicipalities().then(setMunicipalities).catch(() => {});
  }, []);

  // No auto-load: parcels come from shape analysis (stateless pipeline)

  const handleParcelClick = useCallback((parcel: ParcelProperties) => {
    setSelectedParcel(parcel);
    const feature = parcels?.features.find(
      f => f.properties.parcel_id === parcel.parcel_id
        && f.properties.municipality_id === parcel.municipality_id
    );
    setSelectedGeometry(feature?.geometry ?? null);
    // Persist current parcel to URL for shareable analyses (only writes
    // when DC mode is on; when off the writer drops the param).
    setDcParcel({ municipalityId: parcel.municipality_id, parcelId: parcel.parcel_id });
    // Clear any prior DC analysis so AnalysisLines unmounts until the
    // user re-runs (panel auto-runs when they switch to the DC tab).
    setDcAnalysis(null);
  }, [parcels, setDcParcel]);

  const handleExport = () => {
    window.open(exportCsvUrl(municipalityId, filters), '_blank');
  };

  const handleScanComplete = useCallback((newMuniId: string, newMuniName: string) => {
    // Refresh full list from DB (picks up anything scanned via CLI too)
    fetchMunicipalities().then(setMunicipalities).catch(() => {
      // Fallback: manually add if fetch fails
      setMunicipalities(prev => {
        if (prev.find(m => m.municipality_id === newMuniId)) return prev;
        return [...prev, { municipality_id: newMuniId, name: newMuniName }];
      });
    });
    // Switch to the scanned municipality
    setMunicipalityId(newMuniId);
    setSelectedParcel(null);
    setSelectedGeometry(null);
  }, []);

  const handleShapeDrawn = useCallback(async (shape: GeoJSON.Polygon) => {
    try {
      // Open panel immediately with empty results
      setShapeResults([]);
      setAnalysisState({ status: 'queued', progress: 0, summary: '', explanations: {}, configUpgradeNotes: {}, error: '', parcelCount: 0 });

      // Start stateless analysis (fetches from ArcGIS, scores in memory)
      const result = await startShapeAnalysis('', shape);
      setAnalysisId(result.analysis_id);
    } catch (e) {
      console.error('Shape analysis failed', e);
      setShapeResults(null);
      setAnalysisState(null);
    }
  }, []);

  const handleClearShape = useCallback(() => {
    setShapeResults(null);
    setShapeHighlightIds(new Set());
    setAnalysisState(null);
    setAnalysisId(null);
    setParcels(null);
  }, []);

  // Poll analysis status
  useEffect(() => {
    if (!analysisId) return;
    const interval = setInterval(async () => {
      try {
        const data = await getShapeAnalysis(analysisId);
        setAnalysisState({
          status: data.status,
          progress: data.progress,
          summary: data.summary,
          explanations: data.explanations,
          configUpgradeNotes: data.config_upgrade_notes,
          error: data.error || '',
          parcelCount: data.parcel_count || 0,
        });
        // Update parcel results when scored data arrives
        if (data.parcels && data.parcels.length > 0) {
          setShapeResults(data.parcels as ParcelProperties[]);
          setShapeHighlightIds(new Set(data.parcels.map((p: any) => p.parcel_id)));
        }
        // Render GeoJSON features on map when available
        if (data.geojson_features && data.geojson_features.length > 0) {
          setParcels({
            type: 'FeatureCollection',
            features: data.geojson_features,
            total: data.geojson_features.length,
          });
        }
        if (data.status === 'complete' || data.status === 'error') {
          clearInterval(interval);
        }
      } catch {
        clearInterval(interval);
      }
    }, 1500);
    return () => clearInterval(interval);
  }, [analysisId]);

  return (
    <div style={styles.app}>
      <FilterBar
        filters={filters}
        onChange={setFilters}
        onExport={handleExport}
        parcelCount={parcels?.features.length ?? 0}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
      />

      <ScanSearchBar
        onScanComplete={handleScanComplete}
        onParcelFound={(parcel, muniId, geom) => {
          // Switch to the municipality if different
          if (muniId !== municipalityId) {
            setMunicipalityId(muniId);
            fetchMunicipalities().then(setMunicipalities).catch(() => {});
          }
          setSelectedParcel(parcel);
          setSelectedGeometry(geom);
          setFocusParcelId(parcel.parcel_id);
        }}
      />

      <div style={styles.body}>
        <div style={styles.mapContainer}>
          {loading && <div style={styles.loadingOverlay}>Loading parcels…</div>}
          {error && <div style={styles.errorOverlay}>{error}</div>}
          <Map
            parcels={parcels}
            selectedParcelId={selectedParcel?.parcel_id ?? null}
            onParcelClick={handleParcelClick}
            viewMode={viewMode}
            focusParcelId={focusParcelId}
            onShapeDrawn={handleShapeDrawn}
            shapeHighlightIds={shapeHighlightIds}
            onClearShape={handleClearShape}
            hasActiveShape={shapeResults !== null}
            onMapReady={setMapInstance}
          />

          {/* DC Mode toggle — top-left corner, above the basemap toggle */}
          <button
            style={{
              ...dcModeStyles.modeBtn,
              ...(urlState.dcMode ? dcModeStyles.modeBtnActive : {}),
            }}
            onClick={() => setDcMode(!urlState.dcMode)}
            title={urlState.dcMode ? 'Hide grid / data-center layers' : 'Show grid / data-center layers'}
          >
            ⚡ DC Mode {urlState.dcMode ? 'ON' : 'OFF'}
          </button>

          {/* Map overlays — only render when DC mode is on. They mount
              imperatively onto the same Leaflet map exposed via
              onMapReady, so toggling DC mode adds/removes layers without
              re-creating the map. */}
          {urlState.dcMode && (
            <>
              <GridLayers
                map={mapInstance}
                enabled={urlState.dcLayers}
              />
              <GridLayerToggles
                enabled={urlState.dcLayers}
                onToggle={toggleLayer}
              />
            </>
          )}
          {/* Analysis lines (parcel -> nearest sub/line/fiber/gas) show
              regardless of DC mode, as long as a DC analysis result is
              cached. Drawing them stays useful in either basemap. */}
          <AnalysisLines map={mapInstance} analysis={dcAnalysis} />
        </div>

        {shapeResults && (
          <ShapeResultsPanel
            results={shapeResults}
            onParcelClick={handleParcelClick}
            onClose={handleClearShape}
            analysis={analysisState}
            parcels={parcels}
          />
        )}

        {selectedParcel && (
          <ParcelDetailPanel
            parcel={selectedParcel}
            geometry={selectedGeometry}
            onClose={() => {
              setSelectedParcel(null);
              setSelectedGeometry(null);
              setDcParcel(null);
              setDcAnalysis(null);
            }}
            initialTab={urlState.dcMode ? 'datacenter' : 'adu'}
            onDataCenterResult={setDcAnalysis}
          />
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  app: {
    height: '100vh',
    display: 'flex',
    flexDirection: 'column',
    background: '#0a0a0a',
  },
  body: {
    flex: 1,
    position: 'relative',
    overflow: 'hidden',
  },
  mapContainer: {
    width: '100%',
    height: '100%',
    position: 'relative',
  },
  loadingOverlay: {
    position: 'absolute',
    top: 16,
    left: '50%',
    transform: 'translateX(-50%)',
    background: '#1a1a1a',
    border: '1px solid #333',
    padding: '6px 16px',
    borderRadius: 20,
    fontSize: 12,
    color: '#888',
    zIndex: 999,
  },
  errorOverlay: {
    position: 'absolute',
    top: 16,
    left: '50%',
    transform: 'translateX(-50%)',
    background: '#1e0a0a',
    border: '1px solid #5a2020',
    padding: '8px 20px',
    borderRadius: 6,
    fontSize: 13,
    color: '#e05c5c',
    zIndex: 999,
  },
  municipalityBar: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '6px 20px',
    background: '#0e0e0e',
    borderBottom: '1px solid #1e1e1e',
    flexWrap: 'wrap' as const,
    minHeight: 36,
  },
  munLabel: {
    fontSize: 11,
    color: '#444',
    marginRight: 4,
    flexShrink: 0,
  },
  munEmpty: {
    fontSize: 11,
    color: '#333',
    fontStyle: 'italic',
  },
  munBtn: {
    background: '#1a1a1a',
    border: '1px solid #333',
    color: '#888',
    padding: '4px 12px',
    borderRadius: 4,
    fontSize: 12,
    cursor: 'pointer',
  },
  munBtnActive: {
    background: '#5de0a0',
    color: '#000',
    borderColor: '#5de0a0',
    fontWeight: 700,
  },
};

const dcModeStyles: Record<string, React.CSSProperties> = {
  modeBtn: {
    position: 'absolute',
    top: 12,
    left: 60,                              // sits just right of Leaflet's zoom control
    zIndex: 800,
    background: 'rgba(15,15,15,0.9)',
    border: '1px solid #333',
    color: '#888',
    padding: '6px 12px',
    borderRadius: 4,
    fontSize: 11,
    fontWeight: 700,
    cursor: 'pointer',
    letterSpacing: '0.05em',
    textTransform: 'uppercase' as const,
    boxShadow: '0 2px 6px rgba(0,0,0,0.5)',
  },
  modeBtnActive: {
    background: '#5de0a0',
    color: '#000',
    borderColor: '#5de0a0',
  },
};
