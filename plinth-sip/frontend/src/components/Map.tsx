import React, { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet-draw';
import 'leaflet-draw/dist/leaflet.draw.css';
import type { ParcelCollection, ParcelProperties, Tier } from '../types/parcel';

export type ViewMode = 'tier' | 'zoning';

/** Compute [lng, lat] centroid from a GeoJSON Polygon or MultiPolygon geometry. */
function computeCentroid(geometry: GeoJSON.Geometry): [number, number] {
  let totalLng = 0, totalLat = 0, count = 0;
  const processRing = (ring: number[][]) => {
    for (const coord of ring) {
      totalLng += coord[0];
      totalLat += coord[1];
      count++;
    }
  };
  if (geometry.type === 'Polygon') {
    processRing((geometry as GeoJSON.Polygon).coordinates[0]);
  } else if (geometry.type === 'MultiPolygon') {
    for (const polygon of (geometry as GeoJSON.MultiPolygon).coordinates) {
      processRing(polygon[0]);
    }
  }
  return count > 0 ? [totalLng / count, totalLat / count] : [0, 0];
}

interface MapProps {
  parcels: ParcelCollection | null;
  selectedParcelId: string | null;
  onParcelClick: (parcel: ParcelProperties) => void;
  viewMode: ViewMode;
  focusParcelId?: string | null;
  onShapeDrawn?: (shape: GeoJSON.Polygon) => void;
  shapeHighlightIds?: Set<string>;
  onClearShape?: () => void;
  hasActiveShape?: boolean;
  /**
   * Called once with the Leaflet map instance after init. Lets sibling
   * components (GridLayers, AnalysisLines) imperatively add their own
   * layers to the same map without us refactoring this component to
   * react-leaflet. Called again with `null` on unmount.
   */
  onMapReady?: (map: L.Map | null) => void;
}

// ── Tier colours ─────────────────────────────────────────────────────────────
// Tier 1 (≥85): Green  — Permissive zone, ADU allowed, plenty of land, Plinth fits
// Tier 2 (≥65): Yellow — Feasible but tighter constraints, still possible
// Tier 3 (≥40): Orange — Conditional / marginal, manual review needed
// Tier 4 (<40):  Red   — Hard blocked: ADU banned, lot too small, setbacks prevent fit
const TIER_COLORS: Record<Tier | 'none', string> = {
  1: '#22c55e',  // green  — Tier 1: Ready for outreach
  2: '#eab308',  // yellow — Tier 2: Possible, review needed
  3: '#f97316',  // orange — Tier 3: Conditional / marginal
  4: '#ef4444',  // red    — Tier 4: Blocked by regs or size
  none: '#444',  // grey   — Unscored
};

function getTierColor(tier: Tier | null): string {
  return tier ? (TIER_COLORS[tier] ?? TIER_COLORS['none']) : TIER_COLORS['none'];
}

// ── Zoning colours ────────────────────────────────────────────────────────────
// Deterministic palette — any unknown zone gets a consistent colour via hash.
const ZONING_PALETTE = [
  '#4e9af1', '#f1c94e', '#4ef17a', '#f14e9a',
  '#b44ef1', '#f1874e', '#4ef1e8', '#e8f14e',
  '#f14e4e', '#4eb4f1',
];

const zoningColorCache: Record<string, string> = {};

export function getZoningColor(code: string | null | undefined): string {
  if (!code) return '#444';
  if (zoningColorCache[code]) return zoningColorCache[code];
  let hash = 0;
  for (let i = 0; i < code.length; i++) {
    hash = ((hash << 5) - hash) + code.charCodeAt(i);
    hash |= 0;
  }
  const color = ZONING_PALETTE[Math.abs(hash) % ZONING_PALETTE.length];
  zoningColorCache[code] = color;
  return color;
}

function getParcelColor(props: ParcelProperties, viewMode: ViewMode): string {
  if (viewMode === 'zoning') return getZoningColor(props.zoning_code);
  return getTierColor(props.tier);
}

function parcelStyle(
  props: ParcelProperties,
  isSelected: boolean,
  viewMode: ViewMode,
): L.PathOptions {
  const color = getParcelColor(props, viewMode);
  return {
    fillColor: color,
    color: isSelected ? '#ffffff' : '#111',
    weight: isSelected ? 2.5 : 0.4,
    fillOpacity: isSelected ? 0.95 : 0.72,
  };
}

// ── Legend builder ────────────────────────────────────────────────────────────
function buildLegendHtml(viewMode: ViewMode, parcels: ParcelCollection | null): string {
  const wrap = (rows: string) => `
    <div style="background:#141414;padding:10px 14px;border-radius:6px;border:1px solid #2a2a2a;
                font-size:11px;color:#bbb;line-height:1.9;min-width:170px">
      ${rows}
    </div>`;

  const swatch = (color: string, label: string) =>
    `<div style="display:flex;align-items:center;gap:8px">
       <span style="display:inline-block;width:12px;height:12px;border-radius:2px;background:${color};flex-shrink:0"></span>
       ${label}
     </div>`;

  if (viewMode === 'tier') {
    return wrap(`
      <div style="font-weight:700;margin-bottom:4px;color:#666;letter-spacing:1px;font-size:9px">FEASIBILITY TIER</div>
      ${swatch(TIER_COLORS[1], '<b style="color:#eee">Tier 1</b> &nbsp;≥85 · Outreach ready')}
      ${swatch(TIER_COLORS[2], '<b style="color:#eee">Tier 2</b> &nbsp;65–84 · Possible')}
      ${swatch(TIER_COLORS[3], '<b style="color:#eee">Tier 3</b> &nbsp;40–64 · Conditional')}
      ${swatch(TIER_COLORS[4], '<b style="color:#eee">Tier 4</b> &nbsp;<40 · Blocked')}
      ${swatch(TIER_COLORS['none'], 'Unscored')}
    `);
  }

  // Zoning view — derive unique zone codes from loaded parcels
  const zones: Record<string, string> = {};
  if (parcels) {
    for (const f of parcels.features) {
      const code = (f.properties as ParcelProperties).zoning_code;
      if (code && !zones[code]) zones[code] = getZoningColor(code);
    }
  }
  const zoneRows = Object.entries(zones)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([code, color]) => swatch(color, code))
    .join('');

  return wrap(`
    <div style="font-weight:700;margin-bottom:4px;color:#666;letter-spacing:1px;font-size:9px">ZONING DISTRICT</div>
    ${zoneRows || '<div style="color:#555">No data</div>'}
  `);
}

