import type { Inventory } from './model.ts';
import type { DropView } from './dropTables.ts';
import { isDropView } from './dropTables.ts';
import { validateInventory } from './storage.ts';
import { validateCraftingPlans } from './crafting.ts';
import type { CraftingPlan } from './crafting.ts';
import { validateArsenals } from './modderModel.ts';
import type { ArsenalBuild } from './modderModel.ts';

export interface AccountUser { id: string; username: string }
export interface SavedDropView { name: string; view: DropView }
export interface AccountProfile { inventory: Inventory; dropViews: SavedDropView[]; craftingPlans: CraftingPlan[]; arsenals: ArsenalBuild[] }
export interface AccountSession { user: AccountUser; profile: AccountProfile; revision: number }
export const isAccessKey = (value: unknown): value is string => typeof value === 'string' && /^wft_[A-Za-z0-9_-]{43}$/.test(value);

export function accessKeyLink(base: string, username: string, accessKey: string): string {
  const url = new URL(base);
  url.search = '';
  url.hash = `sign-in?${new URLSearchParams({ username, key: accessKey })}`;
  return url.href;
}

export function readAccessKeyLink(hash: string): { username: string; accessKey: string } | null {
  if (hash !== '#sign-in' && !hash.startsWith('#sign-in?')) return null;
  const params = new URLSearchParams(hash.slice('#sign-in?'.length));
  const accessKey = params.get('key');
  if (!isAccessKey(accessKey) || params.getAll('key').length !== 1 || params.getAll('username').length !== 1) throw new Error('This sign-in link is incomplete or invalid. Use your saved key to sign in.');
  return { username: accountUsername(params.get('username')).username, accessKey };
}

export const emptyProfile = (): AccountProfile => ({ inventory: { version: 1, items: [], relics: [], imports: [], referenceNotice: false }, dropViews: [], craftingPlans: [], arsenals: [] });

export function validateDropViews(value: unknown): SavedDropView[] {
  if (!Array.isArray(value) || value.length > 200 || value.some(entry => !entry || typeof entry.name !== 'string' || !entry.name.trim() || entry.name.length > 100 || !isDropView(entry.view))) throw new Error('Saved views could not be read. The original data has been kept.');
  return value.map(entry => ({ name: entry.name, view: entry.view }));
}

export function validateProfile(value: unknown): AccountProfile {
  if (!value || typeof value !== 'object') throw new Error('Invalid saved profile.');
  const profile = value as AccountProfile;
  return { inventory: validateInventory(profile.inventory), dropViews: validateDropViews(profile.dropViews), craftingPlans: validateCraftingPlans(profile.craftingPlans === undefined ? [] : profile.craftingPlans), arsenals: validateArsenals(profile.arsenals === undefined ? [] : profile.arsenals) };
}

export function accountUsername(value: unknown): { username: string; key: string } {
  if (typeof value !== 'string') throw new Error('Enter your Warframe username.');
  const username = value.trim().normalize('NFKC');
  if (!/^[\p{L}\p{N}_.# -]{1,64}$/u.test(username)) throw new Error('Use your Warframe username, up to 64 characters. Letters, numbers, spaces, dots, underscores, hyphens, and # are supported.');
  return { username, key: username.toLowerCase() };
}
