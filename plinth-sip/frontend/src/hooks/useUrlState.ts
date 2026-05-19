import { useCallback, useEffect, useState } from 'react';

/**
 * Minimal URL search-param state hook.
 *
 * The existing app encodes nothing in the URL; this hook adds DC-mode
 * persistence (mode toggle, layer toggles, optional active-parcel) for
 * shareable analysis links without pulling in a router.
 *
 * `read` runs once on mount and synchronously parses the current URL.
 * `update` writes via `history.replaceState` so back/forward navigation
 * isn't polluted with toggle changes.
 */
export interface UrlState {
  dcMode: boolean;
  dcLayers: Set<string>;
  dcParcel: { municipalityId: string; parcelId: string } | null;
}

const DEFAULT_LAYERS = new Set(['subs', 'lines', 'plants', 'iso']);

function readFromLocation(): UrlState {
  if (typeof window === 'undefined') {
    return { dcMode: false, dcLayers: new Set(DEFAULT_LAYERS), dcParcel: null };
  }
  const params = new URLSearchParams(window.location.search);
  const dcMode = params.get('dcMode') === '1' || params.get('dcMode') === 'true';
  const layersRaw = params.get('dcLayers');
  const dcLayers = layersRaw
    ? new Set(layersRaw.split(',').map(s => s.trim()).filter(Boolean))
    : new Set(DEFAULT_LAYERS);

  const dcParcelRaw = params.get('dcParcel');
  let dcParcel: UrlState['dcParcel'] = null;
  if (dcParcelRaw) {
    const idx = dcParcelRaw.indexOf('/');
    if (idx > 0) {
      dcParcel = {
        municipalityId: dcParcelRaw.slice(0, idx),
        parcelId: dcParcelRaw.slice(idx + 1),
      };
    }
  }
  return { dcMode, dcLayers, dcParcel };
}

function writeToLocation(s: UrlState): void {
  if (typeof window === 'undefined') return;
  const params = new URLSearchParams(window.location.search);
  if (s.dcMode) params.set('dcMode', '1'); else params.delete('dcMode');
  if (s.dcLayers.size > 0 && s.dcMode) {
    params.set('dcLayers', Array.from(s.dcLayers).sort().join(','));
  } else {
    params.delete('dcLayers');
  }
  if (s.dcParcel) {
    params.set('dcParcel', `${s.dcParcel.municipalityId}/${s.dcParcel.parcelId}`);
  } else {
    params.delete('dcParcel');
  }
  const qs = params.toString();
  const newUrl = `${window.location.pathname}${qs ? '?' + qs : ''}${window.location.hash}`;
  window.history.replaceState(null, '', newUrl);
}

export function useUrlState() {
  const [state, setState] = useState<UrlState>(() => readFromLocation());

  // Persist on every change.
  useEffect(() => {
    writeToLocation(state);
  }, [state]);

  const setDcMode = useCallback((dcMode: boolean) => {
    setState(s => ({ ...s, dcMode }));
  }, []);

  const setDcLayers = useCallback((dcLayers: Set<string>) => {
    setState(s => ({ ...s, dcLayers: new Set(dcLayers) }));
  }, []);

  const toggleLayer = useCallback((key: string) => {
    setState(s => {
      const next = new Set(s.dcLayers);
      if (next.has(key)) next.delete(key); else next.add(key);
      return { ...s, dcLayers: next };
    });
  }, []);

  const setDcParcel = useCallback((dcParcel: UrlState['dcParcel']) => {
    setState(s => ({ ...s, dcParcel }));
  }, []);

  return {
    state,
    setDcMode,
    setDcLayers,
    toggleLayer,
    setDcParcel,
  };
}
