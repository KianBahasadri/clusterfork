import { slots, variants } from './model.ts';
import type { Equipment, Inventory } from './model.ts';
import { parseRelicName, validateRelics } from './relics.ts';
import type { Relic } from './relics.ts';
import { helminthStatus, recommendationFor } from './recommendations.ts';
import type { EquipmentRow } from './weaponCatalog.ts';

export const STORAGE_KEY = 'warframe-toolkit.inventory.v1';
const booleanFields = ['owned', 'mastered', 'dontKeep', 'craftingIngredient', 'incarnon', 'questWeapon', 'powerful', 'hasPrimeVariant'] as const;
const stringFields = ['id', 'name', 'craftingTarget', 'notes', 'source', 'updatedAt'] as const;

export function validateInventory(value: unknown): Inventory {
  if (!value || typeof value !== 'object') throw new Error('This file is not an inventory backup.');
  const data = value as Partial<Inventory>;
  if (data.version !== 1 || !Array.isArray(data.items) || data.items.length > 10000) throw new Error('Unsupported or invalid backup format.');
  const ids = new Set<string>();
  const names = new Set<string>();
  for (const raw of data.items) {
    if (!raw || typeof raw !== 'object') throw new Error('The backup contains an invalid equipment row.');
    const item = raw as Equipment;
    if (stringFields.some(key => typeof item[key] !== 'string' || item[key].length > 10000) || !item.id || !item.name.trim()) throw new Error('An equipment row has missing or invalid text fields.');
    if (booleanFields.some(key => typeof item[key] !== 'boolean')) throw new Error(`Invalid checkbox values for ${item.name}.`);
    if (!slots.includes(item.slot) || !variants.includes(item.variant) || !['Unknown', 'Yes', 'No'].includes(item.upgraded) || !['None', 'Keep', "Don't keep"].includes(item.override)) throw new Error(`Invalid equipment settings for ${item.name}.`);
    for (const field of ['incarnonInstalled', 'helminth'] as const) {
      if (item[field] !== undefined && !['Unknown', 'Yes', 'No'].includes(item[field])) throw new Error(`Invalid ${field} setting for ${item.name}.`);
    }
    if (item.rank !== null && (!Number.isInteger(item.rank) || item.rank < 0 || item.rank > 40)) throw new Error(`Invalid rank for ${item.name}.`);
    if (ids.has(item.id) || names.has(item.name.toLowerCase().replace(/[^a-z0-9]/g, ''))) throw new Error('The backup contains duplicate equipment.');
    ids.add(item.id); names.add(item.name.toLowerCase().replace(/[^a-z0-9]/g, ''));
  }
  if (!Array.isArray(data.imports) || data.imports.length > 1000) throw new Error('The import history is invalid.');
  for (const log of data.imports) {
    if (!log || typeof log.id !== 'string' || typeof log.date !== 'string' || Number.isNaN(Date.parse(log.date)) || !Array.isArray(log.files) || log.files.some((file: unknown) => typeof file !== 'string') || !Number.isInteger(log.added) || !Number.isInteger(log.updated) || log.added < 0 || log.updated < 0) throw new Error('The import history is invalid.');
  }
  return { version: 1, items: data.items, relics: validateRelics(data.relics), imports: data.imports, referenceNotice: data.referenceNotice === true };
}

export function parseBackup(text: string): Inventory {
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch { throw new Error('Could not read this JSON file. Choose a Grakata backup.'); }
  return validateInventory(parsed);
}

const csvColumns: [string, keyof Equipment][] = [
  ['Owned', 'owned'], ['Weapon Name', 'name'], ["Don't Keep", 'dontKeep'],
  ['Crafting Ingredient?', 'craftingIngredient'], ['Crafting Target', 'craftingTarget'], ['Variant', 'variant'],
  ['Incarnon', 'incarnon'], ['Quest Weapon?', 'questWeapon'], ['Upgraded', 'upgraded'],
  ['Mastered', 'mastered'], ['Meta / Powerful', 'powerful'], ['Slot', 'slot'],
  ['Has Prime Variant', 'hasPrimeVariant'], ['Override', 'override'], ['Rank', 'rank'], ['Notes', 'notes'],
  ['Incarnon Installed', 'incarnonInstalled'], ['Helminth Ability Unlocked', 'helminth'],
];

function csvCell(value: unknown): string {
  let text = typeof value === 'boolean' ? (value ? 'TRUE' : 'FALSE') : String(value ?? '');
  // Keep user notes and names from being evaluated as spreadsheet formulas.
  if (/^[\s]*[=+@-]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

export function inventoryCsv(items: Equipment[]): string {
  return '\uFEFF' + [csvColumns.map(([label]) => csvCell(label)).join(','), ...items.map(item => csvColumns.map(([, key]) => csvCell(item[key])).join(','))].join('\r\n');
}

export function warframesCsv(items: EquipmentRow[]): string {
  const rows = [['Warframe', 'Type', 'Owned', 'Rank', 'Mastered', 'Variant', 'Upgraded', 'Helminth Ability Unlocked', 'Recommended Action', 'Favorite', 'Quest Reward', 'Has Prime Variant', 'Notes'],
    ...items.filter(item => item.slot === 'Warframe').map(item => [item.name, item.catalogItem?.weaponType ?? 'Warframe', item.owned, item.rank, item.mastered, item.variant, item.upgraded, helminthStatus(item), recommendationFor(item).action, item.powerful, item.questWeapon, item.hasPrimeVariant, item.notes])];
  return '\uFEFF' + rows.map(row => row.map(csvCell).join(',')).join('\r\n');
}

export function relicsCsv(relics: Relic[]): string {
  const rows = [['Relic', 'Era', 'Refinement', 'Quantity', 'Notes'], ...relics.map(relic => [relic.name, parseRelicName(relic.name)?.era || '', relic.refinement, relic.quantity, relic.notes])];
  return '\uFEFF' + rows.map(row => row.map(csvCell).join(',')).join('\r\n');
}

export function downloadFile(filename: string, contents: string, type: string): void {
  const url = URL.createObjectURL(new Blob([contents], { type }));
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
