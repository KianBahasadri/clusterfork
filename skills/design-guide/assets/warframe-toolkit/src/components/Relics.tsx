import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { Ref } from 'react';
import { Minus, Plus, ScanLine } from 'lucide-react';
import { Dialog } from './Dialog';
import { Select } from './Select';
import { TableNotes } from './TableNotes';
import { MarketPrice } from './MarketPrice';
import { CollectionTable } from './CollectionTable';
import { RelicDetails } from './RelicDetails';
import type { CollectionActions, CollectionDefinition } from './CollectionTable';
import { defaultRelicColumns, RELIC_COLUMNS_STORAGE_KEY, relicColumns, relicTableModel } from '../lib/relicColumns';
import { relicCatalog, relicNames } from '../data/relicCatalog';
import { editableRelic, relicRows } from '../lib/relicCatalog';
import type { RelicRow } from '../lib/relicCatalog';
import { createRelic, MAX_RELIC_QUANTITY, parseRelicName, refinements, relicEras, relicKey } from '../lib/relics';
import type { Relic, Refinement } from '../lib/relics';

function RelicNameInput({ value, onChange, autoFocus }: { value: string; onChange: (value: string) => void; autoFocus: boolean }) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const matches = relicNames.filter(name => name.toLowerCase().includes(value.trim().toLowerCase()));
  const expanded = open && matches.length > 0;
  const choose = (name: string) => { onChange(name); setOpen(false); setActive(-1); };
  useLayoutEffect(() => {
    const menu = list.current;
    if (!expanded || !menu) return;
    menu.showPopover();
    const position = () => {
      const field = input.current!.getBoundingClientRect();
      const viewport = window.visualViewport;
      const viewportTop = viewport?.offsetTop ?? 0;
      const viewportLeft = viewport?.offsetLeft ?? 0;
      const viewportHeight = viewport?.height ?? innerHeight;
      const viewportWidth = viewport?.width ?? innerWidth;
      const below = viewportTop + viewportHeight - field.bottom - 8;
      const above = field.top - viewportTop - 8;
      const desiredHeight = Math.min(320, viewportHeight / 2, menu.scrollHeight + 2);
      const opensAbove = below < desiredHeight && above > below;
      const height = Math.max(0, Math.min(desiredHeight, opensAbove ? above : below));
      const width = Math.min(field.width, viewportWidth - 16);
      Object.assign(menu.style, {
        width: `${width}px`, maxHeight: `${height}px`,
        left: `${Math.max(viewportLeft + 8, Math.min(field.left, viewportLeft + viewportWidth - width - 8))}px`,
        top: `${opensAbove ? field.top - height - 4 : field.bottom + 4}px`,
      });
    };
    position();
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    window.visualViewport?.addEventListener('resize', position);
    window.visualViewport?.addEventListener('scroll', position);
    return () => {
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
      window.visualViewport?.removeEventListener('resize', position);
      window.visualViewport?.removeEventListener('scroll', position);
      menu.hidePopover();
    };
  }, [expanded, matches.length]);
  useEffect(() => {
    if (expanded && active >= 0) list.current?.children[active]?.scrollIntoView({ block: 'nearest' });
  }, [active, expanded]);
  return <div className="form-field span-2">
    <label htmlFor={id}>Relic name</label>
    <div className="autocomplete">
      <input ref={input} id={id} autoFocus={autoFocus} value={value} placeholder="Lith A1" maxLength={40} required autoComplete="off" role="combobox" aria-autocomplete="list" aria-expanded={expanded} aria-controls={expanded ? `${id}-list` : undefined} aria-activedescendant={expanded && active >= 0 ? `${id}-${active}` : undefined}
        onChange={event => { onChange(event.target.value); setActive(-1); setOpen(Boolean(event.target.value.trim())); }}
        onFocus={() => { setOpen(Boolean(value.trim())); setActive(-1); }} onClick={() => setOpen(Boolean(value.trim()))} onBlur={() => setOpen(false)}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing) return;
          if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && matches.length) {
            event.preventDefault(); setOpen(true);
            setActive(!expanded || active < 0 ? event.key === 'ArrowDown' ? 0 : matches.length - 1 : Math.max(0, Math.min(matches.length - 1, active + (event.key === 'ArrowDown' ? 1 : -1))));
          } else if (event.key === 'Enter' && expanded && active >= 0) {
            event.preventDefault(); choose(matches[active]);
          } else if (event.key === 'Escape' && expanded) {
            event.preventDefault(); event.stopPropagation(); setOpen(false); setActive(-1);
          }
        }} />
      {expanded && <ul id={`${id}-list`} ref={list} popover="manual" className="suggestion-list" role="listbox" aria-label="Matching relics">
        {matches.map((name, index) => <li key={name} id={`${id}-${index}`} role="option" aria-selected={active === index} onPointerDown={event => event.preventDefault()} onClick={() => choose(name)}>{name}</li>)}
      </ul>}
    </div>
  </div>;
}

