import { useEffect, useRef } from 'react';
import L from 'leaflet';
import {
  fetchSubstations,
  fetchTransmissionLines,
  fetchPowerPlants,
  fetchIsoRto,
  fetchServiceTerritory,
} from '../api/datacenter';
import type { GridLayerKey } from '../types/datacenter';

interface Props {
  map: L.Map | null;
  enabled: Set<string>;
  highlightedUtilityIdEia?: number | null;
}

/**
 * Imperatively manages five Leaflet layer groups based on `enabled`.
 *
 * Refetches on map move/zoom (debounced, 350ms). Below zoom 7 we skip
 * point/line fetches because the national datasets are too dense to
 * be useful at that scale; ISO polygons stay visible at all zooms.
 */
export const GridLayers: React.FC<Props> = ({ map, enabled, highlightedUtilityIdEia }) => {
  const groupsRef = useRef<Record<GridLayerKey, L.LayerGroup | null>>({
    subs: null, lines: null, plants: null, iso: null, utility: null,
  });
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Initialize / cleanup layer groups whenever `map` changes
  useEffect(() => {
    if (!map) return;
    const groups = groupsRef.current;
    (Object.keys(groups) as GridLayerKey[]).forEach(k => {
      if (!groups[k]) {
        groups[k] = L.layerGroup();
      }
    });
    return () => {
      Object.values(groups).forEach(g => {
        if (g) {
          g.clearLayers();
          map.removeLayer(g);
        }
      });
    };
  }, [map]);

  // Add/remove groups from the map when `enabled` changes
  useEffect(() => {
    if (!map) return;
    const groups = groupsRef.current;
    (Object.keys(groups) as GridLayerKey[]).forEach(k => {
      const g = groups[k];
      if (!g) return;
      if (enabled.has(k)) {
        if (!map.hasLayer(g)) g.addTo(map);
      } else {
        if (map.hasLayer(g)) map.removeLayer(g);
      }
    });
  }, [map, enabled]);

  // Refetch on map move (debounced) or layer toggle / utility change
  useEffect(() => {
    if (!map) return;

    const refetch = () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        const b = map.getBounds();
        const bbox: [number, number, number, number] = [
          b.getWest(), b.getSouth(), b.getEast(), b.getNorth(),
        ];
        const zoom = map.getZoom();
        loadAll(groupsRef.current, enabled, bbox, zoom, highlightedUtilityIdEia ?? null);
      }, 350);
    };

    refetch();
    map.on('moveend zoomend', refetch);
    return () => {
      map.off('moveend zoomend', refetch);
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [map, enabled, highlightedUtilityIdEia]);

  return null;
};


// --- Drawing logic (Leaflet) ----------------------------------------

function loadAll(
  groups: Record<GridLayerKey, L.LayerGroup | null>,
  enabled: Set<string>,
  bbox: [number, number, number, number],
  zoom: number,
  utilityIdEia: number | null,
): void {
  // ISO polygons are coarse and useful at all zoom levels.
  if (enabled.has('iso') && groups.iso) {
    drawIso(groups.iso, bbox);
  }

  // Point/line layers shouldn't show below zoom 7 (continental view) —
  // the national datasets would render as a solid blob and overload
  // the API.
  const showDense = zoom >= 7;

  if (enabled.has('subs') && groups.subs) {
    if (showDense) drawSubstations(groups.subs, bbox, zoom);
    else groups.subs.clearLayers();
  }
  if (enabled.has('lines') && groups.lines) {
    if (showDense) drawTransmissionLines(groups.lines, bbox, zoom);
    else groups.lines.clearLayers();
  }
  if (enabled.has('plants') && groups.plants) {
    if (showDense) drawPowerPlants(groups.plants, bbox);
    else groups.plants.clearLayers();
  }
  if (enabled.has('utility') && groups.utility) {
    drawUtilityTerritory(groups.utility, utilityIdEia, bbox);
  }
}


function voltageToColor(kv: number | null): string {
  if (kv == null) return '#666';
  if (kv >= 500) return '#ef4444';
  if (kv >= 345) return '#f97316';
  if (kv >= 230) return '#eab308';
  if (kv >= 115) return '#84cc16';
  return '#3b82f6';
}

function voltageToWeight(kv: number | null): number {
  if (kv == null) return 1.5;
  if (kv >= 500) return 4;
  if (kv >= 345) return 3;
  if (kv >= 230) return 2.5;
  if (kv >= 115) return 1.8;
  return 1.2;
}

const FUEL_COLOR: Record<string, string> = {
  nuclear: '#a855f7',
  gas: '#ef4444',
  coal: '#1f2937',
  wind: '#06b6d4',
  solar: '#facc15',
  hydro: '#0ea5e9',
  oil: '#7c2d12',
  biomass: '#65a30d',
  geothermal: '#9a3412',
  battery: '#3b82f6',
  other: '#6b7280',
};


async function drawSubstations(group: L.LayerGroup, bbox: [number, number, number, number], zoom: number): Promise<void> {
  // Above zoom 9 show all; below show only >=115kV to keep counts sane.
  const minKv = zoom >= 9 ? undefined : 115;
  try {
    const data = await fetchSubstations(bbox, { minVoltageKv: minKv });
    group.clearLayers();
    for (const f of data.features) {
      if (!f.geometry || f.geometry.type !== 'Point') continue;
      const [lng, lat] = (f.geometry as GeoJSON.Point).coordinates as [number, number];
      const props = f.properties as { name?: string; operator?: string; max_voltage_kv?: number; min_voltage_kv?: number };
      const color = voltageToColor(props.max_voltage_kv ?? null);
      const r = props.max_voltage_kv && props.max_voltage_kv >= 230 ? 6 : 4;
      const marker = L.circleMarker([lat, lng], {
        radius: r,
        fillColor: color,
        color: '#000',
        weight: 1,
        fillOpacity: 0.85,
      });
      marker.bindTooltip(
        `<b>${props.name ?? '(unnamed)'}</b><br/>${props.operator ?? '—'}<br/>${props.max_voltage_kv ?? '?'} kV`,
        { sticky: true, direction: 'top' as const },
      );
      group.addLayer(marker);
    }
  } catch (e) {
    console.warn('substations fetch failed', e);
  }
}


