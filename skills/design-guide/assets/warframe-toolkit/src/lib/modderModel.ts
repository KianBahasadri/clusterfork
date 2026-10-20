// Persistence stays independent of the catalog and calculator. Unknown source IDs
// survive refreshes and are reported by the workshop instead of being discarded.
export const arsenalSlots = ['Warframe', 'Primary', 'Secondary', 'Melee'] as const;
export type ArsenalSlot = typeof arsenalSlots[number];
export const polarities = ['', 'madurai', 'vazarin', 'naramon', 'zenurik', 'unairu', 'penjaga', 'umbra', 'universal'] as const;
export type Polarity = typeof polarities[number];
export interface UpgradeSlot { id: string; rank: number; polarity: Polarity; enabled: boolean; stacks: number }
export interface EquipmentBuild {
  id: string; mods: UpgradeSlot[]; exilus: UpgradeSlot; special: UpgradeSlot; arcanes: UpgradeSlot[];
  doubled: boolean; capacity: number; attack: number; overrides: Record<string, number>;
}
export interface ModderConditions {
  combo: number; armor: number; strip: number; faction: string;
  roar: boolean; roarBase: number; abilityCost: number; abilityDrain: number;
}
export interface ArsenalBuild {
  id: string; name: string; equipment: Record<ArsenalSlot, EquipmentBuild>; conditions: ModderConditions; notes: string;
}
export const emptyUpgrade = (polarity: Polarity = ''): UpgradeSlot => ({ id: '', rank: 0, polarity, enabled: true, stacks: 0 });
export const emptyEquipment = (slot: ArsenalSlot, id = ''): EquipmentBuild => ({
  id, mods: Array.from({ length: 8 }, () => emptyUpgrade()), exilus: emptyUpgrade(), special: emptyUpgrade(),
  arcanes: Array.from({ length: slot === 'Warframe' ? 2 : 1 }, () => emptyUpgrade()), doubled: true, capacity: 30, attack: -1, overrides: {},
});
export const newArsenal = (): ArsenalBuild => ({
  id: crypto.randomUUID(), name: 'Untitled arsenal',
  equipment: Object.fromEntries(arsenalSlots.map(slot => [slot, emptyEquipment(slot)])) as Record<ArsenalSlot, EquipmentBuild>,
  conditions: { combo: 1, armor: 0, strip: 0, faction: 'None', roar: false, roarBase: 30, abilityCost: 25, abilityDrain: 5 }, notes: '',
});
export const overrideKeys = ['health', 'shield', 'armor', 'energy', 'criticalChance', 'criticalMultiplier', 'procChance', 'fireRate', 'magazineSize', 'reloadTime', 'multishot'] as const;

export function validateArsenals(value: unknown): ArsenalBuild[] {
  const fail = (): never => { throw new Error('Modding Calculator builds could not be read. The original data has been kept.'); };
  const object = (v: unknown): v is Record<string, unknown> => Boolean(v && typeof v === 'object' && !Array.isArray(v));
  const string = (v: unknown, max: number): v is string => typeof v === 'string' && v.length <= max;
  const number = (v: unknown, min: number, max: number, integer = false): v is number => typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max && (!integer || Number.isInteger(v));
  const upgrade = (v: unknown) => object(v) && string(v.id, 500) && number(v.rank, 0, 20, true) && polarities.includes(v.polarity as Polarity) && typeof v.enabled === 'boolean' && number(v.stacks, 0, 1000, true);
  if (!Array.isArray(value) || value.length > 100) return fail();
  const ids = new Set<string>();
  for (const build of value) {
    if (!object(build) || !string(build.id, 100) || !build.id || ids.has(build.id) || !string(build.name, 100) || !build.name.trim() || !string(build.notes, 10000) || !object(build.equipment) || !object(build.conditions)) return fail();
    ids.add(build.id);
    for (const slot of arsenalSlots) {
      const e = build.equipment[slot];
      if (!object(e) || !string(e.id, 500) || !Array.isArray(e.mods) || e.mods.length !== 8 || !e.mods.every(upgrade) || !upgrade(e.exilus) || !upgrade(e.special) || !Array.isArray(e.arcanes) || e.arcanes.length !== (slot === 'Warframe' ? 2 : 1) || !e.arcanes.every(upgrade) || typeof e.doubled !== 'boolean' || !number(e.capacity, 0, 40, true) || !number(e.attack, -1, 100, true) || !object(e.overrides)) return fail();
      if (Object.entries(e.overrides).some(([key, v]) => !overrideKeys.includes(key as typeof overrideKeys[number]) || !number(v, 0, 100000))) return fail();
    }
    const c = build.conditions;
    if (!number(c.combo, 1, 12, true) || !number(c.armor, 0, 100000) || !number(c.strip, 0, 100) || !['None', 'Grineer', 'Corpus', 'Infested', 'Corrupted', 'Sentient', 'Murmur'].includes(c.faction as string) || typeof c.roar !== 'boolean' || !number(c.roarBase, 0, 500) || !number(c.abilityCost, 0, 10000) || !number(c.abilityDrain, 0, 10000)) return fail();
  }
  return structuredClone(value) as ArsenalBuild[];
}
