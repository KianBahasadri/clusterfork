import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Moon, Search, SlidersHorizontal, Sun, Table2, UserRound } from 'lucide-react';
import { InventoryTable } from './components/InventoryTable';
import type { CollectionActions } from './components/CollectionTable';
import { EquipmentForm } from './components/EquipmentForm';
import { ScreenshotImport } from './components/ScreenshotImport';
import { DropTables } from './components/DropTables';
import type { DropTableActions } from './components/DropTables';
import { Commands } from './components/Commands';
import type { Command } from './components/Commands';
import { Dialog } from './components/Dialog';
import { AccountDialog } from './components/AccountDialog';
import { CraftingNav } from './components/CraftingNav';
import { useAccount } from './lib/useAccount';
import { useToasts } from './components/Toasts';
import './components/Account.css';
import { catalog } from './data/catalog';
import { editableRecord, equipmentRows, toggleEquipment } from './lib/weaponCatalog';
import { mergeObservations, normalizeName } from './lib/model';
import type { Equipment, Inventory } from './lib/model';
import { downloadFile, inventoryCsv, relicsCsv, STORAGE_KEY, warframesCsv } from './lib/storage';
import { relicKey } from './lib/relics';
import { updateRelic } from './lib/relicCatalog';
import { mergeRelicObservations } from './lib/relicMatching';
import weaponsIcon from './assets/warframe/weapons.png';
import warframesIcon from './assets/warframe/warframes.png';
import relicsIcon from './assets/warframe/relics.png';

type Page = 'weapons' | 'warframes' | 'relics' | 'droptables' | 'crafting' | 'modder' | 'import';
const Relics = lazy(() => import('./components/Relics').then(module => ({ default: module.Relics })));
const Modder = lazy(() => import('./components/Modder').then(module => ({ default: module.Modder })));
const Crafting = lazy(() => import('./components/Crafting').then(module => ({ default: module.Crafting })));

const pageNames: Record<Page, string> = { weapons: 'Weapons', warframes: 'Warframes', relics: 'Relics', droptables: 'Drop Tables', crafting: 'Crafting', modder: 'Modding Calculator', import: 'Screenshot import' };
const collectionTabs = [{ key: 'weapons', icon: weaponsIcon }, { key: 'warframes', icon: warframesIcon }, { key: 'relics', icon: relicsIcon }, { key: 'droptables', icon: '' }, { key: 'crafting', icon: '' }, { key: 'modder', icon: '' }] as const;

function pageFromHash(): Page {
  const value = location.hash.slice(1).split('?')[0];
  return Object.keys(pageNames).includes(value) ? value as Page : 'weapons';
}

