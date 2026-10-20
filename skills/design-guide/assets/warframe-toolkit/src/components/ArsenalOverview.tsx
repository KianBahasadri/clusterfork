import type { ReactNode } from 'react';
import artwork from '../data/arsenalArt.json';
import { catalog } from '../data/catalog';
import { frames } from '../lib/modder';
import { arsenalSlots } from '../lib/modderModel';
import type { ArsenalBuild, ArsenalSlot } from '../lib/modderModel';
import type { CatalogItem } from '../lib/model';
import { Dialog } from './Dialog';
import { GameIcon } from './ModderVisuals';
import type { GameIconName } from './ModderVisuals';

const items = new Map(catalog.map(item => [item.id, item]));
const portraits: Record<string, string> = artwork.portraits;
const abilities: Record<string, string> = artwork.abilities;

export function AbilityIcons({ item, onOpen }: { item?: CatalogItem; onOpen: () => void }) {
  const list = item && frames.get(item.id)?.abilities;
  if (!list?.length) return null;
  return <div className="arsenal-ability-icons" aria-label="Warframe abilities">{list.map(ability => <button type="button" key={ability.name} onClick={onOpen} aria-label={ability.name} title={`${ability.name}\n${ability.description}`}>
    {abilities[ability.name.toLowerCase()] ? <span aria-hidden="true" style={{ maskImage: `url(${abilities[ability.name.toLowerCase()]})` }} /> : <small>{ability.name}</small>}
  </button>)}</div>;
}

export function AbilitiesDialog({ item, onClose }: { item: CatalogItem; onClose: () => void }) {
  return <Dialog title={`${item.name} abilities`} onClose={onClose} className="modder-theme arsenal-abilities-dialog">
    {frames.get(item.id)?.abilities.map(ability => <div className="arsenal-ability-description" key={ability.name}>
      {abilities[ability.name.toLowerCase()] && <span aria-hidden="true" style={{ maskImage: `url(${abilities[ability.name.toLowerCase()]})` }} />}
      <div><h3>{ability.name}</h3><p>{ability.description}</p></div>
    </div>)}
    <p className="small muted">Ability descriptions are reference information. Ability-specific effects are not simulated.</p>
  </Dialog>;
}

export function ArsenalOverview({ build, slot, item, name, stats, actions, disabled, onSlot, onSwap, onUpgrade, onAbilities }: {
  build: ArsenalBuild; slot: ArsenalSlot; item?: CatalogItem; name: ReactNode; stats: ReactNode; actions: ReactNode; disabled: boolean;
  onSlot: (slot: ArsenalSlot) => void; onSwap: () => void; onUpgrade: () => void; onAbilities: () => void;
}) {
  const portrait = item && (portraits[item.name] ?? item.image);
  return <div className="arsenal-overview">
    <div className="arsenal-overview-grid">
      <aside className="arsenal-equipment-panel" aria-label="Arsenal equipment">
        <div className="arsenal-loadout-name">{name}<img src="images/arsenal/ornament.png" alt="" /></div>
        <div className="arsenal-categories" role="group" aria-label="Equipment categories">{arsenalSlots.map(key => <button type="button" key={key} aria-label={`Select ${key.toLowerCase()}`} title={key} aria-pressed={key === slot} onClick={() => onSlot(key)}><GameIcon name={key.toLowerCase() as GameIconName} size={42} /></button>)}</div>
        <div className="arsenal-equipment-list">{arsenalSlots.map(key => {
          const equipment = build.equipment[key], selectedItem = items.get(equipment.id), selected = key === slot;
          return <div key={key} className={`arsenal-equipment-row${selected ? ' is-current' : ''}`}>
            <button type="button" className="arsenal-equipment-select" aria-label={`Edit ${key.toLowerCase()}`} aria-pressed={selected} onPointerMove={event => { if (event.pointerType === 'mouse' && !selected) onSlot(key); }} onClick={() => onSlot(key)}>
              <span><strong>{selectedItem?.name ?? `Choose ${key.toLowerCase()}`}</strong><small>{key} · {equipment.mods.filter(mod => mod.id).length} / 8 mods</small></span>
              {selectedItem?.image && <img src={selectedItem.image} alt="" draggable={false} />}
            </button>
            {selected && <div className="arsenal-equipment-actions">
              <button type="button" disabled={disabled} onClick={onSwap}>Swap</button>
              <button type="button" disabled={!item} onClick={onUpgrade}>Upgrade</button>
              {key === 'Warframe' && !!frames.get(equipment.id)?.abilities.length && <button type="button" onClick={onAbilities}>Abilities</button>}
            </div>}
          </div>;
        })}</div>
      </aside>
      <div className={`arsenal-equipment-preview${slot !== 'Warframe' ? ' is-weapon' : ''}`}>
        {portrait && <img key={portrait} src={portrait} alt={item?.name ?? ''} draggable={false} />}
      </div>
      <aside className="arsenal-equipment-details" aria-label="Equipment details">
        <div className="arsenal-description"><h2>{item?.name ?? slot}</h2><p>{item?.description ?? 'Select equipment to start a build.'}</p></div>
        <div className="arsenal-overview-stats"><AbilityIcons item={item} onOpen={onAbilities} /><div className="modder-stat-panel">{stats}</div></div>
      </aside>
    </div>
    <div className="modder-game-footer arsenal-overview-footer">{actions}<a className="modder-game-button" href="#weapons">Exit</a></div>
  </div>;
}
