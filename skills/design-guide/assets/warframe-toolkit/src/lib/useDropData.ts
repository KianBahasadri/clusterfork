import { useEffect, useState } from 'react';
import { DROP_CACHE_POLL_MS, isDropDataset } from './dropTables';
import type { DropDataset } from './dropTables';
import { sampleDropDataset } from '../data/sampleFixtures';

// Re-entering the tab resumes this session without triggering another read.
const session: { data: DropDataset | null; etag: string | null; sourceError: string; readError: string; nextRead: number } = { data: sampleDropDataset, etag: null, sourceError: '', readError: '', nextRead: Date.now() + DROP_CACHE_POLL_MS };

/** Poll the local snapshot. Only the server's independent schedule contacts Warframe. */
export function useDropData() {
  const [data, setData] = useState(session.data);
  const [error, setError] = useState(session.readError || session.sourceError);
  const [loading, setLoading] = useState(!session.data && !error);
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    const readCache = async () => {
      if (!active) return;
      controller = new AbortController();
      let delay = DROP_CACHE_POLL_MS, waiting = false;
      try {
        const response = await fetch('/api/droptables', {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]), cache: 'no-store',
          headers: session.etag ? { 'If-None-Match': session.etag } : {},
        });
        if (!active) return;
        if (response.status === 304 && session.data) { session.readError = ''; setError(session.sourceError); return; }
        if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('The drop-table service is unavailable.');
        const result = await response.json();
        if (!active) return;
        if (response.status === 503 && result.refreshing === true) { waiting = !session.data; delay = 5_000; session.readError = ''; setError(session.sourceError); return; }
        if (!response.ok) throw new Error(result.error || 'Could not read the saved drop tables.');
        const sourceError = typeof result.refreshError === 'string' ? result.refreshError : '';
        if (!isDropDataset(result)) throw new Error('The drop-table response was not recognized.');
        session.etag = response.headers.get('etag');
        session.sourceError = sourceError;
        session.readError = ''; setError(session.sourceError);
        if (session.data?.fetchedAt !== result.fetchedAt) { session.data = result; setData(result); }
      } catch {
        if (active && !controller.signal.aborted && !session.data) {
          session.data = sampleDropDataset;
          setData(sampleDropDataset);
        }
      } finally {
        if (active) { setLoading(waiting); session.nextRead = Date.now() + delay; timer = setTimeout(() => void readCache(), delay); }
      }
    };
    // StrictMode replays mount effects; discard its first scheduled read.
    queueMicrotask(() => {
      if (!active) return;
      const remaining = session.nextRead - Date.now();
      if (remaining > 0) timer = setTimeout(() => void readCache(), remaining); else void readCache();
    });
    return () => { active = false; clearTimeout(timer); controller?.abort(); };
  }, []);
  return { data, error, loading };
}