export default function App() {
  const account = useAccount();
  const inventory = account.profile.inventory;
  const commit = (next: Inventory, saveDelay?: number) => { const saved = account.commitInventory(next, saveDelay); if (saved) inventoryRef.current = next; return saved; };
  const [accountOpen, setAccountOpen] = useState<'login' | 'register' | null>(null);
  const inventoryRef = useRef(inventory); inventoryRef.current = inventory;
  const [page, setPage] = useState<Page>(pageFromHash);
  const [craftingPlan, setCraftingPlan] = useState('');
  const [importRelics, setImportRelics] = useState(false);
  const equipmentLabel = page === 'warframes' ? 'Warframe' : page === 'relics' ? 'relic' : 'weapon';
  const [editing, setEditing] = useState<Equipment | 'new' | null>(null);
  const [palette, setPalette] = useState(false);
  const [shortcuts, setShortcuts] = useState(false);
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem('arsenal.theme') || 'dark'; } catch { return 'dark'; } });
  const notify = useToasts();
  const pendingNotes = useRef(new Map<string, { userId: string | undefined; collection: 'items' | 'relics'; id: string; name: string; value: string }>());
  const noteChanged = (collection: 'items' | 'relics', id: string, name: string, value: string) => pendingNotes.current.set(`${collection}:${id}`, { userId: account.user?.id, collection, id, name, value });
  const dropActions = useRef<DropTableActions>(null);
  const collectionActions = useRef<CollectionActions>(null);

  useEffect(() => {
    const hashChange = () => {
      const destination = pageFromHash();
      if (destination === 'crafting') setCraftingPlan('');
      setPage(destination);
    };
    hashChange();
    window.addEventListener('hashchange', hashChange);
    return () => window.removeEventListener('hashchange', hashChange);
  }, []);
  useEffect(() => { setEditing(null); setCraftingPlan(''); }, [account.user?.id]);
  useEffect(() => {
    for (const [key, change] of pendingNotes.current) if (change.userId !== account.user?.id) pendingNotes.current.delete(key);
    if (!pendingNotes.current.size || account.error || account.user && (account.dirty || account.saving)) return;
    const timer = window.setTimeout(() => {
      for (const change of pendingNotes.current.values()) {
        // A rejected save or an explicit reload must never produce a success toast.
        if (inventory[change.collection].find(item => item.id === change.id)?.notes !== change.value) continue;
        notify(account.user ? `Notes saved for ${change.name}.` : `Notes updated for ${change.name}. Sign in to save.`, account.user ? 'success' : 'neutral');
      }
      pendingNotes.current.clear();
    }, account.user ? 0 : 600);
    return () => clearTimeout(timer);
  }, [inventory, account.user, account.dirty, account.saving, account.error, notify]);
  useEffect(() => { document.documentElement.dataset.theme = theme; try { localStorage.setItem('arsenal.theme', theme); } catch { /* Inventory saving reports its own failures. */ } }, [theme]);
  const navigate = (destination: Page) => {
    if (destination === 'crafting') setCraftingPlan('');
    setPage(destination); location.hash = destination;
  };
  const openCraftingPlan = (id: string) => {
    // Set both together; a hashchange would otherwise return Crafting to its picker.
    if (location.hash !== '#crafting') history.pushState(null, '', '#crafting');
    setCraftingPlan(id); setPage('crafting');
  };
  const openImport = () => { setImportRelics(page === 'relics'); navigate('import'); };
  const exportCsv = () => {
    const rows = equipmentRows(inventoryRef.current.items, catalog);
    const warframes = page === 'warframes';
    downloadFile(`grakata-${warframes ? 'warframes' : 'inventory'}-${new Date().toISOString().slice(0, 10)}.csv`, warframes ? warframesCsv(rows) : inventoryCsv(rows), 'text/csv;charset=utf-8');
    notify(`${warframes ? 'Warframes' : 'Inventory'} CSV downloaded.`, 'success');
  };
  const exportRelics = () => { downloadFile(`grakata-relics-${new Date().toISOString().slice(0, 10)}.csv`, relicsCsv(inventoryRef.current.relics ?? []), 'text/csv;charset=utf-8'); notify('Relics CSV downloaded.', 'success'); };
  const commands: Command[] = [
    ...(['weapons', 'warframes', 'relics'].includes(page) ? [{ label: `Add ${page === 'relics' ? 'relic' : equipmentLabel}`, keys: ['Alt', 'N'], run: () => collectionActions.current?.add() }] : []),
    { label: 'Import screenshots', keys: ['Alt', 'I'], run: openImport },
    ...(!['crafting', 'modder'].includes(page) ? [{ label: page === 'droptables' ? 'Export filtered drop tables CSV' : page === 'relics' ? 'Export relics CSV' : page === 'warframes' ? 'Export Warframes CSV' : 'Export inventory CSV', keys: ['Alt', 'E'], run: page === 'droptables' ? () => dropActions.current?.exportCsv() : page === 'relics' ? exportRelics : exportCsv }] : []),
    { label: 'Toggle theme', keys: ['Alt', 'Shift', 'T'], run: () => setTheme(value => value === 'dark' ? 'light' : 'dark') },
    { label: 'Keyboard shortcuts', keys: ['Alt', '/'], run: () => setShortcuts(true) },
  ];
  const destinations: Command[] = collectionTabs.map(({ key }, index) => ({ label: pageNames[key], keys: ['Alt', String(index + 1)], run: () => navigate(key) }));
  const keyHandler = useRef<(event: KeyboardEvent) => void>(() => {});
  keyHandler.current = event => {
    if ((event.ctrlKey || event.metaKey) && (event.key.toLowerCase() === 'k' || event.ctrlKey && event.code === 'Space')) { if (palette || !document.querySelector('dialog[open]')) { event.preventDefault(); setPalette(value => !value); } return; }
    if ((event.target as HTMLElement)?.closest('input, textarea, [role="combobox"], [contenteditable], dialog') || !event.altKey) return;
    const key = event.key.toLowerCase();
    const command = [...commands, ...destinations].find(option => option.keys.at(-1)?.toLowerCase() === key && option.keys.includes('Shift') === event.shiftKey);
    if (command) { event.preventDefault(); command.run(); }
    if (key === 'j') { event.preventDefault(); setPalette(true); }
  };
  useEffect(() => { const handle = (event: KeyboardEvent) => keyHandler.current(event); window.addEventListener('keydown', handle); return () => window.removeEventListener('keydown', handle); }, []);

  const relics = inventory.relics ?? [];
  const nav = <nav className="collection-nav" aria-label="Main navigation"><ul>
    {collectionTabs.map(({ key, icon }, index) => key === 'crafting' ? <CraftingNav key={`crafting-${account.user?.id ?? 'guest'}`} plans={account.profile.craftingPlans} active={page === 'crafting'} selected={craftingPlan} onHome={() => navigate('crafting')} onPlan={openCraftingPlan} /> : <li key={key} className={key === 'droptables' ? 'nav-group-start' : undefined}><a href={`#${key}`} aria-current={page === key ? 'page' : undefined} aria-keyshortcuts={`Alt+${index + 1}`} title={`${pageNames[key]} (Alt+${index + 1})`} onClick={event => { event.preventDefault(); navigate(key); }}><span className="nav-icon" aria-hidden="true" style={!icon ? { background: 'none' } : { maskImage: `url(${icon})` }}>{key === 'droptables' && <Table2 size={20} />}{key === 'modder' && <SlidersHorizontal size={20} />}</span><span>{pageNames[key]}</span></a></li>)}
    </ul></nav>;
  const visibleItems = useMemo(() => equipmentRows(inventory.items, catalog, true).filter(item => page === 'warframes' ? item.slot === 'Warframe' : item.slot !== 'Warframe'), [inventory.items, page]);
  const isInventory = page === 'weapons' || page === 'warframes';
  return <div className="app-shell">
    <div className="content-shell"><header className="topbar">{nav}<div className="topbar-right"><div className="page-utilities"><button type="button" className="search-launcher" aria-label="Open search and commands" aria-keyshortcuts="Control+k Meta+k Control+Space" onClick={() => setPalette(true)}><Search size={15} /></button><button type="button" className="icon-button theme-toggle" aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} onClick={() => setTheme(value => value === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}</button><div className="account-actions"><button type="button" className={`button account-button ${account.user ? 'secondary' : 'account-sign-in'}`} aria-label={account.user ? `Account: ${account.user.username}` : 'Sign in'} aria-haspopup="dialog" disabled={account.checking} onClick={() => setAccountOpen('login')}><UserRound size={16} /><span>{account.user?.username ?? 'Sign in'}</span></button>{!account.user && <button type="button" className="button secondary" aria-haspopup="dialog" disabled={account.checking} onClick={() => setAccountOpen('register')}>Create account</button>}</div></div></div></header>
      <main id="main-content" className={page === 'import' ? 'import-page' : undefined}>
        {account.user && <div className="sr-only" role="status" aria-label="Save status">{account.error && account.dirty ? 'Unsaved changes' : account.saving || account.dirty ? 'Saving…' : 'Saved'}</div>}
        {account.error && <div className="callout danger" role="alert">{account.error}<div className="account-error-actions">{account.unreadable && <button type="button" className="button secondary" onClick={() => { try { downloadFile('grakata-original-data.txt', localStorage.getItem(STORAGE_KEY) || '', 'text/plain'); } catch { notify('Browser storage is unavailable.', 'caution'); } }}>Download original data</button>}{account.user && <><button type="button" className="button secondary" disabled={account.saving} onClick={() => void account.retrySave()}>Try saving again</button><button type="button" className="button secondary" onClick={() => downloadFile('grakata-unsaved-profile.json', JSON.stringify(account.profile, null, 2), 'application/json')}>Download changes</button><button type="button" className="button quiet" disabled={account.saving} onClick={() => void account.reloadSaved()}>Discard changes and reload saved data</button></>}</div></div>}
        {isInventory && <InventoryTable key={`${page}:${account.user?.id ?? 'guest'}`} weapons={page === 'weapons'} ownedOnlyDefault={Boolean(account.user)} items={visibleItems} onEdit={row => setEditing(editableRecord(row))} onToggle={(item, field) => commit({ ...inventoryRef.current, items: toggleEquipment(inventoryRef.current.items, item, field) })}
          onNotesChange={(row, notes) => {
            const current = inventoryRef.current;
            const previous = current.items.find(item => item.id === row.id || normalizeName(item.name) === normalizeName(row.name));
            if ((previous?.notes ?? '') === notes) return;
            const item = { ...(previous ?? editableRecord(row)), notes, updatedAt: new Date().toISOString() };
            if (commit({ ...current, items: previous ? current.items.map(entry => entry.id === item.id ? item : entry) : [...current.items, item] }, 600)) noteChanged('items', item.id, item.name, notes);
          }} onAdd={() => setEditing('new')} onImport={openImport} onExport={exportCsv} actionsRef={collectionActions} />}
        {page === 'relics' && <Suspense fallback={<p role="status" className="small muted">Loading relics…</p>}><Relics key={account.user?.id ?? 'guest'} relics={relics} trackedOnlyDefault={Boolean(account.user)} onExport={exportRelics} onImport={openImport} actionsRef={collectionActions} onSave={relic => {
          const current = inventoryRef.current;
          const existing = current.relics ?? [];
          return commit({ ...current, relics: existing.some(entry => entry.id === relic.id) ? existing.map(entry => entry.id === relic.id ? relic : entry) : [...existing, relic] });
        }} onNotesChange={(relic, notes) => {
          const current = inventoryRef.current;
          const relics = updateRelic(current.relics, relic, { notes });
          if (relics === current.relics) return;
          const updated = relics.find(item => relicKey(item) === relicKey(relic))!;
          if (commit({ ...current, relics }, 600)) noteChanged('relics', updated.id, `${relic.name} ${relic.refinement}`, notes);
        }} onDelete={id => commit({ ...inventoryRef.current, relics: (inventoryRef.current.relics ?? []).filter(relic => relic.id !== id) })} onQuantityChange={(relic, change) => {
          const current = inventoryRef.current;
          const relics = updateRelic(current.relics, relic, { quantityChange: change });
          if (relics !== current.relics) commit({ ...current, relics });
        }} /></Suspense>}
        {page === 'import' && <ScreenshotImport key={account.user?.id ?? 'guest'} initialSource={importRelics ? 'relics' : 'inventory'} existingRelics={relics} onRelicImport={(observations, files) => {
          const current = inventoryRef.current;
          const merged = mergeRelicObservations(current.relics, observations);
          if (!commit({ ...current, relics: merged.relics, imports: [{ id: crypto.randomUUID(), date: new Date().toISOString(), files, added: merged.added, updated: merged.updated }, ...current.imports].slice(0, 100) })) return false;
          navigate('relics');
          return true;
        }} onImport={(observations, files) => {
          const merged = mergeObservations(inventoryRef.current.items, observations);
          const next = { ...inventoryRef.current, items: merged.items, imports: [{ id: crypto.randomUUID(), date: new Date().toISOString(), files, added: merged.added, updated: merged.updated }, ...inventoryRef.current.imports].slice(0, 100) };
          if (!commit(next)) return false;
          navigate('weapons');
          return true;
        }} />}
        {page === 'droptables' && <DropTables actionsRef={dropActions} savedViews={account.profile.dropViews} savedViewsError={account.viewsError} canSave={Boolean(account.user)} onSaveViews={account.commitViews} onSignIn={() => setAccountOpen('login')} />}
        {page === 'modder' && <Suspense fallback={<p role="status" className="small muted">Loading Modding Calculator…</p>}><Modder key={account.user?.id ?? 'guest'} builds={account.profile.arsenals} onSave={account.commitArsenals} canSave={Boolean(account.user)} disabled={account.checking || account.unreadable} onSignIn={() => setAccountOpen('login')} /></Suspense>}
        {page === 'crafting' && <Suspense fallback={<p role="status" className="small muted">Loading crafting…</p>}><Crafting key={account.user?.id ?? 'guest'} plans={account.profile.craftingPlans} selected={craftingPlan} onSelect={setCraftingPlan} onSave={account.commitCrafting} canSave={Boolean(account.user)} disabled={account.checking || account.unreadable} onSignIn={() => setAccountOpen('login')} /></Suspense>}
      </main>
    </div>
    {editing && <EquipmentForm weapons={page === 'weapons'} item={editing === 'new' ? undefined : editing} existing={inventory.items} onClose={() => setEditing(null)} onSave={item => { const current = inventoryRef.current; const exists = current.items.some(entry => entry.id === item.id); if (commit({ ...current, items: exists ? current.items.map(entry => entry.id === item.id ? item : entry) : [...current.items, item] })) { setEditing(null); notify(`${item.name} ${exists ? 'updated' : 'added'}.`); return true; } return false; }} onDelete={id => { if (commit({ ...inventoryRef.current, items: inventoryRef.current.items.filter(item => item.id !== id) })) { setEditing(null); notify(`${page === 'weapons' ? 'Weapon' : 'Warframe'} entry removed.`); return true; } return false; }} />}
    {palette && <Commands commands={commands} destinations={destinations} onClose={() => setPalette(false)} />}
    {accountOpen && <AccountDialog initialMode={accountOpen} user={account.user} dirty={account.dirty} canIncludeCurrent={account.canIncludeCurrent} onAuthenticate={account.authenticate} onReplaceAccessKey={account.replaceAccessKey} onSignOut={account.signOut} onClose={() => setAccountOpen(null)} />}
    {shortcuts && <Dialog title="Keyboard shortcuts" onClose={() => setShortcuts(false)}><dl className="shortcut-list"><div><dt>Search and commands</dt><dd><kbd>Ctrl</kbd> + <kbd>K</kbd> / <kbd>⌘</kbd> + <kbd>K</kbd> / <kbd>Ctrl</kbd> + <kbd>Space</kbd></dd></div>{[...commands, ...destinations].map(command => <div key={command.label}><dt>{command.label}</dt><dd>{command.keys.map(key => <kbd key={key}>{key}</kbd>)}</dd></div>)}<div><dt>Column actions</dt><dd><kbd>Shift</kbd> + <kbd>F10</kbd></dd></div><div><dt>Inspect price history</dt><dd><kbd>←</kbd><kbd>→</kbd><kbd>Home</kbd><kbd>End</kbd></dd></div><div><dt>Pin price sample</dt><dd><kbd>Enter</kbd><kbd>Space</kbd></dd></div><div><dt>Zoom / pan price history</dt><dd><kbd>+</kbd><kbd>−</kbd> / <kbd>Shift</kbd> + <kbd>←</kbd><kbd>→</kbd></dd></div><div><dt>Move between {page === 'droptables' ? 'reward' : equipmentLabel} rows</dt><dd><kbd>↑</kbd><kbd>↓</kbd></dd></div><div><dt>Open selected {page === 'droptables' ? 'reward' : equipmentLabel}</dt><dd><kbd>Enter</kbd><kbd>Space</kbd></dd></div></dl></Dialog>}
  </div>;
}
