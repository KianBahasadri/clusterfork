import { relicNames } from '../data/relicCatalog';
import { MAX_RELIC_QUANTITY, refinements, relicKey } from '../lib/relics';
import type { Relic, Refinement } from '../lib/relics';
import type { RelicDetection } from '../lib/relicMatching';
import { relicImportError } from '../lib/relicMatching';
import { Select } from './Select';

const names = [{ value: '', label: 'Choose relic' }, ...relicNames.map(name => ({ value: name, label: name }))];

export function RelicScreenshotReview({ detections, existing, busy, onUpdate, onSelectAll, onImport }: {
  detections: RelicDetection[]; existing: Relic[]; busy: boolean;
  onUpdate: (id: string, patch: Partial<RelicDetection>) => void; onSelectAll: () => void; onImport: () => void;
}) {
  const selected = detections.filter(detection => detection.selected);
  const error = relicImportError(detections);
  return <section className="review-section" aria-labelledby="relic-review-title">
    <div className="section-heading"><h3 id="relic-review-title">Review relics</h3><div className="import-actions">
      <button type="button" className="button secondary" onClick={onSelectAll}>{detections.every(detection => detection.selected) ? 'Deselect all' : 'Select all'}</button>
      <button type="button" className="button primary" disabled={busy || !selected.length || Boolean(error)} onClick={onImport}>Import {selected.length} {selected.length === 1 ? 'stack' : 'stacks'}</button>
    </div></div>
    <p className="small muted">Replaces quantities for selected stacks. Notes and other relics stay unchanged. Confirm counts marked 1 when no count was read.</p>
    {error && <p className="callout caution" role="alert">{error}</p>}
    <div className="table-scroll" role="region" aria-label="Relic screenshot matches" tabIndex={0}><table className="review-table relic-review-table">
      <caption className="sr-only">Review relic names, refinements, and quantities before importing. Overlapping screenshots never add quantities together.</caption>
      <thead><tr><th scope="col">Import</th><th scope="col">Relic</th><th scope="col">Refinement</th><th scope="col">Quantity</th></tr></thead>
      <tbody>{detections.map(detection => {
        const label = `${detection.name || 'Unknown relic'} ${detection.refinement || 'unknown refinement'}`;
        const saved = detection.refinement && existing.find(item => relicKey(item) === relicKey({ name: detection.name, refinement: detection.refinement! }));
        return <tr key={detection.id}>
          <td><label className="table-checkbox"><input type="checkbox" checked={detection.selected} aria-label={`Import ${label}`} onChange={event => onUpdate(detection.id, { selected: event.target.checked })} /></label></td>
          <td className="match-cell"><Select label={`Relic name for ${detection.rawText}`} value={detection.name} options={names} onChange={name => onUpdate(detection.id, { name, refinement: name === 'Requiem Eterna' ? 'Unrefined' : detection.refinement === 'Unrefined' ? null : detection.refinement })} /><small>Read: {detection.rawText}</small>{!detection.reviewed && detection.issues.map(issue => <span className="badge caution-badge" key={issue}>{issue}</span>)}</td>
          <td><Select label={`Refinement: ${label}`} value={detection.refinement ?? ''} options={[{ value: '', label: 'Choose refinement' }, ...refinements.filter(value => detection.name === 'Requiem Eterna' ? value === 'Unrefined' : value !== 'Unrefined').map(value => ({ value, label: value }))]} onChange={value => onUpdate(detection.id, { refinement: value ? value as Refinement : null })} /></td>
          <td><input className="rank-input" type="number" min={0} max={MAX_RELIC_QUANTITY} step={1} value={detection.quantity ?? ''} aria-label={`Quantity: ${label}`} onChange={event => onUpdate(detection.id, { quantity: event.target.value === '' ? null : Number(event.target.value) })} />{saved && <small className="muted">Saved: {saved.quantity}</small>}</td>
        </tr>;
      })}</tbody>
    </table></div>
  </section>;
}
