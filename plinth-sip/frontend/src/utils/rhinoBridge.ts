// Rhino bridge — writes a parcel-boundary DXF straight into a folder the
// Rhino watcher polls. The user's chosen FileSystemDirectoryHandle is
// persisted in IndexedDB so the folder is only picked once; permission must
// still be re-granted per session, which is one silent click — no folder
// browser. Falls back to a plain download if the File System Access API
// isn't available (Safari, older Edge).

import type { ParcelProperties } from '../types/parcel';
import { fetchZoningEnvelope } from '../api/client';
import type { ZoningEnvelope } from '../api/client';

const RHINO_DB = 'plinth-rhino-bridge';
const RHINO_STORE = 'handles';
const RHINO_KEY = 'inbox';

function fsAccessSupported(): boolean {
  return typeof (window as any).showDirectoryPicker === 'function';
}

function openRhinoDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(RHINO_DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(RHINO_STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function getStoredInboxHandle(): Promise<FileSystemDirectoryHandle | null> {
  try {
    const db = await openRhinoDb();
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(RHINO_STORE, 'readonly');
      const req = tx.objectStore(RHINO_STORE).get(RHINO_KEY);
      req.onsuccess = () => resolve((req.result as FileSystemDirectoryHandle) ?? null);
      req.onerror = () => reject(req.error);
    });
  } catch {
    return null;
  }
}