function RelicForm({ item, existing, onSave, onDelete, onClose }: { item?: Relic; existing: Relic[]; onSave: (item: Relic) => boolean; onDelete: (id: string) => boolean; onClose: () => void }) {
  const [draft, setDraft] = useState<Relic>(() => item ? { ...item } : createRelic());
  const [error, setError] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const entry = relicCatalog.find(entry => relicKey(entry) === relicKey(draft));
  const tracked = existing.some(saved => saved.id === draft.id);
  const [view, setView] = useState<'details' | 'inventory'>(item && entry ? 'details' : 'inventory');
  const availableRefinements = refinements.filter(value => value !== 'Unrefined' || draft.refinement === 'Unrefined');
  return <Dialog title={item ? `Edit ${item.name}` : 'Add relic'} onClose={onClose} className="equipment-dialog">
    {entry && <div className="weapon-overview">{entry.image && <img src={entry.image} alt="" width="152" height="116" />}<div className="weapon-tags"><span className="badge">{entry.name}</span><span className="badge">{entry.refinement}</span></div></div>}
    {entry && <div className="equipment-tabs" role="tablist" aria-label="Relic details" onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'details' : event.key === 'End' ? 'inventory' : view === 'details' ? 'inventory' : 'details';
      setView(next); document.getElementById(`relic-${next}-tab`)?.focus();
    }}>{(['details', 'inventory'] as const).map(tab => <button key={tab} type="button" role="tab" id={`relic-${tab}-tab`} tabIndex={view === tab ? 0 : -1} aria-selected={view === tab} aria-controls={`relic-${tab}-panel`} onClick={() => setView(tab)}>{tab === 'details' ? 'Relic details' : 'My inventory'}</button>)}</div>}
    {entry && view === 'details' && <div role="tabpanel" id="relic-details-panel" aria-labelledby="relic-details-tab"><RelicDetails item={entry} /><div className="dialog-footer"><button type="button" className="button secondary" onClick={onClose}>Close details</button><button type="button" className="button primary" onClick={() => setView('inventory')}>Edit inventory</button></div></div>}
    {view === 'inventory' && <form role={entry ? 'tabpanel' : undefined} id="relic-inventory-panel" aria-labelledby={entry ? 'relic-inventory-tab' : undefined} onSubmit={event => {
      event.preventDefault();
      const parsed = parseRelicName(draft.name);
      if (!parsed) { setError('Enter an era and relic code, such as Lith A1 or Requiem I.'); return; }
      if (!Number.isInteger(draft.quantity) || draft.quantity < 0 || draft.quantity > MAX_RELIC_QUANTITY) { setError(`Quantity must be a whole number from 0 to ${MAX_RELIC_QUANTITY}.`); return; }
      const next = { ...draft, name: parsed.name, updatedAt: new Date().toISOString() };
      if (existing.some(relic => relic.id !== draft.id && relicKey(relic) === relicKey(next))) { setError('This relic and refinement are already tracked. Edit that entry’s quantity instead.'); return; }
      if (!onSave(next)) { setError('Your changes could not be applied. Your previous data is unchanged.'); return; }
      onClose();
    }}>
      <div className="form-grid">
        <RelicNameInput autoFocus={!item} value={draft.name} onChange={name => setDraft({ ...draft, name, refinement: parseRelicName(name)?.name === 'Requiem Eterna' ? 'Unrefined' : draft.refinement === 'Unrefined' ? 'Intact' : draft.refinement })} />
        {draft.refinement === 'Unrefined' ? <label>Refinement<span>Unrefined</span></label> : <label>Refinement<Select label="Refinement" value={draft.refinement} onChange={value => setDraft({ ...draft, refinement: value as Refinement })} options={availableRefinements.map(value => ({ value, label: value }))} /></label>}
        <label>Quantity<input type="number" min={0} max={MAX_RELIC_QUANTITY} step={1} required value={Number.isNaN(draft.quantity) ? '' : draft.quantity} onChange={event => setDraft({ ...draft, quantity: event.target.value === '' ? NaN : Number(event.target.value) })} /></label>
        <div className="form-field span-2"><label htmlFor="relic-notes">Notes</label><textarea id="relic-notes" rows={3} value={draft.notes} onChange={event => setDraft({ ...draft, notes: event.target.value })} maxLength={5000} /></div>
      </div>
      {error && <p className="callout danger relic-form-error" role="alert"><strong>Couldn’t save:</strong> {error}</p>}
      {confirmDelete && <div className="callout caution relic-form-error"><strong>Remove {item?.name} · {item?.refinement}?</strong><div><button type="button" className="button danger-button" onClick={() => { if (item && onDelete(item.id)) onClose(); else setError('This entry could not be removed.'); }}>Remove relic</button></div></div>}
      <div className="dialog-footer">{tracked && <button type="button" className="button quiet push-right" onClick={() => setConfirmDelete(!confirmDelete)}>{confirmDelete ? 'Keep entry' : 'Remove entry'}</button>}<button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="submit" className="button primary">{tracked ? 'Save changes' : 'Add relic'}</button></div>
    </form>}
  </Dialog>;
}

