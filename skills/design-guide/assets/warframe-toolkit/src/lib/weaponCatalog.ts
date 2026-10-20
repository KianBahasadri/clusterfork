import { createEquipment, normalizeName } from './model.ts';
import type { CatalogItem, Equipment } from './model.ts';
import { calculateRecommendation, helminthStatus, recommendationFor } from './recommendations.ts';
import type { Recommendation } from './recommendations.ts';

export interface EquipmentRow extends Equipment {
  catalogItem?: CatalogItem;
  saved?: Equipment;
  recommendation?: Recommendation;
}

export const catalogFields = ['slot', 'variant', 'craftingIngredient', 'craftingTarget', 'incarnon', 'questWeapon', 'hasPrimeVariant'] as const;
export type BooleanField = 'owned' | 'mastered' | 'dontKeep' | 'powerful' | 'craftingIngredient' | 'incarnon' | 'questWeapon' | 'hasPrimeVariant';

// Catalog facts are a view. Never rewrite historical planning fields in a save
// merely because the database was refreshed or a row was displayed.
export function withCatalogFacts(item: Equipment, entry?: CatalogItem): Equipment {
  if (!entry) return { ...item };
  return {
    ...item, slot: entry.slot, variant: entry.variant,
    craftingIngredient: Boolean(entry.craftingTarget), craftingTarget: entry.craftingTarget,
    incarnon: Boolean(entry.incarnon && entry.incarnon.type !== 'none'),
    questWeapon: Boolean(entry.quests?.length), hasPrimeVariant: entry.hasPrimeVariant,
  };
}

export function equipmentRows(items: Equipment[], database: CatalogItem[], includeUntracked = false): EquipmentRow[] {
  const byName = new Map(database.map(entry => [normalizeName(entry.name), entry]));
  const savedNames = new Set(items.map(item => normalizeName(item.name)));
  const rows: EquipmentRow[] = items.map(saved => {
    const catalogItem = byName.get(normalizeName(saved.name));
    return { ...withCatalogFacts(saved, catalogItem), saved, catalogItem };
  });
  if (includeUntracked) for (const catalogItem of database) {
    if (!savedNames.has(normalizeName(catalogItem.name))) rows.push({
      ...createEquipment(catalogItem, { id: `catalog:${catalogItem.id}`, owned: false, source: catalogItem.slot === 'Warframe' ? 'Warframe catalog' : 'Weapon catalog', updatedAt: '' }),
      saved: undefined, catalogItem,
    });
  }
  const inventory = new Map(items.map(item => [normalizeName(item.name), item]));
  return rows.map(row => ({ ...row, recommendation: calculateRecommendation(row, inventory) })).sort((a, b) => a.name.localeCompare(b.name));
}

export function editableRecord(row: EquipmentRow): Equipment {
  return row.saved ? { ...row.saved } : createEquipment(row.catalogItem!, { owned: false, source: row.slot === 'Warframe' ? 'Warframe catalog' : 'Weapon catalog' });
}

export function toggleEquipment(items: Equipment[], row: EquipmentRow, field: BooleanField): Equipment[] {
  if (row.catalogItem && catalogFields.some(key => key === field)) return items;
  const current = items.find(item => item.id === row.saved?.id || normalizeName(item.name) === normalizeName(row.name));
  const next = { ...(current || editableRecord(row)), [field]: !(current || row)[field], updatedAt: new Date().toISOString() };
  return current ? items.map(item => item.id === current.id ? next : item) : [...items, next];
}

export interface WeaponFilters {
  ownedOnly: boolean;
  slot: string;
  variant: string;
  weaponType: string;
  mastery: string;
  incarnon: string;
  quest: string;
  ingredient: string;
  recipe: string;
  prime: string;
  vaulted: string;
  keep: string;
  maxMr: string;
  helminth: string;
}

export const defaultWeaponFilters: WeaponFilters = {
  ownedOnly: true, slot: '', variant: '', weaponType: '', mastery: '', incarnon: '',
  quest: '', ingredient: '', recipe: '', prime: '', vaulted: '', keep: '', maxMr: '', helminth: '',
};
const booleanMatch = (value: boolean | undefined | null, filter: string) => !filter || (filter === 'yes' ? value === true : value === false);

export function filterEquipment(items: EquipmentRow[], search: string, filters: WeaponFilters): EquipmentRow[] {
  const words = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return items.filter(item => {
    const entry = item.catalogItem;
    const text = [item.name, item.slot === 'Warframe' ? '' : item.craftingTarget, item.notes, entry?.weaponType, entry?.tags?.join(' '), entry?.quests?.map(quest => quest.name).join(' ')].join(' ').toLowerCase();
    return words.every(word => text.includes(word))
      && (!filters.ownedOnly || item.owned)
      && (!filters.slot || item.slot === filters.slot)
      && (!filters.variant || item.variant === filters.variant)
      && (!filters.weaponType || (entry?.weaponType ?? (item.slot === 'Warframe' ? 'Warframe' : '')) === filters.weaponType)
      && booleanMatch(item.mastered, filters.mastery)
      && (!filters.incarnon || (filters.incarnon === 'yes' ? item.incarnon : filters.incarnon === 'none' ? !item.incarnon : entry?.incarnon?.type === filters.incarnon))
      && booleanMatch(item.questWeapon, filters.quest)
      && booleanMatch(item.craftingIngredient, filters.ingredient)
      && booleanMatch(entry ? Boolean(entry.recipes?.length) : undefined, filters.recipe)
      && booleanMatch(item.hasPrimeVariant, filters.prime)
      && booleanMatch(entry?.vaulted, filters.vaulted)
      && (!filters.keep || recommendationFor(item).action === filters.keep)
      && (!filters.helminth || helminthStatus(item) === filters.helminth)
      && (filters.maxMr === '' || entry !== undefined && entry.masteryReq <= Number(filters.maxMr));
  });
}
