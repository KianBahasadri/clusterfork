import { ExternalLink } from 'lucide-react';
import { Dialog } from './Dialog';
import { DROP_TABLES_URL } from '../lib/dropTables';
import type { DropDataset } from '../lib/dropTables';

export function DropInfo({ data, onClose }: { data: DropDataset | null; onClose: () => void }) {
  return <Dialog title="About Drop Tables" onClose={onClose} className="drop-info">
    <p>These are Warframe’s official PC drop tables: the rewards that can drop, where they come from, and their published chances.</p>
    <dl className="drop-info-facts">
      <div><dt>Source</dt><dd><a href={DROP_TABLES_URL} target="_blank" rel="noreferrer">Warframe PC drops <ExternalLink size={12} /></a></dd></div>
      <div><dt>Published</dt><dd>{data?.updated || 'Date unavailable'}</dd></div>
      <div><dt>Last fetched</dt><dd>{data ? <time dateTime={data.fetchedAt} title={new Date(data.fetchedAt).toLocaleString([], { timeZoneName: 'long' })}>{new Date(data.fetchedAt).toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })}</time> : 'Not yet'}</dd></div>
    </dl>
    <h3>Browsing</h3>
    <p>Choose how to browse: mission type, item, location, relic, bounty, or source. Select a result to see its drops, then narrow the list with search and filters. Open a reward to see its drop chances and use the odds calculator.</p>
    <h3>Drop chances</h3>
    <p>Rotations, bounty stages, and relic refinements are separate entries. Chances apply to the listed opportunity; repeated rewards are not added together. A listed source may be vaulted or currently unavailable.</p>
    <h3>Updates</h3>
    <p>Data updates automatically in the background. Opening this page uses the latest saved results.</p>
  </Dialog>;
}
