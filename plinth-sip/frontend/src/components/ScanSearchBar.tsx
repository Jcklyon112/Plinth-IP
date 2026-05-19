import React, { useState, useRef, useEffect } from 'react';
import { startAutoScan, getScanStatus, searchParcel } from '../api/client';
import type { AutoScanResult, ScanRunStatus, ParcelSearchResult } from '../api/client';
import type { ParcelProperties } from '../types/parcel';

interface Props {
  onScanComplete: (municipalityId: string, municipalityName: string) => void;
  onParcelFound?: (parcel: ParcelProperties, municipalityId: string, geometry: GeoJSON.Geometry | null) => void;
}

const STATUS_LABELS: Record<string, string> = {
  queued:        'Queued...',
  fetching:      'Fetching parcels from GIS...',
  configuring:   'Generating municipality config...',
  loading_config:'Loading config into database...',
  ingesting:     'Ingesting parcels into database...',
  scoring:       'Running rules engine & scoring...',
  complete:      'Scan complete!',
  failed:        'Scan failed',
};

const STATUS_PROGRESS: Record<string, number> = {
  queued:        5,
  fetching:      20,
  configuring:   45,
  loading_config:55,
  ingesting:     65,
  scoring:       80,
  complete:      100,
  failed:        100,
};

function looksLikeZip(q: string): boolean {
  return /^\d{5}(-\d{4})?$/.test(q.trim());
}

function looksLikeAddress(q: string): boolean {
  // Starts with a number followed by a street name
  return /^\d+\s+\w/.test(q.trim());
}

