import { useDeferredValue, useState } from 'react';
import type { DragEvent } from 'react';
import { catalog } from '../data/catalog';
import type { CatalogItem } from '../lib/model';
import { arcanes, compatible, modFamily, mods, upgradeEffects, upgrades } from '../lib/modder';
import type { Upgrade, UpgradeKind } from '../lib/modder';
import { emptyUpgrade, polarities } from '../lib/modderModel';
import type { ArsenalSlot, EquipmentBuild, ModderConditions } from '../lib/modderModel';
import { Dialog } from './Dialog';
import { Select } from './Select';
import { GameIcon, PolarityIcon, rankText, UpgradeFace } from './ModderVisuals';

export type PickerTarget = { kind: UpgradeKind | 'equipment'; index: number };
export const targetLabel = (target: PickerTarget, slot: ArsenalSlot) => target.kind === 'mod' ? `Mod ${target.index + 1}` : target.kind === 'arcane' ? `Arcane ${target.index + 1}` : target.kind === 'special' ? slot === 'Warframe' ? 'Aura' : 'Stance' : target.kind === 'exilus' ? 'Exilus' : slot;
export function ModderLibrary({ target, item, slot, equipment, conditions, onChoose, onClose, onDragUpgrade, onDragEnd, inline = false, disabled = false }: { target: PickerTarget; item?: CatalogItem; slot: ArsenalSlot; equipment: EquipmentBuild; conditions: ModderConditions; onChoose: (id: string) => void; onClose?: () => void; onDragUpgrade?: (event: DragEvent<HTMLButtonElement>, id: string) => void; onDragEnd?: () => void; inline?: boolean; disabled?: boolean }) {
  const [query, setQuery] = useState(''), [calculated, setCalculated] = useState(false), [limit, setLimit] = useState(40), [polarity, setPolarity] = useState('all'), [sort, setSort] = useState('drain');
  const terms = useDeferredValue(query.toLowerCase().trim()).split(/\s+/);
  const isEquipment = target.kind === 'equipment', arcane = target.kind === 'arcane';
  const installed = target.kind === 'mod' ? equipment.mods[target.index] : arcane ? equipment.arcanes[target.index] : target.kind === 'exilus' ? equipment.exilus : equipment.special;
  const occupied = [...equipment.mods, equipment.exilus, equipment.special].filter(mod => mod !== installed).flatMap(mod => { const definition = upgrades.get(mod.id); return definition ? [modFamily(definition)] : []; });
  const candidates = isEquipment ? catalog.filter(entry => entry.slot === slot && (slot !== 'Warframe' || entry.weaponType === 'Warframe')) : (arcane ? arcanes : mods).filter(mod => item && compatible(mod, item, target.kind as UpgradeKind));
  const matches = candidates.filter(entry => {
    const mod = isEquipment ? undefined : entry as Upgrade;
    if (!terms.every(term => `${entry.name} ${mod?.ranks.at(-1)?.join(' ') ?? ''}`.toLowerCase().includes(term))) return false;
    if (mod && !arcane && polarity !== 'all' && mod.polarity !== polarity) return false;
    return !calculated || !mod || upgradeEffects({ ...emptyUpgrade(), id: mod.id, rank: mod.maxRank }, conditions).omitted.length === 0;
  }).sort((a, b) => sort === 'drain' && !isEquipment ? Math.abs((b as Upgrade).drain) + (b as Upgrade).maxRank - Math.abs((a as Upgrade).drain) - (a as Upgrade).maxRank || a.name.localeCompare(b.name) : a.name.localeCompare(b.name));
  const title = isEquipment ? `Choose ${slot.toLowerCase()}` : arcane ? 'Choose arcane' : target.kind === 'special' ? `Choose ${slot === 'Warframe' ? 'aura' : 'stance'}` : 'Choose mod';
  const coverageFilter = <label><input type="checkbox" checked={calculated} onChange={event => { setCalculated(event.target.checked); setLimit(40); }} />Fully calculated only</label>;
  const content = <>
    <div className="modder-library-filters">
      <label className="search-field"><GameIcon name="search" size={24} /><input type="search" aria-label={inline ? 'Search compatible upgrades' : isEquipment ? 'Search equipment' : 'Search mods and effects'} placeholder="Search…" value={query} onChange={event => { setQuery(event.target.value); setLimit(40); }} autoFocus={!inline} /></label>
      {!isEquipment && !arcane && <div className="modder-polarity-filters" role="group" aria-label="Filter by polarity">
        <button type="button" aria-label="All polarities" title="All polarities" aria-pressed={polarity === 'all'} onClick={() => { setPolarity('all'); setLimit(40); }}><GameIcon name="all" size={28} /><span>All</span></button>
        {polarities.filter(value => value && value !== 'universal').map(value => <button type="button" key={value} aria-label={`${value.charAt(0).toUpperCase() + value.slice(1)} polarity`} title={`${value.charAt(0).toUpperCase() + value.slice(1)} polarity`} aria-pressed={polarity === value} onClick={() => { setPolarity(value); setLimit(40); }}><PolarityIcon polarity={value} /></button>)}
      </div>}
      {!isEquipment && <Select label="Sort upgrades" value={sort} options={[{ value: 'name', label: 'Name' }, { value: 'drain', label: 'Drain' }]} onChange={setSort} />}
    </div>
    {!inline && <div className="modder-library-tools"><span className="small muted" role="status">{matches.length} results</span>{!isEquipment && coverageFilter}</div>}
    <div className={`modder-library-list${isEquipment ? ' is-equipment' : ''}`} tabIndex={inline ? 0 : undefined}>{matches.slice(0, limit).map(entry => {
      const mod = isEquipment ? undefined : entry as Upgrade;
      const effects = mod ? upgradeEffects({ ...emptyUpgrade(), id: mod.id, rank: mod.maxRank }, conditions) : undefined;
      const conflict = mod && (arcane ? equipment.arcanes.some(arc => arc !== installed && arc.id === mod.id) : occupied.includes(modFamily(mod)));
      return <button type="button" key={entry.id} className="modder-library-result" aria-label={`${inline ? 'Install' : 'Equip'} ${entry.name}`} title={mod ? `${entry.name}\n${rankText(mod, mod.maxRank)}${conflict ? '\nConflicts with an equipped upgrade' : effects!.omitted.length ? '\n' + (effects!.effects.length ? 'Partly calculated' : 'Reference only') : ''}` : entry.name} disabled={disabled || !inline && Boolean(conflict)} aria-disabled={Boolean(conflict) || undefined} draggable={inline && !disabled && Boolean(mod && onDragUpgrade)} onDragStart={event => onDragUpgrade?.(event, entry.id)} onDragEnd={onDragEnd} onClick={() => { if (!conflict) onChoose(entry.id); }}>
        {mod ? <><UpgradeFace mod={mod} rank={mod.maxRank} drain={Math.abs(mod.drain) + mod.maxRank} polarity={mod.polarity} label={mod.rarity ?? ''} arcane={arcane} /><span className="modder-library-description">{rankText(mod, mod.maxRank)}</span><span className="modder-library-coverage">{conflict ? 'Already equipped / conflicts' : effects!.omitted.length ? effects!.effects.length ? 'Partly calculated' : 'Reference only' : 'Calculated'}</span></> : <>{(entry as CatalogItem).image && <img src={(entry as CatalogItem).image} alt="" loading="lazy" />}<span><strong>{entry.name}</strong><small>{(entry as CatalogItem).weaponType} · {(entry as CatalogItem).variant}</small></span></>}
      </button>;
    })}{inline && matches.length > limit && <button type="button" className="modder-library-more" onClick={() => setLimit(limit + 40)}><GameIcon name="add" size={32} />Show more</button>}</div>
    {inline && <div className="modder-library-heading"><h2>{arcane ? 'Arcanes' : 'Mods'}</h2><span>Equip to <strong>{targetLabel(target, slot)}</strong></span><span className="modder-drag-hint">Drag to equip · Right-click to remove</span>{coverageFilter}<span role="status">{matches.length} compatible</span></div>}
    {!matches.length && <p className="small muted">No compatible results. Try another name or clear the filters.</p>}
    {!inline && matches.length > limit && <button type="button" className="button secondary" onClick={() => setLimit(limit + 40)}>Show more</button>}
  </>;
  return inline ? <section className="modder-library-dock" aria-label="Compatible upgrades">{content}</section> : <Dialog title={title} onClose={onClose!} className="modder-library modder-theme" onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose?.(); } }}>{content}</Dialog>;
}
