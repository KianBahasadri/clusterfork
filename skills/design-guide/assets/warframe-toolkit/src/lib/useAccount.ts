import { useCallback, useEffect, useRef, useState } from 'react';
import { accountUsername, emptyProfile, isAccessKey, readAccessKeyLink, validateDropViews, validateProfile } from './account';
import type { AccountProfile, AccountSession, AccountUser, SavedDropView } from './account';
import type { Inventory } from './model';
import type { CraftingPlan } from './crafting';
import type { ArsenalBuild } from './modderModel';
import { parseBackup, STORAGE_KEY } from './storage';
import { initialInventory } from '../data/reference';
import { defaultArsenals, defaultCraftingPlans, defaultDropViews } from '../data/sampleFixtures';

export const LEGACY_VIEWS_KEY = 'warframe-toolkit.drop-views.v1';
type State = { user: AccountUser | null; profile: AccountProfile; revision: number; checking: boolean; dirty: boolean; saving: boolean; error: string; viewsError: string; unreadable: boolean };

function guestState(): State {
  const state: State = { user: null, profile: { inventory: initialInventory(), dropViews: defaultDropViews(), craftingPlans: defaultCraftingPlans(), arsenals: defaultArsenals() }, revision: 0, checking: false, dirty: false, saving: false, error: '', viewsError: '', unreadable: false };
  try { const raw = localStorage.getItem(STORAGE_KEY); if (raw) state.profile.inventory = parseBackup(raw); }
  catch { state.error = 'Your saved inventory could not be read. It has not been overwritten. Download the original data below.'; state.unreadable = true; }
  try { const raw = localStorage.getItem(LEGACY_VIEWS_KEY); if (raw) state.profile.dropViews = validateDropViews(JSON.parse(raw)); }
  catch { state.viewsError = 'Saved views could not be read. The original data has been kept.'; }
  return state;
}

