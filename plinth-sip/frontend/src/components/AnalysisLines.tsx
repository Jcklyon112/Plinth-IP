import { useEffect, useRef } from 'react';
import L from 'leaflet';
import type { DcAnalysisResult } from '../types/datacenter';

interface Props {
  map: L.Map | null;
  analysis: DcAnalysisResult | null;
}

/**
 * Draws four polylines from the parcel centroid to:
 *   - the nearest >=115kV transmission substation
 *   - the nearest transmission line (snap point on the line)
 *   - the nearest fiber feature
 *   - the nearest gas pipeline
 *
 * Each labeled with the distance in miles. Lines are dashed and
 * color-coded so they're distinguishable when several stack near a
 * small parcel.
 */
const COLORS = {
  substation: '#f59e0b',
  line: '#a78bfa',
  fiber: '#06b6d4',
  gas: '#ef4444',
};

export const AnalysisLines: React.FC<Props> = ({ map, analysis }) => {
  const groupRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!map) return;
    if (!groupRef.current) groupRef.current = L.layerGroup().addTo(map);
    return () => {
      if (groupRef.current) {
        groupRef.current.clearLayers();
        map.removeLayer(groupRef.current);
        groupRef.current = null;
      }
    };
  }, [map]);

  useEffect(() => {
    const g = groupRef.current;
    if (!g) return;
    g.clearLayers();
    if (!analysis) return;

    const [pLng, pLat] = analysis.parcelCentroid;
    const parcelPt: L.LatLngExpression = [pLat, pLng];

    type Edge = {
      label: string;
      color: string;
      target: [number, number] | undefined;  // [lng, lat]
      distMi: number | null | undefined;
    };

    const edges: Edge[] = [
      {
        label: '>=115kV substation',
        color: COLORS.substation,
        target: analysis.grid.nearestTransmissionSubstation?.coords,
        distMi: analysis.grid.nearestTransmissionSubstation?.distanceMi ?? null,
      },
      {
        label: 'transmission line',
        color: COLORS.line,
        target: analysis.grid.nearestTransmissionLine?.coords,
        distMi: analysis.grid.nearestTransmissionLine?.distanceMi ?? null,
      },
      {
        label: 'fiber',
        color: COLORS.fiber,
        target: analysis.infrastructure.nearestFiber?.coords,
        distMi: analysis.infrastructure.nearestFiber?.distanceMi ?? null,
      },
      {
        label: 'gas pipeline',
        color: COLORS.gas,
        target: analysis.infrastructure.nearestGasPipeline?.coords,
        distMi: analysis.infrastructure.nearestGasPipeline?.distanceMi ?? null,
      },
    ];

    // Anchor point at the parcel centroid
    const anchor = L.circleMarker(parcelPt, {
      radius: 5,
      fillColor: '#5de0a0',
      color: '#000',
      weight: 1,
      fillOpacity: 1,
    }).bindTooltip('Parcel centroid', { direction: 'top' });
    g.addLayer(anchor);

    for (const edge of edges) {
      if (!edge.target || edge.distMi == null) continue;
      const targetPt: L.LatLngExpression = [edge.target[1], edge.target[0]];
      const line = L.polyline([parcelPt, targetPt], {
        color: edge.color,
        weight: 2,
        opacity: 0.85,
        dashArray: '4 4',
      });
      line.bindTooltip(`${edge.label}: ${edge.distMi} mi`, { sticky: true });
      g.addLayer(line);

      // Distance label at the midpoint
      const midLat = (parcelPt as [number, number])[0] + (targetPt as [number, number])[0];
      const midLng = (parcelPt as [number, number])[1] + (targetPt as [number, number])[1];
      const midPt: L.LatLngExpression = [midLat / 2, midLng / 2];
      const label = L.marker(midPt, {
        icon: L.divIcon({
          className: 'dc-edge-label',
          html: `<span style="background:rgba(15,15,15,0.85);color:${edge.color};border:1px solid ${edge.color};padding:2px 6px;border-radius:3px;font-size:10px;font-weight:700;white-space:nowrap;">${edge.distMi} mi</span>`,
          iconSize: [60, 16],
          iconAnchor: [30, 8],
        }),
        interactive: false,
      });
      g.addLayer(label);

      // Endpoint marker (small, color-matched)
      const endMarker = L.circleMarker(targetPt, {
        radius: 4,
        fillColor: edge.color,
        color: '#000',
        weight: 1,
        fillOpacity: 1,
      }).bindTooltip(edge.label, { direction: 'top' });
      g.addLayer(endMarker);
    }
  }, [map, analysis]);

  return null;
};