// ── Component ─────────────────────────────────────────────────────────────────
export const Map: React.FC<MapProps> = ({
  parcels,
  selectedParcelId,
  onParcelClick,
  viewMode,
  focusParcelId,
  onShapeDrawn,
  shapeHighlightIds,
  onClearShape,
  hasActiveShape,
  onMapReady,
}) => {
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.GeoJSON | null>(null);
  const legendRef = useRef<L.Control | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const drawnItemsRef = useRef<L.FeatureGroup | null>(null);
  const drawControlRef = useRef<L.Control.Draw | null>(null);
  const drawHandlerRef = useRef<any>(null);
  const tileLayerRef = useRef<L.TileLayer | null>(null);
  const [basemap, setBasemap] = useState<'satellite' | 'dark'>('dark');

  // Store latest onShapeDrawn in a ref so the CREATED listener always sees it
  const onShapeDrawnRef = useRef(onShapeDrawn);
  useEffect(() => { onShapeDrawnRef.current = onShapeDrawn; }, [onShapeDrawn]);

  // Swap basemap tiles when toggle changes
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    // Remove all current tile layers
    map.eachLayer(layer => {
      if (layer instanceof L.TileLayer) map.removeLayer(layer);
    });

    if (basemap === 'satellite') {
      L.tileLayer(
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        { attribution: 'Tiles © Esri — Source: Esri, USGS, NOAA', maxZoom: 20 }
      ).addTo(map);
      // Place name / road labels on top
      L.tileLayer(
        'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
        { maxZoom: 20, opacity: 0.8 }
      ).addTo(map);
    } else {
      L.tileLayer(
        'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        {
          attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/">CARTO</a>',
          subdomains: 'abcd',
          maxZoom: 20,
        }
      ).addTo(map);
    }
  }, [basemap]);

  // Init map, draw layer, draw handler, and CREATED listener — all in one useEffect, dependency []
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      center: [42.48, -71.43],
      zoom: 13,
      zoomControl: true,
    });

    // Pick initial tiles from current `basemap` state so the first paint
    // matches the toggle (the basemap useEffect won't fire on mount because
    // the state hasn't changed). Default is dark — see `useState` above.
    if (basemap === 'satellite') {
      const satelliteLayer = L.tileLayer(
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        { attribution: 'Tiles © Esri — Source: Esri, USGS, NOAA', maxZoom: 20 }
      );
      const labelsLayer = L.tileLayer(
        'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
        { maxZoom: 20, opacity: 0.8 }
      );
      satelliteLayer.addTo(map);
      labelsLayer.addTo(map);
      tileLayerRef.current = satelliteLayer;
    } else {
      const darkLayer = L.tileLayer(
        'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        {
          attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors © <a href="https://carto.com/">CARTO</a>',
          subdomains: 'abcd',
          maxZoom: 20,
        }
      );
      darkLayer.addTo(map);
      tileLayerRef.current = darkLayer;
    }

    mapRef.current = map;
    // Hand the Leaflet instance to App so DC overlays (GridLayers,
    // AnalysisLines) can mount on the same map.
    onMapReady?.(map);

    // Draw layer
    const drawnItems = new L.FeatureGroup();
    map.addLayer(drawnItems);
    drawnItemsRef.current = drawnItems;

    // Polygon draw options
    const polygonOptions = {
      allowIntersection: true,
      showArea: false,
      metric: false,
      showLength: false,
      shapeOptions: {
        color: '#5de0a0',
        weight: 2,
        fillColor: '#5de0a0',
        fillOpacity: 0.15,
      },
    };

    // Create the draw handler ONCE and store in ref
    const drawHandler = new (L.Draw as any).Polygon(map, polygonOptions);
    drawHandlerRef.current = drawHandler;

    // Single CREATED listener — forwards geometry to parent and discards
    // the drawn outline. The polygon is just a selection tool: once the
    // parcels inside are identified, leaving its outline on the map
    // clutters the view and gets confused for actual parcel linework
    // (especially when later exported to Rhino).
    map.on(L.Draw.Event.CREATED, (e: any) => {
      drawnItems.clearLayers();
      const geojson = e.layer.toGeoJSON();
      onShapeDrawnRef.current?.(geojson.geometry as GeoJSON.Polygon);
    });

    return () => {
      onMapReady?.(null);
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-render parcel layer when parcels, selection, or viewMode changes
  useEffect(() => {
    if (!mapRef.current) return;

    if (layerRef.current) {
      layerRef.current.remove();
      layerRef.current = null;
    }

    // Remove old legend
    if (legendRef.current) {
      legendRef.current.remove();
      legendRef.current = null;
    }

    // Rebuild legend
    const legend = new L.Control({ position: 'bottomleft' });
    legend.onAdd = () => {
      const div = L.DomUtil.create('div');
      div.innerHTML = buildLegendHtml(viewMode, parcels);
      return div;
    };
    legend.addTo(mapRef.current);
    legendRef.current = legend;

    if (!parcels || parcels.features.length === 0) return;

    // When shapeHighlightIds is active, only render those parcels
    const filteredParcels: GeoJSON.FeatureCollection = shapeHighlightIds && shapeHighlightIds.size > 0
      ? {
          type: 'FeatureCollection',
          features: parcels.features.filter(f => {
            const pid = (f.properties as ParcelProperties).parcel_id;
            return shapeHighlightIds.has(pid);
          }),
        }
      : parcels as GeoJSON.FeatureCollection;

    if (filteredParcels.features.length === 0) return;

    const geoLayer = L.geoJSON(filteredParcels, {
      style: (feature) => {
        const props = feature?.properties as ParcelProperties;
        const isHighlighted = shapeHighlightIds?.has(props.parcel_id);
        if (isHighlighted) {
          return {
            fillColor: getParcelColor(props, viewMode),
            color: '#ffffff',
            weight: 3,
            fillOpacity: 0.3,
          };
        }
        return parcelStyle(props, props.parcel_id === selectedParcelId, viewMode);
      },
      onEachFeature: (feature, layer) => {
        const props = feature.properties as ParcelProperties;
        layer.on('click', () => {
          // Compute centroid from geometry and attach to props for 3D view
          const centroid = computeCentroid(feature.geometry as GeoJSON.Geometry);
          onParcelClick({ ...props, _centroid_lng: centroid[0], _centroid_lat: centroid[1] } as ParcelProperties);
        });
        layer.bindTooltip(
          `<strong>${props.address || props.parcel_id}</strong><br/>
           Zone: <strong>${props.zoning_code ?? '—'}</strong> &nbsp;·&nbsp;
           Tier ${props.tier ?? '?'} &nbsp;·&nbsp; Score: ${props.score?.toFixed(0) ?? '—'}<br/>
           ${props.lot_area_sqft ? (props.lot_area_sqft / 43560).toFixed(2) + ' ac' : ''}`,
          { sticky: true, className: 'plinth-tooltip' }
        );
      },
    });

    geoLayer.addTo(mapRef.current);
    layerRef.current = geoLayer;

    // Fit bounds on first data load
    try {
      const bounds = geoLayer.getBounds();
      if (bounds.isValid()) {
        mapRef.current.fitBounds(bounds, { padding: [20, 20] });
      }
    } catch (_) {}

  }, [parcels, selectedParcelId, onParcelClick, viewMode, shapeHighlightIds]);

  // Fly to focused parcel
  useEffect(() => {
    if (!focusParcelId || !mapRef.current || !layerRef.current) return;

    layerRef.current.eachLayer((layer: any) => {
      const feature = layer.feature;
      if (feature?.properties?.parcel_id === focusParcelId) {
        try {
          const bounds = layer.getBounds?.();
          if (bounds?.isValid()) {
            mapRef.current!.flyToBounds(bounds, { padding: [100, 100], maxZoom: 18, duration: 1 });
          }
        } catch (_) {}
      }
    });
  }, [focusParcelId, parcels]);

  return (
    <>
      <style>{`
        .plinth-tooltip {
          background: #141414 !important;
          border: 1px solid #333 !important;
          color: #e0e0e0 !important;
          font-size: 12px !important;
          padding: 6px 10px !important;
          border-radius: 4px !important;
          box-shadow: 0 2px 8px rgba(0,0,0,0.6) !important;
        }
        .plinth-tooltip::before { display: none !important; }
        .leaflet-control-zoom a {
          background: #1a1a1a !important;
          color: #ccc !important;
          border-color: #333 !important;
        }
        .leaflet-control-zoom a:hover { background: #2a2a2a !important; }
        .leaflet-control-attribution {
          background: rgba(0,0,0,0.5) !important;
          color: #555 !important;
          font-size: 9px !important;
        }
        .leaflet-control-attribution a { color: #666 !important; }
        .leaflet-draw-toolbar a {
          background-color: #1a1a1a !important;
          border-color: #2a2a2a !important;
        }
        .leaflet-draw-toolbar a:hover { background-color: #2a2a2a !important; }
        .leaflet-draw-tooltip {
          background: #1a1a1a !important;
          border: 1px solid #333 !important;
          color: #5de0a0 !important;
          font-family: monospace !important;
        }
        .leaflet-draw-guide-dash { background: #5de0a0 !important; }
      `}</style>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      {/* Draw Area button */}
      <button
        onClick={() => {
          drawHandlerRef.current?.enable();
        }}
        style={{
          position: 'absolute',
          top: 80,
          left: 10,
          zIndex: 1000,
          background: '#1a1a1a',
          border: '1px solid #333',
          color: '#5de0a0',
          padding: '6px 12px',
          borderRadius: 4,
          fontSize: 12,
          cursor: 'pointer',
          fontWeight: 600,
        }}
      >
        Draw Area
      </button>
      {/* Clear button — visible as soon as a polygon is drawn */}
      {hasActiveShape && (
        <button
          onClick={() => {
            drawnItemsRef.current?.clearLayers();
            onClearShape?.();
          }}
          style={{
            position: 'absolute',
            top: 112,
            left: 10,
            zIndex: 1000,
            background: '#1a1a1a',
            border: '1px solid #333',
            color: '#e05d5d',
            padding: '6px 12px',
            borderRadius: 4,
            fontSize: 12,
            cursor: 'pointer',
            fontWeight: 600,
          }}
        >
          Clear
        </button>
      )}

      {/* Basemap toggle */}
      <div style={{
        position: 'absolute',
        bottom: 28,
        right: 10,
        zIndex: 1000,
        display: 'flex',
        borderRadius: 6,
        overflow: 'hidden',
        border: '1px solid #333',
        boxShadow: '0 2px 8px rgba(0,0,0,0.6)',
      }}>
        <button
          onClick={() => setBasemap('satellite')}
          style={{
            background: basemap === 'satellite' ? '#5de0a0' : '#1a1a1a',
            color: basemap === 'satellite' ? '#000' : '#aaa',
            border: 'none',
            padding: '5px 11px',
            fontSize: 11,
            fontWeight: 700,
            cursor: 'pointer',
            letterSpacing: '0.03em',
          }}
        >
          🛰 Satellite
        </button>
        <button
          onClick={() => setBasemap('dark')}
          style={{
            background: basemap === 'dark' ? '#5de0a0' : '#1a1a1a',
            color: basemap === 'dark' ? '#000' : '#aaa',
            border: 'none',
            borderLeft: '1px solid #333',
            padding: '5px 11px',
            fontSize: 11,
            fontWeight: 700,
            cursor: 'pointer',
            letterSpacing: '0.03em',
          }}
        >
          🌑 Dark
        </button>
      </div>
    </>
  );
};