export function ScanSearchBar({ onScanComplete, onParcelFound }: Props) {
  const [query, setQuery] = useState('');
  const [scanning, setScanning] = useState(false);
  const [searching, setSearching] = useState(false);
  const [scanResult, setScanResult] = useState<AutoScanResult | null>(null);
  const [scanStatus, setScanStatus] = useState<ScanRunStatus | null>(null);
  const [searchResult, setSearchResult] = useState<ParcelSearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offerScan, setOfferScan] = useState<{ municipalityId: string; municipalityName: string; state: string } | null>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const startScan = async (q: string) => {
    setScanning(true);
    setError(null);
    setScanResult(null);
    setScanStatus(null);
    setSearchResult(null);
    setOfferScan(null);

    try {
      const result = await startAutoScan(q);
      setScanResult(result);

      pollRef.current = window.setInterval(async () => {
        try {
          const status = await getScanStatus(result.scan_run_id);
          setScanStatus(status);

          if (status.status === 'complete') {
            clearInterval(pollRef.current!);
            pollRef.current = null;
            setScanning(false);
            setTimeout(() => {
              onScanComplete(result.municipality_id, result.municipality_name);
              setScanResult(null);
              setScanStatus(null);
              setQuery('');
            }, 1500);
          } else if (status.status === 'failed') {
            clearInterval(pollRef.current!);
            pollRef.current = null;
            setScanning(false);
            setError(`Scan failed: ${status.error_log || 'Unknown error'}`);
          }
        } catch {
          // ignore poll errors
        }
      }, 2500);
    } catch (err: any) {
      setScanning(false);
      const msg = err?.response?.data?.detail || err?.message || 'Failed to start scan';
      setError(msg);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = query.trim();
    if (!q || scanning || searching) return;

    // If user typed a full address, try parcel search first
    if (looksLikeAddress(q)) {
      setSearching(true);
      setError(null);
      setSearchResult(null);
      setOfferScan(null);

      try {
        const result = await searchParcel(q);
        setSearchResult(result);

        if (result.status === 'found' && result.parcel && onParcelFound) {
          onParcelFound(result.parcel, result.municipality_id, result.geometry ?? null);
          setQuery('');
        } else if (result.status === 'not_scanned') {
          setOfferScan({
            municipalityId: result.municipality_id,
            municipalityName: result.municipality_name,
            state: result.state,
          });
        } else if (result.status === 'no_match') {
          // Municipality is scanned, switch to it
          onScanComplete(result.municipality_id, result.municipality_name);
          setError(result.message || 'Parcel not found, but municipality loaded on map.');
        }
      } catch (err: any) {
        const msg = err?.response?.data?.detail || err?.message || 'Search failed';
        setError(msg);
      } finally {
        setSearching(false);
      }
      return;
    }

    // Zip codes and town+state: run full scan
    await startScan(q);
  };

  const handleOfferAccept = () => {
    if (offerScan) {
      setOfferScan(null);
      startScan(`${offerScan.municipalityName}, ${offerScan.state}`);
    }
  };

  const currentStatus = scanStatus?.status || (scanning ? 'queued' : null);
  const statusLabel = currentStatus ? STATUS_LABELS[currentStatus] || currentStatus : null;
  const progress = currentStatus ? STATUS_PROGRESS[currentStatus] || 5 : 0;
  const isComplete = currentStatus === 'complete';
  const isFailed = currentStatus === 'failed';

  return (
    <div style={styles.wrapper}>
      <form onSubmit={handleSubmit} style={styles.form}>
        <div style={styles.inputRow}>
          <span style={styles.icon}>&#x1F50D;</span>
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder='Search address, zip code, or town — try "11963" or "14 Main St, Southampton NY"'
            style={styles.input}
            disabled={scanning || searching}
          />
          <button
            type="submit"
            style={{
              ...styles.button,
              ...(scanning || searching ? styles.buttonDisabled : {}),
            }}
            disabled={scanning || searching || !query.trim()}
          >
            {scanning ? 'Scanning...' : searching ? 'Searching...' : 'Search'}
          </button>
        </div>

        {/* Progress bar for scan */}
        {scanning && (
          <div style={styles.progressContainer}>
            <div style={styles.progressBg}>
              <div
                style={{
                  ...styles.progressFill,
                  width: `${progress}%`,
                  background: isComplete ? '#22c55e' : isFailed ? '#ef4444' : '#5de0a0',
                  transition: 'width 0.6s ease',
                }}
              />
            </div>
            <div style={styles.statusRow}>
              <span style={{
                ...styles.statusLabel,
                color: isComplete ? '#22c55e' : isFailed ? '#ef4444' : '#5de0a0',
              }}>
                {isComplete ? 'OK ' : isFailed ? 'X ' : ''}
                {statusLabel}
              </span>
              {scanResult && (
                <span style={styles.muniLabel}>
                  {scanResult.municipality_name}, {scanResult.state}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Offer to scan */}
        {offerScan && !scanning && (
          <div style={styles.offerRow}>
            <span style={styles.offerText}>
              {offerScan.municipalityName}, {offerScan.state} not yet scanned.
            </span>
            <button type="button" style={styles.offerBtn} onClick={handleOfferAccept}>
              Scan it now
            </button>
            <button type="button" style={styles.dismissBtn} onClick={() => setOfferScan(null)}>
              x
            </button>
          </div>
        )}

        {/* Search result - found */}
        {searchResult?.status === 'found' && !scanning && (
          <div style={styles.foundRow}>
            <span style={styles.foundText}>
              Found: {searchResult.parcel?.address || searchResult.parcel?.parcel_id} —
              Tier {searchResult.parcel?.tier ?? '?'}, Score {searchResult.parcel?.score?.toFixed(0) ?? '?'}
            </span>
            <button type="button" style={styles.dismissBtn} onClick={() => setSearchResult(null)}>
              x
            </button>
          </div>
        )}

        {/* Error message */}
        {error && !scanning && (
          <div style={styles.errorRow}>
            <span style={styles.errorText}>X {error}</span>
            <button type="button" style={styles.dismissBtn} onClick={() => setError(null)}>
              x
            </button>
          </div>
        )}
      </form>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    background: '#0e0e0e',
    borderBottom: '1px solid #1e1e1e',
    padding: '8px 20px',
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  inputRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    background: '#1a1a1a',
    border: '1px solid #2a2a2a',
    borderRadius: 6,
    padding: '6px 12px',
  },
  icon: {
    fontSize: 14,
    color: '#555',
    flexShrink: 0,
  },
  input: {
    flex: 1,
    background: 'transparent',
    border: 'none',
    outline: 'none',
    color: '#e0e0e0',
    fontSize: 13,
    fontFamily: 'inherit',
  },
  button: {
    background: '#5de0a0',
    color: '#000',
    border: 'none',
    borderRadius: 4,
    padding: '4px 14px',
    fontSize: 12,
    fontWeight: 700,
    cursor: 'pointer',
    flexShrink: 0,
    letterSpacing: '0.02em',
  },
  buttonDisabled: {
    background: '#2a4a3a',
    color: '#555',
    cursor: 'not-allowed',
  },
  progressContainer: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  progressBg: {
    height: 3,
    background: '#222',
    borderRadius: 2,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    borderRadius: 2,
  },
  statusRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  statusLabel: {
    fontSize: 11,
    fontFamily: 'monospace',
  },
  muniLabel: {
    fontSize: 11,
    color: '#555',
  },
  offerRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    background: '#1a1a0e',
    border: '1px solid #3a3a1a',
    borderRadius: 4,
    padding: '6px 10px',
  },
  offerText: {
    fontSize: 12,
    color: '#eab308',
    flex: 1,
  },
  offerBtn: {
    background: '#eab308',
    color: '#000',
    border: 'none',
    borderRadius: 4,
    padding: '3px 10px',
    fontSize: 11,
    fontWeight: 700,
    cursor: 'pointer',
  },
  foundRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    background: '#0a1e0a',
    border: '1px solid #1a3a1a',
    borderRadius: 4,
    padding: '4px 10px',
  },
  foundText: {
    fontSize: 12,
    color: '#22c55e',
  },
  errorRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    background: '#1e0a0a',
    border: '1px solid #3a1a1a',
    borderRadius: 4,
    padding: '4px 10px',
  },
  errorText: {
    fontSize: 12,
    color: '#e05c5c',
  },
  dismissBtn: {
    background: 'none',
    border: 'none',
    color: '#666',
    cursor: 'pointer',
    fontSize: 16,
    padding: '0 4px',
    lineHeight: 1,
  },
};
