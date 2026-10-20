import { useMemo, useState } from 'react';
import { ExternalLink, MapPin } from 'lucide-react';
import { Dialog } from './Dialog';
import { CraftingArtwork } from './CraftingArtwork';
import { Currency } from './Currency';
import { craftingMaterials } from '../data/crafting';
import { miningInfo, MINING_GUIDE_URL } from '../data/mining';
import { craftingDuration, craftingSources } from '../lib/crafting';
import type { CraftingItem, CraftingNode } from '../lib/crafting';
import { useDropData } from '../lib/useDropData';
import { defaultDropView, DROP_TABLES_URL, dropViewHash, formatChance } from '../lib/dropTables';

const number = (value: number) => value.toLocaleString();

export function CraftingDetails({ item, node, onRecipe, onClose }: { item: CraftingItem; node?: CraftingNode; onRecipe?: (choice: string) => void; onClose: () => void }) {
  const { data, loading, error } = useDropData();
  const [limit, setLimit] = useState(12);
  const recipe = node?.recipe ?? item.recipes[0];
  const blueprint = craftingMaterials.find(item => item.id === recipe?.id);
  const sources = useMemo(() => craftingSources(data?.rows ?? [], item.name, recipe?.name), [data, item.name, recipe?.name]);
  const blueprintCredits = blueprint?.blueprintCredits ?? item.blueprintCredits;
  const description = item.description.replace(/\s*Location: .+$/i, '');
  const mining = miningInfo(item.name);
  const orbVallisFish = item.id.startsWith('/Lotus/Types/Items/Fish/Solaris/') && (item.id.includes('/FishParts/') || /Fish[^/]*Item$/.test(item.id));
  const guides = [
    ...(mining ? [{ title: 'Mining guide', url: MINING_GUIDE_URL, author: 'WARFRAME Wiki' }] : []),
    ...(orbVallisFish ? [{ title: 'Fortuna / Orb Vallis Fishing Guide', url: 'https://steamcommunity.com/sharedfiles/filedetails/?id=1562024912', author: 'Revalopod · Steam Community' }] : []),
  ];
  const openDrops = () => {
    const view = defaultDropView();
    view.filters.reward = { kind: 'values', values: [...new Set(sources.length ? sources.map(row => row.reward) : [item.name, ...(recipe ? [recipe.name] : [])])] };
    view.browse = { mode: 'item' };
    onClose(); location.hash = dropViewHash(view);
  };
  return <Dialog title={item.name} onClose={onClose} className="craft-detail">
    <div className="craft-detail-intro"><CraftingArtwork item={item} /><div>{description && <p className="small muted">{description}</p>}{item.location && <p className="craft-location"><MapPin size={16} /><span>{item.location}</span></p>}{node && <span className="badge">{number(node.quantity)} needed</span>}</div></div>
    {mining && <section className="craft-detail-section craft-mining" aria-label="Mining info">
      <h3>Mining</h3>
      <dl className="craft-facts">
        <div><dt>Rarity</dt><dd>{mining.rarity} {mining.kind.toLowerCase()}</dd></div>
        <div><dt>Suggested cutter</dt><dd><a href={mining.cutterUrl} target="_blank" rel="noreferrer">{mining.cutter} <ExternalLink size={12} /></a></dd></div>
      </dl>
      <p className="small muted">{mining.advice}</p>
    </section>}
    {recipe && <section className="craft-detail-section" aria-label="Crafting info"><h3>{recipe.name}</h3><dl className="craft-facts">
      <div><dt>Build time</dt><dd>{craftingDuration(recipe.seconds)}</dd></div>
      <div><dt>Build cost</dt><dd>{recipe.credits === null ? 'Unknown' : <Currency value={recipe.credits} currency="credits" />}</dd></div>
      <div><dt>Produces</dt><dd>{number(recipe.quantity)} per build</dd></div>
      {node && node.batches > 1 && <div><dt>Builds needed</dt><dd>{number(node.batches)} ({number(node.batches * recipe.quantity)} produced)</dd></div>}
      {blueprintCredits !== null && <div><dt>Blueprint cost</dt><dd><Currency value={blueprintCredits} currency="credits" /></dd></div>}
    </dl>{node && node.key !== 'root' && onRecipe && <button type="button" className="button quiet craft-recipe-action" onClick={() => onRecipe(node.recipe ? 'gather' : recipe.id)}>{node.recipe ? 'Gather instead' : 'Use this recipe'}</button>}</section>}
    {!!item.catalogItem?.quests?.length && <section className="craft-detail-section"><h3>Quests</h3><ul className="craft-quests">{item.catalogItem.quests.map(quest => <li key={`${quest.name}-${quest.kind}`}><a href={quest.url} target="_blank" rel="noreferrer">{quest.name} ↗</a><span className="muted small">{quest.kind}</span></li>)}</ul></section>}
    {item.catalogItem?.market && (item.catalogItem.market.credits !== null || item.catalogItem.market.platinum !== null) && <section className="craft-detail-section"><h3>In-game Market</h3><dl className="craft-facts">{item.catalogItem.market.credits !== null && <div><dt>Built item</dt><dd><Currency value={item.catalogItem.market.credits} currency="credits" /></dd></div>}{item.catalogItem.market.platinum !== null && <div><dt>Built item</dt><dd><Currency value={item.catalogItem.market.platinum} currency="platinum" /></dd></div>}</dl></section>}
    {item.modular && <p className="small muted">{item.modular}</p>}
    <section className="craft-detail-section" aria-label="Sources"><div className="craft-detail-heading"><h3>Sources</h3><a href={DROP_TABLES_URL} target="_blank" rel="noreferrer">Official drop tables <ExternalLink size={12} /></a></div>
      {loading && <p className="small muted" role="status">Loading sources…</p>}
      {error && <p className="small error-text" role="status">{data ? 'Showing the last available drop tables. ' : ''}{error}</p>}
      {!loading && !sources.length && <p className="small muted">{data ? 'No matching drop-table rewards. See the wiki for vendors, research, and world resources.' : 'Drop tables are unavailable. Locations and wiki links are still available.'}</p>}
      {!!sources.length && <ul className="craft-source-list">{sources.slice(0, limit).map(row => <li key={row.id}><div><strong>{row.source}</strong><span>{row.reward}{row.rotation ? ` · Rotation ${row.rotation}` : ''}{row.stage ? ` · ${row.stage}` : ''}{row.refinement && !row.source.includes(row.refinement) ? ` · ${row.refinement}` : ''}</span></div><span className="craft-source-chance" title={row.chance === null ? 'Multiple rolls; no single per-attempt rate is published.' : 'Chance per opportunity'}>{row.chance === null ? 'Multiple rolls' : formatChance(row.chance)}</span></li>)}</ul>}
      {sources.length > limit && <button type="button" className="button quiet" onClick={() => setLimit(value => value + 24)}>Show more sources ({sources.length - limit})</button>}
      {data && <p className="craft-source-date">Drop snapshot: <time dateTime={data.fetchedAt}>{new Date(data.fetchedAt).toLocaleString()}</time></p>}
    </section>
    <div className="craft-detail-links"><a className="button secondary" href={item.wikiUrl} target="_blank" rel="noreferrer">Item wiki <ExternalLink size={14} /></a><button type="button" className="button secondary" onClick={openDrops}>Open Drop Tables <ExternalLink size={14} /></button></div>
    {!!guides.length && <footer className="craft-detail-footnotes"><ol aria-label="Community guides">{guides.map(guide => <li key={guide.url}><a href={guide.url} target="_blank" rel="noreferrer">{guide.title} <ExternalLink size={11} aria-hidden="true" /></a><span> — {guide.author}</span></li>)}</ol></footer>}
  </Dialog>;
}
