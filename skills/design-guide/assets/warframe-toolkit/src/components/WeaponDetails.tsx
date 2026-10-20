import { catalogDate } from '../data/catalog';
import type { CatalogItem, Recipe } from '../lib/model';
import { maximumRank } from '../lib/model';
import { Currency } from './Currency';

const number = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 3 });
const label = (key: string) => key.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ').replace(/^./, char => char.toUpperCase());
const statLabels: Record<string, string> = {
  procChance: 'Status chance', criticalChance: 'Critical chance', criticalMultiplier: 'Critical multiplier',
  fireRate: 'Attack / fire rate', magazineSize: 'Magazine', reloadTime: 'Reload time', omegaAttenuation: 'Riven multiplier', disposition: 'Riven disposition (1–5)',
  crit_chance: 'Critical chance', crit_mult: 'Critical multiplier', status_chance: 'Status chance', speed: 'Attack / fire rate', shot_type: 'Shot type', shot_speed: 'Projectile speed', flight: 'Flight speed',
};
function statValue(key: string, value: number | string): string {
  if (typeof value !== 'number') return value;
  if (['criticalChance', 'procChance'].includes(key)) return `${number(value * 100)}%`;
  if (['crit_chance', 'status_chance'].includes(key)) return `${number(value)}%`;
  if (['criticalMultiplier', 'crit_mult'].includes(key)) return `${number(value)}×`;
  if (['reloadTime', 'comboDuration', 'windUp', 'charge_time'].includes(key)) return `${number(value)} s`;
  if (['range', 'slamRadius', 'heavySlamRadius'].includes(key)) return `${number(value)} m`;
  if (['fireRate', 'speed'].includes(key)) return `${number(value)} /s`;
  return number(value);
}
function Stats({ stats }: { stats: Record<string, number | string> }) {
  return <dl className="weapon-stats">{Object.entries(stats).map(([key, value]) => <div key={key}><dt>{statLabels[key] || label(key)}</dt><dd>{statValue(key, value)}</dd></div>)}</dl>;
}
function duration(seconds: number) {
  if (!seconds) return 'Instant';
  const hours = Math.floor(seconds / 3600); const minutes = Math.floor(seconds % 3600 / 60);
  return [hours ? `${hours} h` : '', minutes ? `${minutes} min` : '', !hours && !minutes ? `${seconds} s` : ''].filter(Boolean).join(' ');
}
export function RecipeDetails({ recipe }: { recipe: Recipe }) {
  return <div className="recipe-details">
    <dl className="recipe-cost"><div><dt>Credits</dt><dd>{recipe.credits === null ? 'Unknown' : <Currency value={recipe.credits} currency="credits" />}</dd></div><div><dt>Build time</dt><dd>{recipe.seconds === null ? 'Unknown' : duration(recipe.seconds)}</dd></div><div><dt>Produces</dt><dd>{recipe.quantity}</dd></div></dl>
    <ul className="recipe-materials">{recipe.ingredients.map(part => <li key={part.id}>
      <div><span>{part.name}{part.equipment && <small>Consumes weapon</small>}</span><strong>× {number(part.count)}</strong></div>
      {part.recipes?.map(subrecipe => <details key={subrecipe.id}><summary>Craft {part.name}</summary><RecipeDetails recipe={subrecipe} /></details>)}
    </li>)}</ul>
  </div>;
}

