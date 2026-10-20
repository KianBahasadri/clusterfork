import { useState } from 'react';
import { Search } from 'lucide-react';
import { Dialog } from './Dialog';
import { Select } from './Select';
import { WeaponDetails } from './WeaponDetails';
import { catalog } from '../data/catalog';
import { createEquipment, maximumRank, normalizeName, slots, variants } from '../lib/model';
import { catalogFields, withCatalogFacts } from '../lib/weaponCatalog';
import { canSubsume } from '../lib/recommendations';
import type { BooleanField } from '../lib/weaponCatalog';
import type { Equipment, Slot, Variant, Upgrade, Override } from '../lib/model';

export function EquipmentForm({ item, existing, onSave, onClose, onDelete, weapons = false }: { item?: Equipment; existing: Equipment[]; onSave: (item: Equipment) => boolean; onClose: () => void; onDelete: (id: string) => boolean; weapons?: boolean }) {
  const noun = weapons ? 'weapon' : 'Warframe';
  const [draft, setDraft] = useState<Equipment>(() => item ? { ...item } : createEquipment({ name: '', slot: weapons ? 'Primary' : 'Warframe', variant: 'Base' }));
  const [query, setQuery] = useState('');
  const [catalogVisible, setCatalogVisible] = useState(!item);
  const [error, setError] = useState('');
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [view, setView] = useState<'details' | 'inventory'>(item && catalog.some(entry => normalizeName(entry.name) === normalizeName(item.name)) ? 'details' : 'inventory');
  const namedEntry = catalog.find(entry => normalizeName(entry.name) === normalizeName(draft.name));
  const entry = namedEntry && (weapons ? namedEntry.slot !== 'Warframe' : namedEntry.slot === 'Warframe') ? namedEntry : undefined;
  const facts = withCatalogFacts(draft, entry);
  const tracked = existing.some(saved => saved.id === draft.id);
  const update = <K extends keyof Equipment>(key: K, value: Equipment[K]) => setDraft(previous => ({ ...previous, [key]: value }));
  const matches = catalog.filter(entry => (weapons ? entry.slot !== 'Warframe' : entry.slot === 'Warframe') && entry.name.toLowerCase().includes(query.toLowerCase())).slice(0, 7);
  const isWarframe = !weapons;
  const kind = entry?.weaponType === 'Necramech' ? 'Necramech' : isWarframe ? 'Warframe' : 'Weapon';
  const fields: [BooleanField, string, string][] = [
    ['owned', 'Owned', 'Currently in your inventory'], ['mastered', 'Mastered', `All mastery earned for this ${isWarframe ? kind : noun}`],
    ['dontKeep', "Don't keep", 'Your own discard preference'], ['craftingIngredient', 'Crafting ingredient', 'Used to build another item'],
    ['incarnon', 'Incarnon', 'Incarnon eligibility'], ['questWeapon', isWarframe ? 'Quest reward' : 'Quest weapon', 'Quest reward'],
    ['powerful', isWarframe ? 'Favorite' : 'Meta / powerful', 'A personal favorite or useful build'], ['hasPrimeVariant', 'Has Prime variant', 'A Prime counterpart exists'],
  ];
  const save = (record: Equipment) => {
    if (!record.name.trim()) { setError(`Give this ${noun} a name.`); return; }
    if (namedEntry && !entry) { setError(`Add ${record.name} from the ${weapons ? 'Warframes' : 'Weapons'} page.`); return; }
    if (existing.some(saved => saved.id !== record.id && normalizeName(saved.name) === normalizeName(record.name))) { setError(`This ${noun} is already in your inventory. Edit its existing row instead.`); return; }
    // New records receive catalog facts, while older saves retain their original
    // planning values. Known facts are always projected from the current catalog.
    const next = entry && !tracked ? withCatalogFacts(record, entry) : record;
    if (!onSave({ ...next, name: next.name.trim(), updatedAt: new Date().toISOString() })) setError('Your changes could not be applied. Close this editor to see recovery options.');
  };
  return <Dialog title={item ? item.name : `Add ${noun}`} onClose={onClose} className="equipment-dialog">
    {entry && <div className="weapon-overview">{entry.image && <img src={entry.image} alt={entry.name} width="152" height="116" />}<div><div className="weapon-tags"><span className="badge">{isWarframe ? kind : entry.slot}</span>{!isWarframe && <><span className="badge">{entry.weaponType}</span><span className="badge">MR {entry.masteryReq}</span></>}<span className="badge">{entry.variant}</span></div><p>{entry.description}</p></div></div>}
    {entry && <div className="equipment-tabs" role="tablist" aria-label={`${kind} details`} onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'details' : event.key === 'End' ? 'inventory' : view === 'details' ? 'inventory' : 'details';
      setView(next); document.getElementById(`weapon-${next}-tab`)?.focus();
    }}><button type="button" role="tab" id="weapon-details-tab" tabIndex={view === 'details' ? 0 : -1} aria-selected={view === 'details'} aria-controls="weapon-details-panel" onClick={() => setView('details')}>{kind} details</button><button type="button" role="tab" id="weapon-inventory-tab" tabIndex={view === 'inventory' ? 0 : -1} aria-selected={view === 'inventory'} aria-controls="weapon-inventory-panel" onClick={() => setView('inventory')}>My inventory</button></div>}
    {entry && view === 'details' && <div role="tabpanel" id="weapon-details-panel" aria-labelledby="weapon-details-tab"><WeaponDetails item={entry} /><div className="dialog-footer"><button type="button" className="button secondary" onClick={onClose}>Close details</button><button type="button" className="button primary" onClick={() => tracked ? setView('inventory') : save({ ...draft, owned: true })}>{tracked ? 'Edit inventory' : 'Mark owned'}</button></div>{error && <p className="callout danger" role="alert"><strong>Couldn’t save:</strong> {error}</p>}</div>}
    {view === 'inventory' && <form role={entry ? 'tabpanel' : undefined} id="weapon-inventory-panel" aria-labelledby={entry ? 'weapon-inventory-tab' : undefined} onSubmit={event => { event.preventDefault(); save(draft); }}>
      {!item && <div className="catalog-picker">
        <label htmlFor="catalog-search">Find in the {noun} catalog</label>
        <div className="search-field"><Search size={16} /><input id="catalog-search" value={query} onFocus={() => setCatalogVisible(true)} onChange={event => { setQuery(event.target.value); setCatalogVisible(true); }} placeholder={weapons ? 'Try Boltor, Braton Prime, Nataruk…' : 'Try Wisp, Wisp Prime, Excalibur Umbra…'} autoComplete="off" /></div>
        {catalogVisible && <div className="catalog-results">{matches.map(match => <button type="button" key={match.id} onClick={() => { setDraft(createEquipment(match)); setQuery(match.name); setCatalogVisible(false); }}><span>{match.name}</span><span className="muted">{isWarframe ? match.weaponType : match.slot}</span></button>)}{!matches.length && <p className="muted">No catalog match. You can add a custom {noun} below.</p>}</div>}
      </div>}
      <div className="form-grid">
        {(!entry || !item) && <label className="span-2">{weapons ? 'Weapon' : 'Warframe'} name<input value={draft.name} onChange={event => update('name', event.target.value)} maxLength={100} required /></label>}
        {!entry && <>{weapons && <label>Slot<Select label="Slot" value={draft.slot} onChange={value => update('slot', value as Slot)} options={slots.filter(value => value !== 'Warframe').map(value => ({ value, label: value }))} /></label>}<label>Variant<Select label="Variant" value={draft.variant} onChange={value => update('variant', value as Variant)} options={(weapons ? variants : ['Base', 'Prime', 'Umbra']).map(value => ({ value, label: value }))} /></label></>}
        <label>Upgraded<Select label="Upgraded" value={draft.upgraded} onChange={value => update('upgraded', value as Upgrade)} options={['Unknown', 'Yes', 'No'].map(value => ({ value, label: value }))} /></label>
        <label>Current rank<input type="number" min="0" max={Math.max(maximumRank(entry ?? draft) ?? 40, item?.rank ?? 0)} step="1" placeholder="Unknown" value={draft.rank ?? ''} onChange={event => update('rank', event.target.value === '' ? null : Number(event.target.value))} /></label>
        {weapons && (entry?.incarnon?.type === 'genesis' || !entry && draft.incarnon) && <label>Incarnon adapter installed<Select label="Incarnon adapter installed" value={draft.incarnonInstalled ?? 'Unknown'} onChange={value => update('incarnonInstalled', value as Upgrade)} options={['Unknown', 'Yes', 'No'].map(value => ({ value, label: value }))} /></label>}
        {canSubsume({ ...facts, catalogItem: entry }) && <label>Helminth ability unlocked<Select label="Helminth ability unlocked" value={draft.helminth ?? 'Unknown'} onChange={value => update('helminth', value as Upgrade)} options={['Unknown', 'Yes', 'No'].map(value => ({ value, label: value }))} /></label>}
      </div>
      <fieldset className="checkbox-grid"><legend>Inventory & planning</legend>{fields.filter(([key]) => (!isWarframe || !['incarnon', 'craftingIngredient'].includes(key)) && (!entry || !catalogFields.some(field => field === key))).map(([key, title, description]) => <label className="check-row" key={key}><input type="checkbox" checked={draft[key]} onChange={event => update(key, event.target.checked)} /><span>{title}<small>{description}</small></span></label>)}</fieldset>
      <div className="form-grid">
        {weapons && !entry && <label className="span-2">Crafting target<input value={draft.craftingTarget} onChange={event => update('craftingTarget', event.target.value)} placeholder="e.g. Proboscis Cernos" maxLength={500} /></label>}
        <label className="span-2">Recommended action override<Select label="Recommended action override" value={draft.override} onChange={value => update('override', value as Override)} options={['None', 'Keep', "Don't keep"].map(value => ({ value, label: value }))} /></label>
        {facts.hasPrimeVariant && draft.override === 'Keep' && <p className="catalog-notice span-2">A Prime counterpart exists, so the recommendation cannot be Keep. Your saved override is preserved.</p>}
        <div className="form-field span-2"><label htmlFor="equipment-notes">Notes</label><textarea id="equipment-notes" rows={3} value={draft.notes} onChange={event => update('notes', event.target.value)} placeholder="Build ideas, Forma investment, things to remember…" maxLength={5000} /></div>
      </div>
      {tracked && <div className="record-meta"><span>{isWarframe && item?.source === 'Weapon catalog' ? 'Warframe catalog' : item?.source}</span></div>}
      {error && <p className="callout danger" role="alert"><strong>Couldn’t save:</strong> {error}</p>}
      {deleteConfirm && <div className="callout caution"><strong>Remove {item?.name}?</strong> This removes its saved inventory entry; catalog details remain available. <button type="button" className="button danger-button" onClick={() => { if (item && !onDelete(item.id)) setError('The entry could not be removed. Close this editor to see recovery options.'); }}>Remove entry</button></div>}
      <div className="dialog-footer">{tracked && <button type="button" className="button quiet push-right" onClick={() => setDeleteConfirm(!deleteConfirm)}>{deleteConfirm ? 'Keep entry' : 'Remove entry'}</button>}<button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="submit" className="button primary">{item ? 'Save changes' : `Add ${noun}`}</button></div>
    </form>}
  </Dialog>;
}
