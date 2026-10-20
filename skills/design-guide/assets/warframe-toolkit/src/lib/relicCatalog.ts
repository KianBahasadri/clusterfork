import { createRelic, MAX_RELIC_QUANTITY, relicKey } from './relics.ts';
import type { Relic, Refinement } from './relics.ts';

export interface RelicCatalogItem {
  name: string;
  refinement: Refinement;
  uniqueNames: string[];
  description: string;
  image: string;
  imageSource: string;
  rewards: { id: string; name: string; quantity: number; rarity: string; chance: number | null }[];
  sources: { source: string; rotation: string; stage: string; chance: number | null; tableChance: number; anchor: string }[];
}
export interface RelicRow extends Relic { catalogItem?: RelicCatalogItem; saved?: Relic }

export function relicRows(saved: Relic[], catalog: RelicCatalogItem[]): RelicRow[] {
  const entries = new Map(catalog.map(item => [relicKey(item), item]));
  const tracked = new Set(saved.map(relicKey));
  return [
    ...saved.map(item => ({ ...item, saved: item, catalogItem: entries.get(relicKey(item)) })),
    ...catalog.filter(item => !tracked.has(relicKey(item))).map(item => ({ id: `catalog:${relicKey(item)}`, name: item.name, refinement: item.refinement, quantity: 0, notes: '', updatedAt: '', catalogItem: item })),
  ];
}

export function editableRelic(row: RelicRow): Relic {
  return row.saved ? { ...row.saved } : { ...createRelic(), name: row.name, refinement: row.refinement, quantity: row.quantity, notes: row.notes };
}

// Resolve the current stack again so rapid edits preserve earlier clicks/notes.
export function updateRelic(saved: Relic[], row: RelicRow, update: { notes: string } | { quantityChange: number }): Relic[] {
  const previous = saved.find(item => item.id === row.id || relicKey(item) === relicKey(row));
  const base = previous ?? editableRelic(row);
  const notes = 'notes' in update ? update.notes : base.notes;
  const quantity = 'quantityChange' in update ? Math.max(0, Math.min(MAX_RELIC_QUANTITY, base.quantity + update.quantityChange)) : base.quantity;
  if (notes === base.notes && quantity === base.quantity) return saved;
  const next = { ...base, notes, quantity, updatedAt: new Date().toISOString() };
  return previous ? saved.map(item => item.id === previous.id ? next : item) : [...saved, next];
}