async function storeInboxHandle(handle: FileSystemDirectoryHandle): Promise<void> {
  const db = await openRhinoDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(RHINO_STORE, 'readwrite');
    tx.objectStore(RHINO_STORE).put(handle, RHINO_KEY);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function clearInboxHandle(): Promise<void> {
  try {
    const db = await openRhinoDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(RHINO_STORE, 'readwrite');
      tx.objectStore(RHINO_STORE).delete(RHINO_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    /* ignore */
  }
}

async function ensureRhinoInboxHandle(forcePicker = false): Promise<FileSystemDirectoryHandle> {
  let handle = forcePicker ? null : await getStoredInboxHandle();
  if (handle) {
    const opts = { mode: 'readwrite' as const };
    const current = await (handle as any).queryPermission(opts);
    if (current !== 'granted') {
      const requested = await (handle as any).requestPermission(opts);
      if (requested !== 'granted') handle = null;
    }
  }
  if (!handle) {
    handle = await (window as any).showDirectoryPicker({
      id: 'plinth-rhino-inbox',
      mode: 'readwrite',
      startIn: 'documents',
    });
    await storeInboxHandle(handle!);
  }
  return handle!;
}

function buildParcelDxf(
  geometry: GeoJSON.Geometry,
  envelope?: ZoningEnvelope | null,
): string {
  const polygons: number[][][] = [];
  if (geometry.type === 'Polygon') {
    polygons.push(...(geometry.coordinates as number[][][]));
  } else if (geometry.type === 'MultiPolygon') {
    for (const poly of geometry.coordinates as number[][][][]) {
      polygons.push(...poly);
    }
  } else {
    throw new Error(`Unsupported geometry type: ${geometry.type}`);
  }
  if (polygons.length === 0 || polygons[0].length === 0) {
    throw new Error('Parcel geometry has no rings');
  }

  // Origin = average of first ring's vertices (good enough for parcel-scale)
  const ring0 = polygons[0];
  const sum = ring0.reduce((acc, [x, y]) => [acc[0] + x, acc[1] + y], [0, 0]);
  const lon0 = sum[0] / ring0.length;
  const lat0 = sum[1] / ring0.length;

  // Local-tangent projection: degrees → meters → US survey feet
  const M_PER_DEG_LAT = 110540;
  const M_PER_DEG_LON = 111320 * Math.cos(lat0 * Math.PI / 180);
  const FT_PER_M = 3.28084;
  const project = (lon: number, lat: number): [number, number] => [
    (lon - lon0) * M_PER_DEG_LON * FT_PER_M,
    (lat - lat0) * M_PER_DEG_LAT * FT_PER_M,
  ];

  const out: string[] = [];

  // Zoning envelope metadata. Group code 999 is the DXF "comment" code --
  // standard DXF readers (incl. Rhino's) skip these lines, but the Plinth
  // Rhino watcher scans for a PLINTH_META_BEGIN/END block to pull setbacks,
  // height, and FAR into the Grasshopper massing definition. Putting it at
  // the very top of the file keeps the parser simple (one pass, early exit).
  if (envelope) {
    const meta: Array<[string, unknown]> = [
      ['municipality_id', envelope.municipality_id],
      ['parcel_id', envelope.parcel_id],
      ['zoning_code_raw', envelope.zoning_code_raw],
      ['district_key', envelope.district_key],
      ['district_label', envelope.district_label],
      ['front_setback_ft', envelope.front_setback_ft],
      ['side_setback_ft', envelope.side_setback_ft],
      ['rear_setback_ft', envelope.rear_setback_ft],
      ['max_height_ft', envelope.max_height_ft],
      ['max_lot_coverage', envelope.max_lot_coverage],
      ['max_far', envelope.max_far],
      ['min_lot_area_sqft', envelope.min_lot_area_sqft],
      ['lot_area_sqft', envelope.lot_area_sqft],
      ['config_version', envelope.config_version],
    ];
    out.push('999', 'PLINTH_META_BEGIN');
    for (const [k, v] of meta) {
      // Skip null/undefined so the watcher can detect "field unknown" vs
      // "field present but zero" (e.g., a real 0ft side setback).
      if (v === null || v === undefined) continue;
      out.push('999', `${k}=${v}`);
    }
    out.push('999', 'PLINTH_META_END');
  }

  // HEADER — declare units as decimal feet so Rhino imports at correct scale
  out.push(
    '0', 'SECTION',
    '2', 'HEADER',
    '9', '$ACADVER', '1', 'AC1015',
    '9', '$INSUNITS', '70', '2',
    '0', 'ENDSEC',
  );
  // TABLES — declare PARCEL layer up front. Rhino 7's DXF reader treats
  // entities referencing an undeclared layer as malformed and silently drops
  // them, so this section is required even though it looks like boilerplate.
  out.push(
    '0', 'SECTION',
    '2', 'TABLES',
    '0', 'TABLE',
    '2', 'LAYER',
    '70', '2',
    '0', 'LAYER',
    '2', '0',
    '70', '0',
    '62', '7',
    '6', 'CONTINUOUS',
    '0', 'LAYER',
    '2', 'PARCEL',
    '70', '0',
    '62', '4', // 4 = cyan
    '6', 'CONTINUOUS',
    '0', 'ENDTAB',
    '0', 'ENDSEC',
  );
  out.push(
    '0', 'SECTION',
    '2', 'ENTITIES',
  );
  for (const ring of polygons) {
    // GeoJSON closes the ring (last == first); drop the duplicate, use closed flag
    const last = ring[ring.length - 1];
    const first = ring[0];
    const verts = (last[0] === first[0] && last[1] === first[1])
      ? ring.slice(0, -1)
      : ring;
    if (verts.length < 3) continue;
    out.push(
      '0', 'LWPOLYLINE',
      '100', 'AcDbEntity',
      '8', 'PARCEL',
      '100', 'AcDbPolyline',
      '90', String(verts.length),
      '70', '1', // 1 = closed
    );
    for (const [lon, lat] of verts) {
      const [x, y] = project(lon, lat);
      out.push('10', x.toFixed(4), '20', y.toFixed(4));
    }
  }
  out.push('0', 'ENDSEC', '0', 'EOF');
  return out.join('\r\n');
}

function dxfFileName(parcel: ParcelProperties): string {
  const safeId = String(parcel.parcel_id || 'parcel').replace(/[^A-Za-z0-9._-]/g, '_');
  return `plinth_parcel_${safeId}_${Date.now()}.dxf`;
}

async function fetchEnvelopeOrNull(parcel: ParcelProperties): Promise<ZoningEnvelope | null> {
  // Best-effort: zoning envelope is enrichment, not required. If the backend
  // is offline, the parcel is missing, or the district isn't in the config,
  // fall through with no metadata and let the GH watcher use slider defaults.
  try {
    return await fetchZoningEnvelope(parcel.municipality_id, parcel.parcel_id);
  } catch {
    return null;
  }
}

export async function downloadParcelDxf(geometry: GeoJSON.Geometry, parcel: ParcelProperties): Promise<void> {
  const envelope = await fetchEnvelopeOrNull(parcel);
  const dxf = buildParcelDxf(geometry, envelope);
  const blob = new Blob([dxf], { type: 'application/dxf' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = dxfFileName(parcel);
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export type RhinoSendResult =
  | { status: 'sent'; fileName: string; envelope: ZoningEnvelope | null }
  | { status: 'fallback-downloaded' };

export async function sendParcelDxfToRhino(
  geometry: GeoJSON.Geometry,
  parcel: ParcelProperties,
  forcePicker = false,
): Promise<RhinoSendResult> {
  if (!fsAccessSupported()) {
    await downloadParcelDxf(geometry, parcel);
    return { status: 'fallback-downloaded' };
  }
  const dir = await ensureRhinoInboxHandle(forcePicker);
  const envelope = await fetchEnvelopeOrNull(parcel);
  const dxf = buildParcelDxf(geometry, envelope);
  const fileName = dxfFileName(parcel);
  const fileHandle = await dir.getFileHandle(fileName, { create: true });
  const writable = await (fileHandle as any).createWritable();
  await writable.write(dxf);
  await writable.close();
  return { status: 'sent', fileName, envelope };
}
