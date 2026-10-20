import { useEffect, useId, useImperativeHandle, useRef, useState } from 'react';
import type { CSSProperties, MouseEvent, ReactNode, Ref } from 'react';
import { ArrowDown, ArrowDownToLine, ArrowUp, Filter, Plus, Search, SlidersHorizontal, X } from 'lucide-react';
import { filterChoices, filterLabel, filterRows, loadColumnPreferences, moveColumn, moveColumnSort, parseColumnPreferences, setColumnSort, sortRows } from '../lib/tableView';
import type { ColumnDropTarget, ColumnFilter, ColumnFilters, ColumnPreferences, ColumnSort, FilterChoice, TableColumn, TableModel } from '../lib/tableView';
import { ColumnMenu } from './ColumnMenu';
import { ColumnPicker } from './ColumnPicker';
import { Select } from './Select';
import { useColumnDrag } from './useColumnDrag';
import type { MarketSubject } from '../lib/market';
import { marketPrice, useMarketPrefetch, useMarketRevision } from '../lib/marketClient';

export type FilterValues = Record<string, string | boolean>;
export type CollectionFilter = {
  key: string; label: string; kind: 'checkbox' | 'select' | 'number'; column?: string;
  defaultValue?: string | boolean; choices?: FilterChoice[]; placeholder?: string; min?: number; max?: number;
};
export type CollectionActions = { add: () => void };
export interface CollectionDefinition<Row> {
  label: string; itemLabel: string; searchLabel: string; regionLabel: string; filterLabel: string;
  columns: TableColumn[]; defaultColumns: string[]; retiredColumns?: string[]; storageKey: string; legacyStorageKey?: string; model: TableModel<Row>;
  rowLabel: (item: Row) => string;
  rowKey?: (item: Row) => string;
  filters: CollectionFilter[];
  applyFilters: (items: Row[], search: string, filters: FilterValues) => Row[];
  initialSorting?: ColumnSort[];
  pageSize?: number;
  marketSubject?: (item: Row) => MarketSubject;
  summary?: (count: number, items: Row[], filters: FilterValues) => string;
  emptyState: (items: Row[], filters: FilterValues) => { title: string; description?: string; resetLabel?: string; resetValues?: FilterValues };
}