async function request(path: string, body?: unknown, method = 'POST') {
  if (location.protocol === 'file:') throw new TypeError('Offline reference');
  const response = await fetch(`/api/account/${path}`, { method: body === undefined ? 'GET' : method, credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(20_000), headers: { 'Content-Type': 'application/json', 'X-Toolkit-Request': '1' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.error === 'string' ? result.error : 'Could not reach your account. Please try again.');
  return result;
}
function checkedSession(value: unknown): AccountSession {
  const session = value as AccountSession;
  if (!session?.user || typeof session.user.id !== 'string' || typeof session.user.username !== 'string' || !Number.isSafeInteger(session.revision) || session.revision < 0) throw new Error('Could not read the account response. Your current data has been kept.');
  return { user: session.user, profile: validateProfile(session.profile), revision: session.revision };
}
const message = (error: unknown) => error instanceof Error && error.name !== 'TypeError' && error.name !== 'TimeoutError' ? error.message : 'Could not reach your account. Your changes are still here; try saving again.';

export function useAccount() {
  const [state, setState] = useState(guestState);
  const current = useRef(state), guest = useRef(state);
  const authVersion = useRef(0), authenticating = useRef(false);
  const pending = useRef<Promise<void> | null>(null), timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const update = useCallback((patch: Partial<State>) => { current.current = { ...current.current, ...patch }; setState(current.current); }, []);

  const save = useCallback((): Promise<void> => {
    if (pending.current) return pending.current;
    const run = async () => {
      while (current.current.user && current.current.dirty) {
        const before = current.current;
        update({ saving: true, error: '' });
        try {
          const result = await request('profile', { accountId: before.user!.id, revision: before.revision, profile: before.profile }, 'PUT');
          if (!Number.isSafeInteger(result.revision) || result.revision !== before.revision + 1) throw new Error('Could not confirm your save. Your changes are still here.');
          update({ revision: result.revision, dirty: current.current.profile !== before.profile });
        } catch (error) { update({ error: message(error) }); break; }
      }
      update({ saving: false });
    };
    pending.current = run().finally(() => { pending.current = null; });
    return pending.current;
  }, [update]);

  const authenticate = useCallback(async (mode: 'login' | 'register', username: string, accessKey: string, includeCurrent: boolean): Promise<string | undefined> => {
    const before = current.current;
    if (authenticating.current) throw new Error('Sign-in is already in progress. Please wait.');
    if (before.user && before.dirty && accountUsername(username).key !== accountUsername(before.user.username).key) throw new Error('Save or download your changes before switching accounts.');
    authenticating.current = true;
    authVersion.current++;
    update({ checking: true });
    try {
      const response = await request(mode, { username, ...(mode === 'login' ? { accessKey } : includeCurrent ? { profile: before.profile } : {}) });
      const result = checkedSession(response);
      if (mode === 'register' && !isAccessKey(response.accessKey)) throw new Error('Could not read your new access key. Reopen the app and replace the key from Account.');
      if (!before.user) guest.current = before;
      if (before.user?.id === result.user.id && before.dirty) { update({ user: result.user, error: '', checking: false }); await save(); }
      else update({ ...result, checking: false, dirty: false, saving: false, error: '', viewsError: '', unreadable: false });
      return mode === 'register' ? response.accessKey : undefined;
    } finally { authenticating.current = false; update({ checking: false }); }
  }, [save, update]);

  useEffect(() => {
    let active = true;
    const consumeLink = () => {
      const hash = location.hash;
      if (hash !== '#sign-in' && !hash.startsWith('#sign-in?')) return false;
      // Fragments never reach the server. Remove credentials from this history entry,
      // even when sign-in fails; the user's saved bookmark remains reusable.
      history.replaceState(history.state, '', `${location.pathname}${location.search}#weapons`);
      authVersion.current++;
      try {
        const link = readAccessKeyLink(hash)!;
        void authenticate('login', link.username, link.accessKey, false).catch(error => { if (active) update({ error: message(error) }); });
      } catch (error) { update({ checking: false, error: message(error) }); }
      return true;
    };
    window.addEventListener('hashchange', consumeLink);
    queueMicrotask(() => { if (!active || consumeLink()) return;
      const version = authVersion.current;
      void request('session').then(result => {
      if (!active || version !== authVersion.current || !current.current.checking) return;
      if (result.user) update({ ...checkedSession(result), checking: false, error: '', viewsError: '', unreadable: false });
      else update({ checking: false });
    }).catch(() => { if (active && version === authVersion.current) update({ checking: false }); }); });
    return () => { active = false; window.removeEventListener('hashchange', consumeLink); };
  }, [authenticate, update]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => { if (current.current.dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', beforeUnload);
    return () => { window.removeEventListener('beforeunload', beforeUnload); clearTimeout(timer.current); };
  }, []);

  const commit = (profile: AccountProfile, saveDelay = 150): boolean => {
    if (current.current.checking) { update({ error: 'Please wait while sign-in is checked.' }); return false; }
    if (current.current.unreadable) { update({ error: 'Editing is disabled to preserve your unreadable saved inventory. Download the original data below.' }); return false; }
    update({ profile, dirty: true, error: '' });
    if (!current.current.user) guest.current = current.current;
    else { clearTimeout(timer.current); timer.current = setTimeout(() => void save(), saveDelay); }
    return true;
  };
  const replaceAccessKey = async (): Promise<string> => {
    const response = await request('access-key', { accountId: current.current.user?.id });
    if (!isAccessKey(response.accessKey)) throw new Error('Could not read your new access key. Try replacing it again.');
    return response.accessKey;
  };
  const signOut = async () => {
    clearTimeout(timer.current); await save();
    if (current.current.dirty) throw new Error('Your changes have not been saved. Try saving again before signing out.');
    await request('logout', { accountId: current.current.user?.id });
    update({ ...guest.current, checking: false, saving: false });
  };
  const reloadSaved = async () => {
    try {
      clearTimeout(timer.current); await pending.current;
      const before = current.current.profile;
      const result = checkedSession(await request('session'));
      if (result.user.id !== current.current.user?.id) throw new Error('Sign in to the same account to load its saved data.');
      if (before !== current.current.profile) throw new Error('You made new changes while loading. Your current changes have been kept.');
      update({ ...result, dirty: false, error: '', unreadable: false, viewsError: '' });
    } catch (error) { update({ error: message(error) }); }
  };
  return { ...state, authenticate, replaceAccessKey, signOut, retrySave: save, reloadSaved,
    commitInventory: (inventory: Inventory, saveDelay?: number) => commit({ ...current.current.profile, inventory }, saveDelay),
    commitViews: (dropViews: SavedDropView[]) => current.current.user ? commit({ ...current.current.profile, dropViews }) : false,
    commitCrafting: (craftingPlans: CraftingPlan[]) => commit({ ...current.current.profile, craftingPlans }),
    commitArsenals: (arsenals: ArsenalBuild[]) => commit({ ...current.current.profile, arsenals }, 400),
    canIncludeCurrent: !state.unreadable && !state.viewsError && Boolean(state.profile.inventory.items.length || state.profile.inventory.relics.length || state.profile.inventory.imports.length || state.profile.dropViews.length || state.profile.craftingPlans.length || state.profile.arsenals.length),
  };
}
