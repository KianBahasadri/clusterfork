import { compatible, modFamily, upgrades } from './modder.ts';
import type { UpgradeKind } from './modder.ts';
import { emptyUpgrade } from './modderModel.ts';
import type { EquipmentBuild, UpgradeSlot } from './modderModel.ts';
import type { CatalogItem } from './model.ts';

export type UpgradeTarget = { kind: UpgradeKind; index: number };
export type UpgradeSource = { id: string; from?: UpgradeTarget };

function upgradeAt(build: EquipmentBuild, target: UpgradeTarget): UpgradeSlot | undefined {
  if (!Number.isInteger(target.index) || target.index < 0) return undefined;
  return target.kind === 'mod' ? build.mods[target.index] : target.kind === 'arcane' ? build.arcanes[target.index] : target.index === 0 ? build[target.kind] : undefined;
}

function writeUpgrade(build: EquipmentBuild, target: UpgradeTarget, value: UpgradeSlot): EquipmentBuild {
  return target.kind === 'mod' ? { ...build, mods: build.mods.map((mod, index) => index === target.index ? value : mod) }
    : target.kind === 'arcane' ? { ...build, arcanes: build.arcanes.map((arcane, index) => index === target.index ? value : arcane) }
    : { ...build, [target.kind]: value };
}

// A library mod replaces the destination at maximum rank. An equipped mod swaps
// with the destination, carrying its settings while each slot keeps its polarity.
// Return null for invalid placements so drag previews and drops use the same rules.
export function placeUpgrade(build: EquipmentBuild, item: CatalogItem | undefined, source: UpgradeSource, target: UpgradeTarget): EquipmentBuild | null {
  const destination = upgradeAt(build, target), origin = source.from && upgradeAt(build, source.from);
  const mod = upgrades.get(source.id);
  if (!item || item.id !== build.id || !mod || !destination || !compatible(mod, item, target.kind)) return null;
  if (source.from && (!origin || origin.id !== source.id || source.from.kind === target.kind && source.from.index === target.index)) return null;
  if (source.from && destination.id) {
    const displaced = upgrades.get(destination.id);
    if (!displaced || !compatible(displaced, item, source.from.kind)) return null;
  }
  const others = target.kind === 'arcane' ? build.arcanes : [...build.mods, build.exilus, build.special];
  if (others.some(value => {
    if (value === destination || value === origin) return false;
    const other = upgrades.get(value.id);
    return other && (target.kind === 'arcane' ? other.id === mod.id : modFamily(other) === modFamily(mod));
  })) return null;
  let next = writeUpgrade(build, target, { ...(origin || { ...emptyUpgrade(), id: mod.id, rank: mod.maxRank }), polarity: destination.polarity });
  if (source.from && origin) next = writeUpgrade(next, source.from, destination.id ? { ...destination, polarity: origin.polarity } : emptyUpgrade(origin.polarity));
  return next;
}