const relicDefinition: CollectionDefinition<RelicRow> = {
  label: 'Relics', itemLabel: 'relic', searchLabel: 'Search relics', regionLabel: 'Relic inventory', filterLabel: 'Relic filters',
  columns: relicColumns, defaultColumns: defaultRelicColumns, storageKey: RELIC_COLUMNS_STORAGE_KEY, model: relicTableModel,
  marketSubject: relic => ({ name: relic.name, kind: 'relic', refinement: relic.refinement }),
  rowLabel: relic => `${relic.name} ${relic.refinement}`, rowKey: relicKey, initialSorting: [{ key: 'name', direction: 1 }], pageSize: 100,
  filters: [
    { key: 'trackedOnly', label: 'Tracked only', kind: 'checkbox' },
    { key: 'ownedOnly', label: 'Owned only', kind: 'checkbox', column: 'quantity' },
    { key: 'era', label: 'Filter by era', kind: 'select', column: 'era', placeholder: 'All eras', choices: relicEras.map(value => ({ value, label: value })) },
    { key: 'refinement', label: 'Filter by refinement', kind: 'select', column: 'refinement', placeholder: 'All refinements', choices: refinements.map(value => ({ value, label: value })) },
  ],
  applyFilters: (relics, search, filters) => {
    const words = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return relics.filter(relic => {
      const text = `${relic.name} ${relic.refinement} ${relic.notes} ${relic.catalogItem?.rewards.map(reward => reward.name).join(' ') ?? ''}`.toLowerCase();
      return words.every(word => text.includes(word))
        && (!filters.era || parseRelicName(relic.name)?.era === filters.era)
        && (!filters.refinement || relic.refinement === filters.refinement)
        && (!filters.ownedOnly || relic.quantity > 0)
        && (!filters.trackedOnly || Boolean(relic.saved));
    });
  },
  summary: (count, relics, filters) => `${count} of ${filters.trackedOnly ? relics.filter(relic => relic.saved).length : relics.length} stacks · ${relics.reduce((total, relic) => total + relic.quantity, 0)} relics`,
  emptyState: (relics, filters) => filters.trackedOnly && !relics.some(relic => relic.saved)
    ? { title: 'No relics added.', resetLabel: 'Browse all relics', resetValues: { trackedOnly: false } }
    : { title: 'No matching relics.', resetLabel: 'Clear filters' },
};

export function Relics({ relics, trackedOnlyDefault, onSave, onDelete, onQuantityChange, onNotesChange, onExport, onImport, actionsRef }: {
  relics: Relic[]; onSave: (item: Relic) => boolean; onDelete: (id: string) => boolean;
  trackedOnlyDefault: boolean; onNotesChange: (item: RelicRow, notes: string) => void;
  onQuantityChange: (item: RelicRow, change: number) => void; onExport: () => void; onImport: () => void; actionsRef?: Ref<CollectionActions>;
}) {
  const [editing, setEditing] = useState<Relic | 'new' | null>(null);
  const rows = useMemo(() => relicRows(relics, relicCatalog), [relics]);
  const definition = { ...relicDefinition, filters: relicDefinition.filters.map(field => field.key === 'trackedOnly' ? { ...field, defaultValue: trackedOnlyDefault } : field) };
  return <>
    <CollectionTable definition={definition} items={rows} onEdit={row => setEditing(editableRelic(row))} onAdd={() => setEditing('new')} onExport={onExport} actionsRef={actionsRef}
      extraActions={<button type="button" className="button primary" onClick={onImport}><ScanLine size={16} />Import screenshots</button>}
      renderCell={(relic, column) => column.key === 'name' ? <>{relic.catalogItem?.image ? <img className="equipment-image" src={relic.catalogItem.image} alt="" width="88" height="56" loading="lazy" /> : <span className="equipment-initial" aria-hidden="true">{relic.name.slice(0, 2).toUpperCase()}</span>}<span className="equipment-label">{relic.name}</span></>
        : column.key === 'wfm' ? <MarketPrice subject={{ name: relic.name, kind: 'relic', refinement: relic.refinement }} />
        : column.key === 'quantity' ? <div className="quantity-control"><button type="button" className="icon-button" aria-label={`Decrease ${relic.name} ${relic.refinement} quantity`} title="Decrease quantity" disabled={relic.quantity === 0} onClick={() => onQuantityChange(relic, -1)}><Minus size={14} /></button><span className="relic-quantity" aria-label={`${relic.name} ${relic.refinement} quantity`}>{relic.quantity}</span><button type="button" className="icon-button" aria-label={`Increase ${relic.name} ${relic.refinement} quantity`} title="Increase quantity" disabled={relic.quantity === MAX_RELIC_QUANTITY} onClick={() => onQuantityChange(relic, 1)}><Plus size={14} /></button></div>
        : column.key === 'notes' ? <TableNotes name={`${relic.name} ${relic.refinement}`} value={relic.notes} onChange={notes => onNotesChange(relic, notes)} />
        : relicTableModel.value(relic, column.key) || <span className="table-missing">—</span>}
    />
    {editing && <RelicForm item={editing === 'new' ? undefined : editing} existing={relics} onSave={onSave} onDelete={onDelete} onClose={() => setEditing(null)} />}
  </>;
}
