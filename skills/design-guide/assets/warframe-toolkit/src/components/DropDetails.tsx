import { useState } from 'react';
import { ExternalLink, Search } from 'lucide-react';
import { Dialog } from './Dialog';
import { MarketPrice } from './MarketPrice';
import { attemptsForChance, DROP_TABLES_URL, formatChance, successChance } from '../lib/dropTables';
import type { DropRow } from '../lib/dropTables';

export function DropDetails({ row, onClose, onFindReward, onFindSource }: { row: DropRow; onClose: () => void; onFindReward: () => void; onFindSource: () => void }) {
  const [attempts, setAttempts] = useState('20'), [target, setTarget] = useState('90');
  const validAttempts = /^\d+$/.test(attempts) && Number(attempts) <= 1_000_000;
  const validTarget = target !== '' && Number(target) > 0 && Number(target) <= 100;
  const required = row.chance !== null && validTarget ? attemptsForChance(row.chance, Number(target)) : null;
  return <Dialog title={row.reward} onClose={onClose} className="drop-details">
    <dl className="drop-facts">
      <div><dt>WFM</dt><dd><MarketPrice subject={{ name: row.reward, kind: 'item' }} /></dd></div>
      <div><dt>Source</dt><dd>{row.source}</dd></div>
      <div><dt>Category</dt><dd>{row.category}</dd></div>
      {row.location && <div><dt>Location</dt><dd>{row.location}</dd></div>}
      {row.mission && <div><dt>Mission type</dt><dd>{row.mission}</dd></div>}
      {row.rotation && <div><dt>Rotation</dt><dd>{row.rotation}</dd></div>}
      {row.stage && <div><dt>Stage</dt><dd>{row.stage}</dd></div>}
      {row.refinement && <div><dt>Refinement</dt><dd>{row.refinement}</dd></div>}
      {row.era && <div><dt>Relic era</dt><dd>{row.era}</dd></div>}
      <div><dt>Official rarity</dt><dd>{row.rarity}</dd></div>
      <div><dt>Within table</dt><dd className="drop-numeric">{formatChance(row.tableChance)}</dd></div>
      <div><dt>Table roll rate</dt><dd className="drop-numeric">{row.sourceChance === null ? 'Not separately specified' : formatChance(row.sourceChance)}</dd></div>
      <div><dt>Drop chance</dt><dd className="drop-numeric">{row.chance === null ? 'Multiple rolls — not calculated' : formatChance(row.chance)}</dd></div>
    </dl>
    {row.sourceChance !== null && row.sourceChance <= 100 && <p className="muted small">Drop chance = {formatChance(row.sourceChance)} table roll rate × {formatChance(row.tableChance)} within that table. Rarity is the publisher’s label for the within-table rate.</p>}
    {row.chance === null ? <p className="callout info">This source reports a roll rate above 100%. Its number and independence of rolls are not specified here, so no per-attempt odds are inferred.</p> : <section className="drop-calculator" aria-label="Odds calculator">
      <h3>Odds calculator</h3>
      <p className="muted small">One attempt means one roll at this source, rotation, and stage. Assumes independent rolls at the published rate; excludes boosters, squads, and pity systems. A listed reward is not proof its source is currently accessible.</p>
      <div className="drop-calculator-fields">
        <label>Attempts<input type="number" min="0" max="1000000" step="1" value={attempts} aria-invalid={!validAttempts || undefined} aria-describedby={!validAttempts ? 'drop-attempts-error' : undefined} onChange={event => setAttempts(event.target.value)} /></label>
        <label>Target chance (%)<input type="number" min="0.01" max="100" step="any" value={target} aria-invalid={!validTarget || undefined} aria-describedby={!validTarget ? 'drop-target-error' : undefined} onChange={event => setTarget(event.target.value)} /></label>
      </div>
      {!validAttempts && <p className="error-text small" id="drop-attempts-error">Enter a whole number from 0 to 1,000,000</p>}
      {!validTarget && <p className="error-text small" id="drop-target-error">Enter a target greater than 0 and at most 100</p>}
      <div className="drop-odds" aria-live="polite"><div><span>Chance of at least one</span><output>{validAttempts ? row.chance < 100 && successChance(row.chance, Number(attempts)) >= 99.99995 ? '>99.9999%' : formatChance(successChance(row.chance, Number(attempts))) : '—'}</output></div><div><span>Attempts to target</span><output>{!validTarget ? '—' : required === null ? 'No finite guarantee' : required.toLocaleString()}</output></div></div>
      <div className="drop-milestones" aria-label="Chance milestones">{[50, 90, 95, 99].map(target => { const value = attemptsForChance(row.chance!, target); return <button key={target} type="button" className="button quiet" disabled={value === null || value > 1_000_000} onClick={() => { setTarget(String(target)); setAttempts(String(value)); }}><span>{target}%</span><span>{value === null ? '—' : `${value.toLocaleString()} attempts`}</span></button>; })}</div>
    </section>}
    <div className="drop-detail-actions"><button type="button" className="button secondary" onClick={onFindReward}><Search size={15} />All sources for this reward</button><button type="button" className="button secondary" onClick={onFindSource}>All rewards from this source</button><a className="button quiet" href={`${DROP_TABLES_URL}#${encodeURIComponent(row.anchor)}`} target="_blank" rel="noreferrer"><ExternalLink size={15} />Official table</a></div>
  </Dialog>;
}