async function drawTransmissionLines(group: L.LayerGroup, bbox: [number, number, number, number], zoom: number): Promise<void> {
  const minKv = zoom >= 9 ? undefined : 115;
  try {
    const data = await fetchTransmissionLines(bbox, { minVoltageKv: minKv });
    group.clearLayers();
    for (const f of data.features) {
      if (!f.geometry) continue;
      const props = f.properties as { voltage_kv?: number; owner?: string; voltage_class?: string };
      const color = voltageToColor(props.voltage_kv ?? null);
      const weight = voltageToWeight(props.voltage_kv ?? null);
      const layer = L.geoJSON(f as GeoJSON.Feature, {
        style: () => ({ color, weight, opacity: 0.8 }),
      });
      layer.bindTooltip(
        `${props.owner ?? '—'}<br/>${props.voltage_kv ?? '?'} kV (${props.voltage_class ?? '—'})`,
        { sticky: true, direction: 'top' as const },
      );
      group.addLayer(layer);
    }
  } catch (e) {
    console.warn('transmission lines fetch failed', e);
  }
}


async function drawPowerPlants(group: L.LayerGroup, bbox: [number, number, number, number]): Promise<void> {
  try {
    const data = await fetchPowerPlants(bbox, {});
    group.clearLayers();
    for (const f of data.features) {
      if (!f.geometry || f.geometry.type !== 'Point') continue;
      const [lng, lat] = (f.geometry as GeoJSON.Point).coordinates as [number, number];
      const props = f.properties as { name?: string; primary_fuel?: string; total_mw?: number; summer_capacity_mw?: number };
      const color = FUEL_COLOR[props.primary_fuel ?? 'other'] ?? FUEL_COLOR.other;
      const cap = props.summer_capacity_mw ?? props.total_mw ?? 0;
      // Bigger plants get bigger circles, capped.
      const r = Math.max(4, Math.min(14, 4 + Math.log10(Math.max(1, cap)) * 2));
      const marker = L.circleMarker([lat, lng], {
        radius: r,
        fillColor: color,
        color: '#000',
        weight: 1,
        fillOpacity: 0.9,
      });
      marker.bindTooltip(
        `<b>${props.name ?? '(unnamed)'}</b><br/>${props.primary_fuel ?? '?'} — ${cap.toLocaleString()} MW`,
        { sticky: true, direction: 'top' as const },
      );
      group.addLayer(marker);
    }
  } catch (e) {
    console.warn('power plants fetch failed', e);
  }
}


async function drawIso(group: L.LayerGroup, bbox: [number, number, number, number]): Promise<void> {
  try {
    const data = await fetchIsoRto(bbox);
    group.clearLayers();
    for (const f of data.features) {
      if (!f.geometry) continue;
      const props = f.properties as { ba_code?: string; ba_name?: string; iso_rto?: string };
      const layer = L.geoJSON(f as GeoJSON.Feature, {
        style: () => ({
          color: '#475569',
          weight: 1,
          opacity: 0.6,
          fillColor: '#1e293b',
          fillOpacity: 0.08,
        }),
      });
      layer.bindTooltip(
        `<b>${props.iso_rto ?? '?'}</b><br/>${props.ba_name ?? props.ba_code ?? ''}`,
        { sticky: true, direction: 'top' as const },
      );
      group.addLayer(layer);
    }
  } catch (e) {
    console.warn('ISO/BA fetch failed', e);
  }
}


async function drawUtilityTerritory(group: L.LayerGroup, utilityIdEia: number | null, bbox: [number, number, number, number]): Promise<void> {
  try {
    if (utilityIdEia == null) {
      // Without a specific utility selected, show all in the bbox at low opacity.
      const data = await fetchServiceTerritory(null, bbox);
      group.clearLayers();
      for (const f of data.features) {
        if (!f.geometry) continue;
        const props = f.properties as { utility_name?: string; utility_id_eia?: number };
        const layer = L.geoJSON(f as GeoJSON.Feature, {
          style: () => ({
            color: '#0ea5e9',
            weight: 0.8,
            opacity: 0.5,
            fillColor: '#0ea5e9',
            fillOpacity: 0.04,
          }),
        });
        layer.bindTooltip(props.utility_name ?? '?', { sticky: true });
        group.addLayer(layer);
      }
      return;
    }
    const data = await fetchServiceTerritory(utilityIdEia, null);
    group.clearLayers();
    for (const f of data.features) {
      if (!f.geometry) continue;
      const props = f.properties as { utility_name?: string };
      const layer = L.geoJSON(f as GeoJSON.Feature, {
        style: () => ({
          color: '#0ea5e9',
          weight: 2,
          opacity: 0.9,
          fillColor: '#0ea5e9',
          fillOpacity: 0.15,
        }),
      });
      layer.bindTooltip(`<b>${props.utility_name ?? '—'}</b><br/>(serving utility)`, { sticky: true });
      group.addLayer(layer);
    }
  } catch (e) {
    console.warn('service territory fetch failed', e);
  }
}
