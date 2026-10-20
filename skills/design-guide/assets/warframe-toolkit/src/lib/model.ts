import type { Relic } from './relics.ts';

export const slots = ['Primary', 'Secondary', 'Melee', 'Warframe', 'Archgun', 'Archmelee', 'Companion', 'Exalted', 'Kitgun', 'Amp'] as const;
export type Slot = (typeof slots)[number];
export const variants = ['Base', 'Prime', 'MK1', 'Umbra', 'Wraith', 'Vandal', 'Prisma', 'Kuva', 'Tenet', 'Dex', 'Coda', 'Sancti', 'Secura', 'Synoid', 'Rakta', 'Vaykor', 'Telos', 'Mara', 'Other'] as const;
export type Variant = (typeof variants)[number];
export type Upgrade = 'Unknown' | 'Yes' | 'No';
export type Override = 'None' | 'Keep' | "Don't keep";

export interface CatalogItem {
  id: string;
  name: string;
  slot: Slot;
  variant: Variant;
  masteryReq: number;
  craftingTarget: string;
  hasPrimeVariant: boolean;
  wikiUrl: string;
  description?: string;
  image?: string;
  imageSource?: string;
  weaponType?: string;
  family?: string;
  tags?: string[];
  maxRank?: number;
  masterable?: boolean;
  vaulted?: boolean | null;
  tradable?: boolean;
  releaseDate?: string | null;
  polarities?: string[];
  craftingTargets?: { id: string; name: string; count: number }[];
  recipes?: Recipe[];
  incarnon?: IncarnonInfo;
  quests?: QuestSource[];
  acquisition?: string[];
  market?: { credits: number | null; platinum: number | null; blueprintCredits: number | null };
  stats?: Record<string, number | string>;
  damage?: Record<string, number>;
  attacks?: { name: string; stats: Record<string, number | string>; damage: Record<string, number> }[];
  modular?: string | null;
  sourceUrls?: string[];
}

export function maximumRank(item: Pick<CatalogItem, 'slot'> & Partial<Pick<CatalogItem, 'weaponType' | 'maxRank'>>): number | undefined {
  // Necramechs are grouped with Warframes by the source, but have their own rank cap.
  // https://www.warframe.com/en/patch-notes/xbox/29-5-5
  return item.slot === 'Warframe' ? item.weaponType === 'Necramech' ? 40 : 30 : item.maxRank;
}

export interface Recipe {
  id: string;
  name: string;
  credits: number | null;
  seconds: number | null;
  quantity: number;
  ingredients: { id: string; name: string; count: number; equipment: boolean; recipes?: Recipe[] }[];
}

export interface IncarnonInfo {
  type: 'none' | 'native' | 'genesis';
  name: string;
  description: string;
  installation: Recipe | null;
  wikiUrl: string;
}

export interface QuestSource {
  name: string;
  kind: 'Weapon reward' | 'Blueprint reward';
  url: string;
}

export interface Equipment {
  id: string;
  name: string;
  slot: Slot;
  owned: boolean;
  mastered: boolean;
  dontKeep: boolean;
  craftingIngredient: boolean;
  craftingTarget: string;
  variant: Variant;
  incarnon: boolean;
  questWeapon: boolean;
  upgraded: Upgrade;
  incarnonInstalled?: Upgrade;
  helminth?: Upgrade;
  powerful: boolean;
  hasPrimeVariant: boolean;
  override: Override;
  rank: number | null;
  notes: string;
  source: string;
  updatedAt: string;
}

export interface ImportLog {
  id: string;
  date: string;
  files: string[];
  added: number;
  updated: number;
}

export interface Inventory {
  version: 1;
  items: Equipment[];
  relics: Relic[];
  imports: ImportLog[];
  referenceNotice: boolean;
}

export function normalizeName(value: string): string {
  return value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

export function createEquipment(item: Pick<CatalogItem, 'name' | 'slot' | 'variant'> & Partial<CatalogItem>, overrides: Partial<Equipment> = {}): Equipment {
  return {
    id: overrides.id ?? crypto.randomUUID(), name: item.name, slot: item.slot, variant: item.variant,
    owned: true, mastered: false, dontKeep: false,
    craftingIngredient: Boolean(item.craftingTarget), craftingTarget: item.craftingTarget || '',
    incarnon: Boolean(item.incarnon && item.incarnon.type !== 'none'), questWeapon: Boolean(item.quests?.length), upgraded: 'Unknown', powerful: false,
    hasPrimeVariant: item.hasPrimeVariant || false, override: 'None', rank: null, notes: '',
    source: 'Added manually', updatedAt: overrides.updatedAt ?? new Date().toISOString(), ...overrides,
  };
}

// Only affirmative observations are merged. An absent checkmark in a screenshot
// is not evidence that a previously owned or mastered item should be cleared.
export function mergeObservations(existing: Equipment[], observations: Equipment[]): { items: Equipment[]; added: number; updated: number } {
  const result = existing.map(item => ({ ...item }));
  const indices = new Map(result.map((item, i) => [normalizeName(item.name), i]));
  const existingIds = new Set(existing.map(item => item.id));
  let added = 0;
  const updated = new Set<string>();
  for (const incoming of observations) {
    const key = normalizeName(incoming.name);
    const index = indices.get(key);
    if (index === undefined) {
      indices.set(key, result.length);
      result.push({ ...incoming });
      added++;
    } else {
      const current = result[index];
      const next = {
        ...current, owned: current.owned || incoming.owned, mastered: current.mastered || incoming.mastered,
        // Ranks can decrease after Forma; only replace when a new rank was read.
        rank: incoming.rank ?? current.rank,
        source: incoming.source, updatedAt: incoming.updatedAt,
      };
      result[index] = next;
      if (existingIds.has(current.id)) updated.add(current.id);
    }
  }
  return { items: result, added, updated: updated.size };
}