export function WeaponDetails({ item }: { item: CatalogItem }) {
  const incarnon = item.incarnon;
  const market = item.market;
  const warframe = item.slot === 'Warframe';
  const kind = warframe ? item.weaponType === 'Necramech' ? 'Necramech' : 'Warframe' : 'Weapon';
  return <div className="weapon-details">
    <section aria-label="Crafting"><h3>Crafting</h3>{item.modular && <p className="muted small">{item.modular}</p>}
      {item.recipes?.length ? item.recipes.map(recipe => <div className="weapon-recipe" key={recipe.id}><h4>{recipe.name}</h4><RecipeDetails recipe={recipe} /></div>) : <p className="muted small">No Foundry recipe recorded.</p>}
      {!warframe && !!item.craftingTargets?.length && <div className="crafting-targets"><h4>Used to craft</h4><ul>{item.craftingTargets.map(target => <li key={target.id}>{target.name} <span className="muted">— consumes {target.count} × {item.name}</span></li>)}</ul></div>}
    </section>
    {!warframe && <section aria-label="Incarnon details"><h3>Incarnon</h3>{incarnon && incarnon.type !== 'none' ? <>
      <p><strong>{incarnon.name}</strong></p><p className="small muted">{incarnon.description}</p>
      {incarnon.installation && <><h4>Adapter installation</h4><RecipeDetails recipe={incarnon.installation} /></>}
      <a className="detail-link" href={incarnon.wikiUrl} target="_blank" rel="noreferrer">Evolution challenges & perks ↗</a>
    </> : <p className="muted small">No Incarnon form or compatible Genesis adapter recorded.</p>}</section>}
    <section aria-label="Acquisition"><h3>Acquisition & quests</h3>
      {!!item.quests?.length && <ul className="quest-rewards">{item.quests.map(quest => <li key={`${quest.name}-${quest.kind}`}><a href={quest.url} target="_blank" rel="noreferrer">{quest.name}</a><span className="muted">{quest.kind === 'Weapon reward' ? `${kind} reward` : quest.kind}</span></li>)}</ul>}
      {market && <dl className="weapon-stats">{market.blueprintCredits !== null && <div><dt>Blueprint cost</dt><dd><Currency value={market.blueprintCredits} currency="credits" /></dd></div>}{market.credits !== null && <div><dt>Market {warframe ? kind : 'weapon'}</dt><dd><Currency value={market.credits} currency="credits" /></dd></div>}{market.platinum !== null && <div><dt>Market {warframe ? kind : 'weapon'}</dt><dd><Currency value={market.platinum} currency="platinum" /></dd></div>}</dl>}
      {!!item.tags?.length && <div className="weapon-tags">{item.tags.map(tag => <span className="badge" key={tag}>{tag}</span>)}</div>}
      {item.acquisition?.length ? <details className="drop-locations"><summary>Drop locations ({item.acquisition.length})</summary><ul>{item.acquisition.map(drop => <li key={drop}>{drop}</li>)}</ul></details> : <p className="muted small">Drop locations are not listed in this source. See the {warframe ? kind : 'weapon'} wiki for vendor and event availability.</p>}
    </section>
    {(!warframe || Object.keys(item.stats || {}).length > 0) && <section aria-label={`${kind} stats`}><h3>Unmodded stats</h3>
      {Object.keys(item.stats || {}).length ? <Stats stats={item.stats!} /> : <p className="muted small">{item.modular ? 'Stats depend on assembled parts.' : 'Stats unavailable in this source.'}</p>}
      {!!Object.keys(item.damage || {}).length && <><h4>Damage</h4><Stats stats={item.damage!} /></>}
      {item.attacks?.map((attack, index) => <details className="attack-details" key={`${attack.name}-${index}`}><summary>{attack.name}</summary><Stats stats={attack.stats} /><h4>Damage</h4><Stats stats={attack.damage} /></details>)}
    </section>}
    <section aria-label={`Other ${warframe ? kind : 'weapon'} data`}><dl className="weapon-stats">
      <div><dt>Maximum rank</dt><dd>{maximumRank(item) ?? 'Unknown'}</dd></div>
      <div><dt>Mastery eligible</dt><dd>{item.masterable ? 'Yes' : 'No'}</dd></div>
      <div><dt>Built item tradable</dt><dd>{item.tradable ? 'Yes' : 'No'}</dd></div>
      <div><dt>Prime counterpart</dt><dd>{item.hasPrimeVariant ? 'Available' : item.variant === 'Prime' ? `Prime ${warframe ? kind : 'weapon'}` : 'None recorded'}</dd></div>
      {item.vaulted !== null && item.vaulted !== undefined && <div><dt>Vaulted</dt><dd>{item.vaulted ? 'Yes' : 'No'}</dd></div>}
      {item.releaseDate && <div><dt>Released</dt><dd>{item.releaseDate}</dd></div>}
      {!!item.polarities?.length && <div><dt>Polarities</dt><dd>{item.polarities.join(', ')}</dd></div>}
    </dl></section>
    <div className="record-meta"><span>Catalog: {catalogDate.slice(0, 10)}</span><a href={item.wikiUrl} target="_blank" rel="noreferrer">{kind} wiki ↗</a>{item.sourceUrls?.slice(0, 2).map((url, index) => <a key={url} href={url} target="_blank" rel="noreferrer">{index ? 'Game export' : 'Item data'} ↗</a>)}</div>
  </div>;
}
