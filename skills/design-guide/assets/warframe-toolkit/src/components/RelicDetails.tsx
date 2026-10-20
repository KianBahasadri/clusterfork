import { relicCatalogDate } from '../data/relicCatalog';
import type { RelicCatalogItem } from '../lib/relicCatalog';
import { MarketPrice } from './MarketPrice';
import { DROP_TABLES_URL } from '../lib/dropTables';

const chance = (value: number | null) => value === null ? 'Not published' : `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;

export function RelicDetails({ item }: { item: RelicCatalogItem }) {
  const total = item.rewards.every(reward => reward.chance !== null) ? item.rewards.reduce((sum, reward) => sum + reward.chance!, 0) : null;
  return <div className="weapon-details relic-details">
    <section aria-label="Relic rewards"><h3>Rewards · {item.refinement}</h3>
      <ul className="relic-rewards">{item.rewards.map((reward, index) => <li key={`${reward.id}:${index}`}><div>{reward.quantity > 1 && `${reward.quantity} × `}{reward.name}<small className={`relic-rarity rarity-${reward.rarity}`}>{reward.rarity}</small></div><span><MarketPrice subject={{ name: reward.name, kind: 'item' }} /></span><span>{chance(reward.chance)}</span></li>)}</ul>
      {total !== null && Math.abs(total - 100) > 0.05 && <p className="small muted">Published chances total {chance(total)}; the official table is incomplete.</p>}
      {item.refinement === 'Unrefined' && <p className="small muted">Cannot be refined.</p>}
    </section>
    <section aria-label="Relic sources"><h3>Sources</h3>{item.sources.length ? <ul className="relic-sources">{item.sources.map((source, index) => <li key={index}><div><a href={`${DROP_TABLES_URL}#${source.anchor}`} target="_blank" rel="noreferrer">{source.source}</a>{(source.rotation || source.stage) && <small>{[source.rotation && `Rotation ${source.rotation}`, source.stage].filter(Boolean).join(' · ')}</small>}</div><span>{chance(source.chance)}</span></li>)}</ul> : <p className="small muted">No sources published for this refinement.</p>}</section>
    <p className="small muted"><a href="https://content.warframe.com/PublicExport/index_en.txt.lzma" target="_blank" rel="noreferrer">Official catalog</a> · <a href={DROP_TABLES_URL} target="_blank" rel="noreferrer">Drop tables</a> · {relicCatalogDate}</p>
  </div>;
}