// A definition supplies domain data; this module owns the shared browsing and table behavior.
export function CollectionTable<Row extends { id: string }>({ definition, items, onEdit, onAdd, onExport, actionsRef, extraActions, renderCell }: {
  definition: CollectionDefinition<Row>; items: Row[];
  onEdit: (item: Row) => void; onAdd: () => void; onExport: () => void; actionsRef?: Ref<CollectionActions>;
  extraActions?: ReactNode; renderCell: (item: Row, column: TableColumn, visibleColumns: string[]) => ReactNode;
}) {
  const defaults = Object.fromEntries(definition.filters.map(field => [field.key, field.defaultValue ?? (field.kind === 'checkbox' ? false : '')]));
  useMarketRevision();
  const model: TableModel<Row> = { ...definition.model, value: (item, key) => key === 'wfm' && definition.marketSubject ? marketPrice(definition.marketSubject(item)) : definition.model.value(item, key) };
  const [search, setSearch] = useState('');
  const [filters, setFilters] = useState<FilterValues>(defaults);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [preferences, setPreferences] = useState(() => loadColumnPreferences(definition.storageKey, definition.columns, definition.defaultColumns, definition.retiredColumns, definition.legacyStorageKey));
  const visibleColumns = preferences.order.filter(key => preferences.visible.includes(key));
  const [sorting, setSorting] = useState<ColumnSort[]>(definition.initialSorting ?? []);
  const [columnFilters, setColumnFilters] = useState<ColumnFilters>({});
  const [page, setPage] = useState(0);
  useEffect(() => { setPage(0); }, [search, filters, sorting, columnFilters]);
  const [menu, setMenu] = useState<{ id: number; key: string; anchor: HTMLElement; point?: { x: number; y: number }; initialView?: 'filter' } | null>(null);
  const [active, setActive] = useState('');
  const [columnAnnouncement, setColumnAnnouncement] = useState('');
  const menuSequence = useRef(0);
  const headerButtons = useRef(new Map<string, HTMLButtonElement>());
  const rowButtons = useRef(new Map<string, HTMLButtonElement>());
  const filterButton = useRef<HTMLButtonElement>(null);
  const tableScroll = useRef<HTMLDivElement>(null);
  const filtersId = useId();
  useImperativeHandle(actionsRef, () => ({ add: onAdd }), [onAdd]);
  const columns = visibleColumns.map(key => definition.columns.find(column => column.key === key)!);
  const primary = definition.columns.find(column => column.required) ?? definition.columns[0];
  const openMenu = (key: string, anchor: HTMLElement, point?: { x: number; y: number }, initialView?: 'filter') => setMenu({ id: ++menuSequence.current, key, anchor, point, initialView });
  const openHeaderMenu = (key: string, event: MouseEvent<HTMLElement>) => {
    event.preventDefault();
    openMenu(key, headerButtons.current.get(key)!, event.clientX || event.clientY ? { x: event.clientX, y: event.clientY } : undefined);
  };
  const changeColumns = (next: ColumnPreferences) => {
    setPreferences(next);
    setSorting(current => current.filter(sort => next.visible.includes(sort.key)));
    try { localStorage.setItem(definition.storageKey, JSON.stringify(next)); }
    catch { /* Column changes still work for this view if preferences cannot be saved. */ }
  };
  const changeVisibility = (key: string, visible: boolean) => changeColumns({ ...preferences, visible: preferences.order.filter(candidate => definition.columns.some(column => column.key === candidate && column.required) || (candidate === key ? visible : visibleColumns.includes(candidate))) });
  const resetColumns = () => changeColumns(parseColumnPreferences(null, definition.columns, definition.defaultColumns));
  const reorderColumn = (key: string, target: ColumnDropTarget, reveal = true) => {
    const order = moveColumn(preferences.order, key, target);
    if (order.every((column, index) => column === preferences.order[index])) return;
    changeColumns({ ...preferences, order });
    const position = order.filter(column => visibleColumns.includes(column)).indexOf(key) + 1;
    setColumnAnnouncement(`${definition.columns.find(column => column.key === key)!.title} moved to column ${position} of ${columns.length}.`);
    requestAnimationFrame(() => {
      const button = headerButtons.current.get(key);
      button?.focus({ preventScroll: true });
      if (reveal) button?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    });
  };
  const columnDrag = useColumnDrag(tableScroll, (key, target) => reorderColumn(key, target, false), () => setMenu(null));
  const changeFilter = (field: CollectionFilter, value: string | boolean) => {
    if (field.column) setColumnFilters(current => { const next = { ...current }; delete next[field.column!]; return next; });
    setFilters(current => ({ ...current, [field.key]: value }));
    if (!filtersOpen && value === defaults[field.key]) requestAnimationFrame(() => filterButton.current?.focus());
  };
  const changeColumnFilter = (key: string, filter?: ColumnFilter) => {
    setColumnFilters(current => { const next = { ...current }; if (filter) next[key] = filter; else delete next[key]; return next; });
    const field = definition.filters.find(field => field.column === key);
    if (field) setFilters(current => ({ ...current, [field.key]: field.kind === 'checkbox' ? false : '' }));
  };
  const clearFilters = (overrides: FilterValues = {}) => {
    setSearch(''); setFilters({ ...defaults, ...overrides }); setColumnFilters({}); filterButton.current?.focus();
  };
  const filterCount = definition.filters.filter(field => filters[field.key] !== defaults[field.key]).length;
  const changed = Boolean(search || Object.keys(columnFilters).length || filterCount);
  const candidates = definition.applyFilters(items, search, filters);
  const pricing = useMarketPrefetch(definition.marketSubject ? filterRows(candidates, Object.fromEntries(Object.entries(columnFilters).filter(([key]) => key !== 'wfm')), model).map(definition.marketSubject) : [], Boolean(definition.marketSubject && (sorting.some(sort => sort.key === 'wfm') || columnFilters.wfm)));
  const filtered = filterRows(candidates, columnFilters, model);
  const sorted = sortRows(filtered, sorting, model);
  const pageSize = definition.pageSize ?? Math.max(1, sorted.length);
  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = sorted.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  const changePage = (next: number) => { setPage(next); tableScroll.current?.scrollTo({ top: 0 }); };
  const activeId = visible.some(item => item.id === active) ? active : visible[0]?.id;
  const menuColumn = menu && definition.columns.find(column => column.key === menu.key)!;
  const empty = definition.emptyState(items, filters);
  return <section className="inventory-section" aria-label={definition.label}>
    <div className="inventory-toolbar">
      <div className="search-field inventory-search"><Search size={16} /><input aria-label={definition.searchLabel} placeholder={`${definition.searchLabel}…`} value={search} onChange={event => setSearch(event.target.value)} /></div>
      <button ref={filterButton} type="button" className={`button secondary ${filtersOpen ? 'selected' : ''}`} aria-label="Filters" aria-expanded={filtersOpen} aria-controls={filtersId} onClick={() => setFiltersOpen(!filtersOpen)}><SlidersHorizontal size={16} />Filters{filterCount > 0 ? ` (${filterCount})` : ''}</button>
      <ColumnPicker options={definition.columns} selected={visibleColumns} onChange={changeVisibility} onReset={resetColumns} />
      {changed && <button type="button" className="button quiet" onClick={() => clearFilters()}>Reset filters</button>}
      <span className="filter-result" role="status">{definition.summary?.(filtered.length, items, filters) ?? `${filtered.length} of ${items.length}`}</span>
      {pricing && <span className="small muted" role="status">{pricing}</span>}
      <div className="inventory-actions">
        <button type="button" className="button secondary" onClick={onAdd}><Plus size={16} />Add {definition.itemLabel}</button>
        {extraActions}
        <button type="button" className="button quiet" onClick={onExport}><ArrowDownToLine size={15} />Export CSV</button>
      </div>
    </div>
    {(filtersOpen || filterCount > 0) && <div id={filtersId} className={filtersOpen ? 'inventory-filters' : 'inventory-active-filters'} role="group" aria-label={filtersOpen ? definition.filterLabel : `Active ${definition.filterLabel.toLowerCase()}`}>
      {definition.filters.filter(field => filtersOpen || filters[field.key] !== defaults[field.key]).map(field => field.kind === 'checkbox'
        ? <label key={field.key} className="owned-filter"><input type="checkbox" checked={Boolean(filters[field.key])} onChange={event => changeFilter(field, event.target.checked)} />{field.label}</label>
        : <label key={field.key}>{field.label}{field.kind === 'select'
          ? <Select label={field.label} value={String(filters[field.key])} onChange={value => changeFilter(field, value)} options={[{ value: '', label: field.placeholder ?? 'Any' }, ...field.choices ?? []]} />
          : <input type="number" min={field.min} max={field.max} step="1" placeholder={field.placeholder ?? 'Any'} value={String(filters[field.key])} onChange={event => changeFilter(field, event.target.value)} />}</label>)}
    </div>}
    {Object.keys(columnFilters).length > 0 && <div className="column-filter-list" role="group" aria-label="Column filters">{definition.columns.filter(column => columnFilters[column.key]).map(column => <div className="column-filter-chip" key={column.key}>
      <button type="button" onClick={event => openMenu(column.key, event.currentTarget, undefined, 'filter')}><Filter size={14} aria-hidden="true" />{column.title}: {filterLabel(columnFilters[column.key]!, filterChoices(column, items, model))}</button>
      <button type="button" className="icon-button" aria-label={`Remove ${column.title} filter`} title={`Remove ${column.title} filter`} onClick={() => { changeColumnFilter(column.key); filterButton.current?.focus(); }}><X size={14} aria-hidden="true" /></button>
    </div>)}</div>}
    <span className="sr-only" aria-live="polite">{columnAnnouncement}</span>
    <div ref={tableScroll} className="table-scroll inventory-grid" role="region" aria-label={definition.regionLabel} aria-busy={Boolean(pricing)} tabIndex={0} style={{ '--first-column-width': `${columns[0].width}px` } as CSSProperties}>
      <table className="inventory-table" style={{ minWidth: columns.reduce((width, column) => width + column.width, 0) }}>
        <caption className="sr-only">{definition.label}. Changes save automatically when signed in. Select a name to view or edit an entry. Drag a header to move its column. Click or right-click a header, or press Shift+F10, for column actions, including Move left and Move right.</caption>
        <colgroup>{columns.map(column => <col key={column.key} style={{ width: column.width }} />)}</colgroup>
        <thead><tr>{columns.map(column => {
          const sortIndex = sorting.findIndex(sort => sort.key === column.key);
          const sort = sorting[sortIndex];
          return <th scope="col" key={column.key} data-column-key={column.key} {...columnDrag.headerProps(column.key)} title="Drag to move; click for column options" className={column.kind === 'boolean' ? 'check-cell' : column.kind === 'number' ? 'number-cell' : ''} aria-sort={sortIndex === 0 ? sort.direction === 1 ? 'ascending' : 'descending' : undefined}
            onClick={event => openHeaderMenu(column.key, event)} onContextMenu={event => openHeaderMenu(column.key, event)}
            onKeyDown={event => { if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') { event.preventDefault(); event.stopPropagation(); openMenu(column.key, event.target as HTMLElement); } }}>
            <div className="column-header"><button ref={node => { if (node) headerButtons.current.set(column.key, node); else headerButtons.current.delete(column.key); }} type="button" className="sort-button" aria-label={`${column.title} column options`} aria-expanded={menu?.key === column.key} aria-description={sort ? `Sort priority ${sortIndex + 1} of ${sorting.length}, ${sort.direction === 1 ? 'ascending' : 'descending'}` : 'Unsorted'}>{column.title}<span className="sort-indicator" aria-hidden="true">{sort && <>{sort.direction === 1 ? <ArrowUp size={14} /> : <ArrowDown size={14} />}{sortIndex + 1}</>}</span></button></div>
          </th>;
        })}</tr></thead>
        <tbody>{visible.map((item, index) => <tr key={definition.rowKey?.(item) ?? item.id} onClick={event => { if (!(event.target as HTMLElement).closest('button, input, textarea, select, a, label, [contenteditable]') && !window.getSelection()?.toString()) onEdit(item); }}>
          {columns.map(column => <td key={column.key} data-column-key={column.key} className={column.kind === 'boolean' ? 'check-cell' : column.kind === 'number' ? 'number-cell' : column.key === primary.key ? 'equipment-cell' : column.wrap ? 'wrapping-cell' : ''}>
            {column.key === primary.key ? <button type="button" ref={node => { if (node) rowButtons.current.set(item.id, node); else rowButtons.current.delete(item.id); }} className="equipment-name" tabIndex={item.id === activeId ? 0 : -1} aria-label={`Edit ${definition.rowLabel(item)}`} onFocus={() => setActive(item.id)} onClick={() => onEdit(item)} onKeyDown={event => {
              if (event.altKey || event.ctrlKey || event.metaKey || !['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
              event.preventDefault();
              const next = visible[event.key === 'Home' ? 0 : event.key === 'End' ? visible.length - 1 : Math.max(0, Math.min(visible.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))];
              rowButtons.current.get(next.id)?.focus({ preventScroll: true }); rowButtons.current.get(next.id)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
            }}>{renderCell(item, column, visibleColumns)}</button> : renderCell(item, column, visibleColumns)}
          </td>)}
        </tr>)}</tbody>
      </table>
      {!sorted.length && <div className="empty-state"><Search size={28} /><h3>{empty.title}</h3>{empty.description && <p>{empty.description}</p>}{items.length > 0 && <button type="button" className="button secondary" onClick={() => clearFilters(empty.resetValues)}>{empty.resetLabel ?? 'Reset filters'}</button>}</div>}
    </div>
    {pageCount > 1 && <nav className="collection-pagination" aria-label={`${definition.label} pages`}><button type="button" className="button secondary" disabled={currentPage === 0} onClick={() => changePage(currentPage - 1)}>Previous</button><span aria-live="polite">{currentPage + 1} / {pageCount}</span><button type="button" className="button secondary" disabled={currentPage + 1 === pageCount} onClick={() => changePage(currentPage + 1)}>Next</button></nav>}
    {menu && menuColumn && <ColumnMenu key={menu.id} anchor={menu.anchor} point={menu.point} initialView={menu.initialView} column={menuColumn} columns={definition.columns} selected={visibleColumns} choices={filterChoices(menuColumn, items, model)} sorting={sorting} filter={columnFilters[menu.key]}
      onVisibility={changeVisibility} onReset={resetColumns} onMove={direction => { const target = visibleColumns[visibleColumns.indexOf(menu.key) + direction]; if (target) reorderColumn(menu.key, { key: target, edge: direction === -1 ? 'before' : 'after' }); }} onSort={direction => setSorting(current => setColumnSort(current, menu.key, direction))} onPriority={index => setSorting(current => moveColumnSort(current, menu.key, index))} onFilter={filter => changeColumnFilter(menu.key, filter)} onClose={() => {
        setMenu(null);
        requestAnimationFrame(() => { if (!menu.anchor.isConnected && document.activeElement === document.body) headerButtons.current.get(primary.key)?.focus({ preventScroll: true }); });
      }} onHide={() => { changeVisibility(menu.key, false); setMenu(null); headerButtons.current.get(primary.key)?.focus({ preventScroll: true }); }} />}
  </section>;
}
