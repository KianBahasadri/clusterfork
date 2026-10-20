export const relicEras = ['Lith', 'Meso', 'Neo', 'Axi', 'Requiem', 'Vanguard'] as const;
export const refinements = ['Intact', 'Exceptional', 'Flawless', 'Radiant', 'Unrefined'] as const;
export type RelicEra = (typeof relicEras)[number];
export type Refinement = (typeof refinements)[number];
export const MAX_RELIC_QUANTITY = 999999;

export interface Relic {
  id: string;
  name: string;
  refinement: Refinement;
  quantity: number;
  notes: string;
  updatedAt: string;
}

export function parseRelicName(value: string): { name: string; era: RelicEra } | null {
  const parts = value.trim().replace(/\s+relic$/i, '').split(/\s+/);
  const era = relicEras.find(era => era.toLowerCase() === parts[0]?.toLowerCase());
  if (!era || parts.length !== 2 || !/^[a-z0-9]{1,10}$/i.test(parts[1])) return null;
  return { name: `${era} ${era === 'Requiem' && parts[1].toLowerCase() === 'eterna' ? 'Eterna' : parts[1].toUpperCase()}`, era };
}

export function relicKey(relic: Pick<Relic, 'name' | 'refinement'>): string {
  return `${relic.name.toLowerCase().trim()}|${relic.refinement}`;
}

export function createRelic(): Relic {
  return { id: crypto.randomUUID(), name: '', refinement: 'Intact', quantity: 1, notes: '', updatedAt: new Date().toISOString() };
}

export function validateRelics(value: unknown): Relic[] {
  // Older inventories did not have a relic collection.
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 10000) throw new Error('The relic collection is invalid.');
  const ids = new Set<string>();
  const keys = new Set<string>();
  return value.map((raw: unknown) => {
    if (!raw || typeof raw !== 'object') throw new Error('The backup contains an invalid relic.');
    const relic = raw as Relic;
    const parsed = typeof relic.name === 'string' && parseRelicName(relic.name);
    if (typeof relic.id !== 'string' || !relic.id || relic.id.length > 100 || !parsed || !refinements.includes(relic.refinement) || typeof relic.notes !== 'string' || relic.notes.length > 5000 || typeof relic.updatedAt !== 'string' || Number.isNaN(Date.parse(relic.updatedAt))) throw new Error('A relic has missing or invalid fields.');
    if (!Number.isInteger(relic.quantity) || relic.quantity < 0 || relic.quantity > MAX_RELIC_QUANTITY) throw new Error(`Invalid quantity for ${parsed.name}.`);
    const normalized = { ...relic, name: parsed.name };
    const key = relicKey(normalized);
    if (ids.has(relic.id) || keys.has(key)) throw new Error('The backup contains duplicate relic stacks.');
    ids.add(relic.id); keys.add(key);
    return normalized;
  });
}
