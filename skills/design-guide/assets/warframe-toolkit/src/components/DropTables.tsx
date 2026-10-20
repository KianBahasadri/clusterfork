import { useCallback, useDeferredValue, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import type { Ref } from 'react';
import { ArrowDownToLine, ArrowLeft, Bookmark, CircleHelp, Filter, Info, Link, Search, SlidersHorizontal, X } from 'lucide-react';
import { TablePopover } from './TablePopover';
import { Select } from './Select';
import { Dialog } from './Dialog';
import { useToasts } from './Toasts';
import { DropFacet, DropFilterForm, DropSorts } from './DropTableControls';
import { DropDetails } from './DropDetails';
import { DropInfo } from './DropInfo';
import { DropResults } from './DropResults';
import { DropGroupCard, DropModePicker, DropRewardCards, dropBrowseOptions } from './DropBrowser';
import { compileDropQuery, defaultDropView, DROP_TABLES_URL, dropColumns, dropFilterLabel, dropRowsCsv, dropViewHash, filterDropRows, moveDropSort, readDropView, setDropSort, sortDropRows } from '../lib/dropTables';
import type { DropBrowseMode, DropColumnKey, DropFilter, DropRow, DropView } from '../lib/dropTables';
import { groupDropRows } from '../lib/dropExplorer';
import { useDropData } from '../lib/useDropData';
import { downloadFile } from '../lib/storage';
import type { SavedDropView } from '../lib/account';
import './DropTables.css';

export interface DropTableActions { exportCsv: () => void }
let sessionView = defaultDropView();
const facetKeys: DropColumnKey[] = ['category', 'location', 'mission', 'rotation', 'stage', 'era', 'refinement', 'rarity'];

export function DropTables({ actionsRef, savedViews, savedViewsError, canSave, onSaveViews, onSignIn }: { actionsRef: Ref<DropTableActions>; savedViews: SavedDropView[]; savedViewsError: string; canSave: boolean; onSaveViews: (views: SavedDropView[]) => boolean; onSignIn: () => void }) {
  const { data, loading, error } = useDropData();
  const notify = useToasts();
  const [view, setView] = useState(() => location.hash.includes('?') ? readDropView(location.hash) : sessionView);
  const [filtersOpen, setFiltersOpen] = useState(false), [help, setHelp] = useState(false), [viewsOpen, setViewsOpen] = useState(false);
  const [info, setInfo] = useState(false);
  const [chanceOpen, setChanceOpen] = useState(false);
  const [details, setDetails] = useState<DropRow | null>(null);
  const [viewName, setViewName] = useState('');
  const saved = { views: savedViews, error: savedViewsError };
  const searchInput = useRef<HTMLInputElement>(null), viewsButton = useRef<HTMLButtonElement>(null), chanceButton = useRef<HTMLButtonElement>(null);
  const resultsHeading = useRef<HTMLHeadingElement>(null);
  const changeView = (next: DropView) => setView(next);
  useEffect(() => {
    sessionView = view;
    const timer = window.setTimeout(() => history.replaceState(null, '', dropViewHash(view)), 250);
    return () => window.clearTimeout(timer);
  }, [view]);
  useEffect(() => {
    // A plain tab link resumes the session; a shared view link replaces its settings.
    const onHash = () => { if (location.hash.startsWith('#droptables?')) setView(readDropView(location.hash)); };
    window.addEventListener('hashchange', onHash); return () => window.removeEventListener('hashchange', onHash);
  }, []);


  const filterEntries = Object.entries(view.filters) as [DropColumnKey, DropFilter][];
  const changed = Boolean(view.query || filterEntries.length);
  // Older saved links can still open their search directly in the item browser.
  const mode = view.browse?.mode ?? (changed || view.sorting.length ? 'item' : undefined);
  const option = dropBrowseOptions.find(option => option.mode === mode);
  const groupKey = view.browse?.group;
  const deferredQuery = useDeferredValue(view.query);
  const resultsKey = JSON.stringify([mode, groupKey, deferredQuery, view.filters, view.sorting]);
  const query = useMemo(() => compileDropQuery(deferredQuery), [deferredQuery]);
  const filtered = useMemo(() => sortDropRows(filterDropRows(data?.rows ?? [], query, view.filters), view.sorting.length ? view.sorting : [{ key: mode === 'item' ? 'source' : 'reward', direction: 1 }]), [data, query, view.filters, view.sorting, mode]);
  const allGroups = useMemo(() => mode ? groupDropRows(data?.rows ?? [], mode) : [], [data, mode]);
  const groups = useMemo(() => mode ? groupDropRows(filtered, mode, view.sorting) : [], [filtered, mode, view.sorting]);
  const selected = groupKey === undefined ? undefined : groups.find(group => group.key === groupKey);
  const selectedTitle = groupKey === undefined ? '' : allGroups.find(group => group.key === groupKey)?.title ?? 'Group unavailable';
  const rows = useMemo(() => groupKey !== undefined ? selected?.rows ?? [] : sortDropRows(groups.flatMap(group => group.rows), view.sorting), [groupKey, selected, groups, view.sorting]);
  const total = groupKey === undefined ? groups.length : rows.length;
  const facets = useMemo(() => Object.fromEntries(facetKeys.map(key => {
    const values = new Map<string, number>();
    for (const row of data?.rows ?? []) { const value = String(row[key] ?? ''); values.set(value, (values.get(value) ?? 0) + 1); }
    return [key, [...values].map(([value, count]) => ({ value, count })).sort((a, b) => !a.value ? 1 : !b.value ? -1 : a.value.localeCompare(b.value, undefined, { numeric: true }))];
  })), [data]);
  const setFilter = (key: DropColumnKey, filter?: DropFilter) => { const next = { ...view.filters }; if (filter) next[key] = filter; else delete next[key]; changeView({ ...view, filters: next }); };
  const resetFilters = () => changeView({ ...view, query: '', filters: {} });
  const focusResults = useCallback(() => requestAnimationFrame(() => { resultsHeading.current?.focus({ preventScroll: true }); resultsHeading.current?.scrollIntoView({ block: 'nearest' }); }), []);
  const chooseMode = (mode: DropBrowseMode) => { changeView({ ...view, browse: { mode } }); setChanceOpen(false); };
  const openGroup = useCallback((group: string) => { setView(current => ({ ...current, browse: { mode: mode!, group } })); focusResults(); }, [mode, focusResults]);
  const exportCsv = () => {
    if (!rows.length || deferredQuery !== view.query) return;
    downloadFile(`warframe-drops-${new Date().toISOString().slice(0, 10)}.csv`, dropRowsCsv(rows, dropColumns.map(column => column.key)), 'text/csv;charset=utf-8');
    notify(`Exported all ${rows.length.toLocaleString()} matching entries with all fields.`, 'success');
  };
  useImperativeHandle(actionsRef, () => ({ exportCsv }));
  const saveViews = onSaveViews;
  const closeViews = () => { setViewsOpen(false); requestAnimationFrame(() => viewsButton.current?.focus({ preventScroll: true })); };
  const sortValue = !view.sorting.length ? 'name' : view.sorting.length === 1 && view.sorting[0].key === 'chance' ? view.sorting[0].direction === -1 ? 'chance-desc' : 'chance-asc' : 'custom';
  const browseRelated = (mode: DropBrowseMode, group: string) => {
    changeView({ ...view, query: '', filters: {}, sorting: [{ key: 'chance', direction: -1 }], browse: { mode, group } });
    setDetails(null); focusResults();
  };

  return <section className="drop-tables" aria-label="Drop Tables" aria-busy={loading}>
    <div className="drop-heading"><div className="drop-title"><h1>Drop Tables</h1><button type="button" className="icon-button" aria-label="About Drop Tables" title="About Drop Tables" aria-haspopup="dialog" onClick={() => setInfo(true)}><Info size={18} /></button></div>
      <div className="drop-heading-actions"><button type="button" ref={viewsButton} className="button secondary" aria-expanded={viewsOpen} onClick={() => setViewsOpen(!viewsOpen)}><Bookmark size={15} />Saved views</button></div>
    </div>
    {error && <div className="callout danger" role="alert">{error}{data && <p>Showing the last successful fetch from {new Date(data.fetchedAt).toLocaleString([], { timeZoneName: 'short' })}.</p>}<p>Updates will retry automatically.</p></div>}
    {data && data.skippedRows > 0 && <p className="callout caution" role="status">{data.skippedRows.toLocaleString()} entries could not be parsed. These results are incomplete; consult the official table.</p>}
    <DropModePicker mode={mode} onSelect={chooseMode} />
    {mode && <div className="drop-workspace">
      <div className="drop-toolbar">
        <div className="drop-search-label"><label className="sr-only" htmlFor="drop-search">Search drop tables</label><div className="search-field"><Search size={18} aria-hidden="true" /><input id="drop-search" ref={searchInput} placeholder={mode === 'item' ? 'Search items, e.g. Nova Prime Systems…' : mode === 'mission' ? 'Search missions, rewards or locations…' : mode === 'relic' ? 'Search relics or their rewards…' : 'Search rewards, locations or sources…'} value={view.query} maxLength={2000} aria-invalid={Boolean(query.error) || undefined} aria-describedby={query.error ? 'drop-query-error' : undefined} onChange={event => changeView({ ...view, query: event.target.value })} />{view.query && <button type="button" className="icon-button" aria-label="Clear drop search" onClick={() => { changeView({ ...view, query: '' }); searchInput.current?.focus(); }}><X size={15} /></button>}</div></div>
        <button type="button" className="button secondary" aria-expanded={filtersOpen} aria-controls="drop-filters" onClick={() => setFiltersOpen(!filtersOpen)}><SlidersHorizontal size={16} />Filters{filterEntries.length > 0 ? ` (${filterEntries.length})` : ''}</button>
        <button type="button" className="icon-button" aria-label="Search help" title="Search help" onClick={() => setHelp(true)}><CircleHelp size={18} /></button>
      </div>
      {query.error && <p id="drop-query-error" className="error-text small" role="alert">{query.error}</p>}
      {filtersOpen && <div className="drop-filters" id="drop-filters" aria-label="Drop table filters">{facetKeys.map(key => <DropFacet key={key} column={dropColumns.find(column => column.key === key)!} options={facets[key] ?? []} filter={view.filters[key]} onChange={filter => setFilter(key, filter)} />)}<button type="button" ref={chanceButton} className="button secondary" aria-expanded={chanceOpen} onClick={() => setChanceOpen(!chanceOpen)}><Filter size={14} />Drop chance</button><button type="button" className="button quiet" disabled={!changed} onClick={resetFilters}>Reset filters</button></div>}
      {filterEntries.length > 0 && <div className="drop-filter-chips" aria-label="Active filters">{filterEntries.map(([key, filter]) => <button type="button" key={key} title={dropFilterLabel(filter)} onClick={() => setFilter(key)} aria-label={`Remove ${dropColumns.find(column => column.key === key)!.title} filter`}><span>{dropColumns.find(column => column.key === key)!.title}: {dropFilterLabel(filter)}</span><X size={12} /></button>)}</div>}
      {sortValue === 'custom' && <DropSorts sorting={view.sorting} onSort={(key, direction) => changeView({ ...view, sorting: setDropSort(view.sorting, key, direction) })} onPriority={(key, index) => changeView({ ...view, sorting: moveDropSort(view.sorting, key, index) })} onClear={() => changeView({ ...view, sorting: [] })} />}
      <div className="drop-results-heading">
        <div>{groupKey !== undefined && <button type="button" className="drop-back" onClick={() => { chooseMode(mode); focusResults(); }}><ArrowLeft size={14} />All {option!.noun}</button>}<h2 ref={resultsHeading} tabIndex={-1}>{groupKey === undefined ? option!.noun[0].toUpperCase() + option!.noun.slice(1) : selectedTitle}</h2>
          <div className="drop-result-summary" role="status">{data ? <>{groupKey === undefined && <><strong>{groups.length.toLocaleString()}</strong> {option!.noun}<span aria-hidden="true"> · </span></>}<strong>{rows.length.toLocaleString()}</strong> matching entries</> : loading ? 'Loading saved drop tables…' : 'Waiting for an automatic update'}</div>
        </div>
        <div className="drop-results-actions"><label className="drop-sort-label"><span>Sort by</span><Select label="Sort results" value={sortValue} options={[{ value: 'name', label: 'Name A–Z' }, { value: 'chance-desc', label: 'Chance: high to low' }, { value: 'chance-asc', label: 'Chance: low to high' }, ...(sortValue === 'custom' ? [{ value: 'custom', label: 'Custom order' }] : [])]} onChange={value => changeView({ ...view, sorting: value === 'name' ? [] : [{ key: 'chance', direction: value === 'chance-desc' ? -1 : 1 }] })} /></label>
          <button type="button" className="icon-button" aria-label="Copy view link" title="Copy view link" onClick={async () => { try { await navigator.clipboard.writeText(`${location.origin}${location.pathname}${dropViewHash(view)}`); notify('View link copied. It uses the latest automatic update.', 'success'); } catch { notify('Could not copy the link. Copy this page’s address from your browser.', 'caution'); } }}><Link size={17} /></button><button type="button" className="icon-button" aria-label="Export CSV" title="Export all matching entries as CSV" disabled={!rows.length || view.query !== deferredQuery} onClick={exportCsv}><ArrowDownToLine size={17} /></button>
        </div>
      </div>
      <div className="drop-results" role="region" aria-label="Drop results" aria-busy={loading || view.query !== deferredQuery}>
        {!data && loading ? <div className="drop-group-grid" aria-hidden="true">{Array.from({ length: 6 }, (_, index) => <div className="drop-loading-card" key={index}><span /><span /><span /></div>)}</div> : groupKey === undefined ? <DropResults key={resultsKey} className="drop-group-grid">{groups.map(group => <DropGroupCard key={group.key} group={group} mode={mode} onSelect={openGroup} />)}</DropResults> : <DropRewardCards key={resultsKey} rows={rows} mode={mode} onOpen={setDetails} />}
        {data && !total && <div className="drop-empty"><Search size={28} aria-hidden="true" /><h3>No matching drops</h3><p>{query.error || 'Try another search, clear your filters, or choose a different view.'}</p>{changed && <button type="button" className="button secondary" onClick={resetFilters}>Clear search and filters</button>}</div>}
        {!data && !loading && <div className="drop-empty"><p>Drop tables will appear after a successful automatic update.</p></div>}
      </div>
    </div>}
    {info && <DropInfo data={data} onClose={() => setInfo(false)} />}
    {chanceOpen && filtersOpen && <TablePopover anchor={chanceButton.current!} label="Filter Drop chance" onClose={() => setChanceOpen(false)}><div className="column-menu-heading">Drop chance</div><DropFilterForm column={dropColumns.find(column => column.key === 'chance')!} filter={view.filters.chance} onApply={filter => { setFilter('chance', filter); setChanceOpen(false); chanceButton.current?.focus(); }} /></TablePopover>}
    {details && <DropDetails row={details} onClose={() => setDetails(null)} onFindReward={() => browseRelated('item', details.reward)} onFindSource={() => browseRelated('source', details.source)} />}
    {viewsOpen && <TablePopover anchor={viewsButton.current!} label="Drop table views" className="drop-views-popover" onClose={() => setViewsOpen(false)}><div className="column-menu-heading">Saved views</div><div className="column-menu-content">
      {saved.views.length > 0 ? <div className="drop-saved-views">{saved.views.map((entry, index) => <div key={entry.name}><button type="button" className="column-menu-action" onClick={() => { changeView({ ...entry.view, browse: entry.view.browse ?? { mode: 'item' } }); closeViews(); }}>{entry.name}</button>{canSave && <button type="button" className="icon-button" aria-label={`Delete view ${entry.name}`} onClick={() => saveViews(saved.views.filter((_, position) => position !== index))}><X size={14} /></button>}</div>)}</div> : <p className="drop-saved-empty muted small">No saved views</p>}
      <form className="drop-save-view" onSubmit={event => { event.preventDefault(); if (!canSave) { closeViews(); onSignIn(); return; } if (!viewName.trim() || saved.error || !mode) return; if (saved.views.some(entry => entry.name.toLocaleLowerCase() === viewName.trim().toLocaleLowerCase())) { notify('A view with that name already exists. Choose another name.', 'caution'); return; } if (saveViews([...saved.views, { name: viewName.trim(), view: { ...view, browse: view.browse ?? { mode } } }])) { setViewName(''); closeViews(); } }}><label>View name<input maxLength={60} value={viewName} onChange={event => setViewName(event.target.value)} disabled={!canSave || Boolean(saved.error) || !mode} /></label><button type="submit" className="button secondary" disabled={canSave && (!viewName.trim() || Boolean(saved.error) || !mode)}>{canSave ? 'Save current view' : 'Sign in to save view'}</button>{saved.error && <p className="error-text small">{saved.error}</p>}{!mode && <p className="muted small">Choose a browse view to save it.</p>}</form>
    </div></TablePopover>}
    {help && <Dialog title="Search & drop rates" onClose={() => setHelp(false)} className="drop-help"><h3>Search syntax</h3><p>Combine terms to narrow results. Use quotes for a phrase and a minus sign to exclude a term. Search ignores letter case.</p><dl className="drop-query-examples"><div><dt><code>"nova prime"</code></dt><dd>Find the phrase across all text fields</dd></div><div><dt><code>reward:forma -source:lith</code></dt><dd>Forma rewards, excluding Lith sources</dd></div><div><dt><code>mission:survival rotation:c</code></dt><dd>Survival rewards in rotation C</dd></div><div><dt><code>chance:&gt;=10 chance:&lt;50</code></dt><dd>Drop chance from 10% up to, but excluding, 50%</dd></div></dl><p className="small muted">Fields: reward, source, category, location, mission, rotation, stage, era, refinement, rarity, chance, within, roll. Numeric fields accept =, &gt;, &gt;=, &lt;, and &lt;=.</p><h3>Reading the rates</h3><p><strong>Drop chance</strong> combines the source’s table roll rate with the reward’s within-table chance. Rates above 100% indicate multiple rolls; their overall odds are left uncalculated. The rarity label comes directly from the publisher.</p><p>Bounty stages and rotations are separate opportunities. Duplicate rewards within a table remain separate entries; they are not automatically added together. Reverse indexes for enemy drops are shown once.</p><p className="muted small">The official page describes PC rewards and may omit drops or lag hotfixes. Listed relics or sources can be vaulted or unavailable. Automatic updates retrieve the published page, not an in-game live rotation. CSV exports every matching entry in the current group or view, with all fields; rates are percentages.</p><a href={DROP_TABLES_URL} target="_blank" rel="noreferrer">Open the official drop tables</a></Dialog>}
  </section>;
}
