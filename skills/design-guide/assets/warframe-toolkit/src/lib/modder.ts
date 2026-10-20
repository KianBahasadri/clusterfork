import snapshot from '../data/modder.json' with { type: 'json' };
import type { CatalogItem } from './model.ts';
import { emptyEquipment, polarities } from './modderModel.ts';
import type { ArsenalBuild, ArsenalSlot, EquipmentBuild, ModderConditions, Polarity, UpgradeSlot } from './modderModel.ts';

export interface Upgrade {
  id: string; name: string; type: string; compat: string; polarity: string; drain: number;
  rarity?: string; image?: string;
  maxRank: number; exilus: boolean; set: boolean; ranks: string[][]; url: string;
}
export const modderData = snapshot;
export const mods: Upgrade[] = snapshot.mods;
export const arcanes: Upgrade[] = snapshot.arcanes;
export const upgrades = new Map([...mods, ...arcanes].map(mod => [mod.id, mod]));
export const frames = new Map(snapshot.frames.map(frame => [frame.id, frame]));
export type UpgradeKind = 'mod' | 'exilus' | 'special' | 'arcane';
export const fmt = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 2 });
const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);
const nativePolarity = (value: string | undefined): Polarity => polarities.includes(value as Polarity) ? value as Polarity : '';

export function equipItem(item: CatalogItem, slot: ArsenalSlot): EquipmentBuild {
  const build = emptyEquipment(slot, item.id);
  const normal = (item.polarities ?? []).filter(polarity => !polarity.includes(':'));
  build.mods.forEach((mod, i) => { mod.polarity = nativePolarity(normal[i]); });
  build.exilus.polarity = nativePolarity(item.polarities?.find(value => value.startsWith('Exilus: '))?.slice(8));
  build.special.polarity = slot === 'Warframe' ? nativePolarity(frames.get(item.id)?.aura) : nativePolarity(item.polarities?.find(value => value.startsWith('Stance: '))?.slice(8));
  return build;
}

export function weaponSourceStats(item: CatalogItem, attack: number): Record<string, number | string | undefined> {
  const values: Record<string, number | string | undefined> = { ...item.stats };
  const alternate = attack >= 0 ? item.attacks?.[attack] : undefined;
  if (alternate) for (const [key, source, divisor] of [['criticalChance', 'crit_chance', 100], ['criticalMultiplier', 'crit_mult', 1], ['procChance', 'status_chance', 100], ['fireRate', 'speed', 1]] as const) {
    const value = alternate.stats[source];
    values[key] = typeof value === 'number' ? value / divisor : undefined;
  }
  return values;
}

export function compatible(mod: Upgrade, item: CatalogItem, kind: UpgradeKind): boolean {
  const slot = item.slot;
  if (kind === 'arcane') return mod.type === `${slot} Arcane` || slot === 'Primary' && (item.weaponType === 'Shotgun' && mod.type === 'Shotgun Arcane' || item.weaponType === 'Bow' && mod.type === 'Bow Arcane');
  const aura = mod.compat === 'AURA', stance = mod.type === 'Stance Mod';
  if (kind === 'special') return slot === 'Warframe' ? aura : slot === 'Melee' && stance && mod.compat.toLowerCase() === item.weaponType?.toLowerCase();
  if (aura || stance || kind === 'exilus' && !mod.exilus) return false;
  const c = mod.compat.toLowerCase();
  if (c === 'warframe') return slot === 'Warframe';
  if (c === 'primary') return slot === 'Primary';
  if (c === 'rifle') return slot === 'Primary' && item.weaponType !== 'Shotgun';
  if (c === 'shotgun') return slot === 'Primary' && item.weaponType === 'Shotgun';
  if (c === 'pistol') return slot === 'Secondary';
  if (c === 'melee') return slot === 'Melee';
  if (c === 'sniper' || c === 'bow' || c === 'assault rifle') return slot === 'Primary' && (item.weaponType?.toLowerCase() === c || c === 'assault rifle' && item.weaponType === 'Rifle');
  // Specific augments are deliberately exact: Hek's augment cannot be used on Vaykor Hek.
  if (slot === 'Warframe') return c === item.name.replace(/ Prime$/, '').toLowerCase();
  return c === item.name.toLowerCase() || slot === 'Melee' && c === item.weaponType?.toLowerCase();
}

export function modFamily(mod: Upgrade): string {
  const name = mod.name.replace(/^(Primed|Amalgam|Archon|Umbral|Sacrificial) /, '');
  const groups = [
    ['Point Strike', 'Critical Delay'], ['Blunderbuss', 'Critical Deceleration'], ['Pistol Gambit', 'Creeping Bullseye'],
    ['Split Chamber', 'Galvanized Chamber'], ['Hell’s Chamber', "Hell's Chamber", 'Galvanized Hell'],
    ['Barrel Diffusion', 'Galvanized Diffusion'], ['Pressure Point', 'Pressure'], ['True Steel', 'Steel'], ['Steel Fiber', 'Fiber'],
    ['Rifle Aptitude', 'Galvanized Aptitude'], ['Shotgun Savvy', 'Galvanized Savvy'], ['Sure Shot', 'Galvanized Shot'],
    ['Argon Scope', 'Galvanized Scope'], ['Hydraulic Crosshairs', 'Galvanized Crosshairs'], ['Fury', 'Berserker Fury'],
  ];
  return groups.find(group => group.includes(name))?.[0] ?? name;
}

export function slotDrain(slot: UpgradeSlot, special = false): number {
  const mod = upgrades.get(slot.id);
  if (!mod) return 0;
  const base = special ? Math.abs(mod.drain) + slot.rank : mod.drain + slot.rank;
  const match = slot.polarity === mod.polarity || slot.polarity === 'universal';
  return special ? !slot.polarity ? base : match ? base * 2 : Math.floor(base * .8)
    : !slot.polarity ? base : match ? Math.ceil(base / 2) : Math.ceil(base * 1.25);
}
export function capacity(build: EquipmentBuild) {
  const used = sum([...build.mods, build.exilus].map(slot => slotDrain(slot)));
  const bonus = slotDrain(build.special, true);
  return { used, bonus, total: build.capacity * (build.doubled ? 2 : 1) + bonus };
}

interface Effect { stat: string; value: number; source: string; target?: string }
export interface UpgradeEffects { effects: Effect[]; omitted: string[]; maxStacks: number }
const statNames: Record<string, string> = {
  Damage: 'damage', 'Melee Damage': 'damage', Multishot: 'multishot', 'Critical Chance': 'criticalChance', 'Critical Damage': 'criticalMultiplier',
  'Status Chance': 'procChance', 'Fire Rate': 'fireRate', 'Attack Speed': 'fireRate', 'Reload Speed': 'reloadSpeed', 'Magazine Capacity': 'magazineSize',
  Health: 'health', 'Shield Capacity': 'shield', Armor: 'armor', 'Energy Max': 'energy',
  'Ability Strength': 'strength', 'Ability Duration': 'duration', 'Ability Range': 'range', 'Ability Efficiency': 'efficiency',
  Heat: 'heat', Cold: 'cold', Electricity: 'electricity', Toxin: 'toxin', Blast: 'blast', Radiation: 'radiation', Magnetic: 'magnetic',
  Impact: 'impact', Puncture: 'puncture', Slash: 'slash',
};
const percent = /^([+-]?[\d.]+)% (.+)$/;
const factionName = (name: string) => name === 'Orokin' ? 'Corrupted' : name === 'Sentients' ? 'Sentient' : name;
export function upgradeEffects(slot: UpgradeSlot, conditions: ModderConditions): UpgradeEffects {
  const mod = upgrades.get(slot.id);
  if (!mod) return { effects: [], omitted: slot.id ? ['Unavailable upgrade'] : [], maxStacks: 0 };
  const texts = mod.ranks[slot.rank];
  if (!texts) return { effects: [], omitted: ['Rank unavailable'], maxStacks: 0 };
  const effects: Effect[] = [], omitted: string[] = [];
  const source = `${mod.name} · rank ${slot.rank}`;
  const scale = (slot.rank + 1) / (mod.maxRank + 1);
  let maxStacks = 0;
  const add = (stat: string, value: number, target?: string) => { if (slot.enabled) effects.push({ stat, value, source, target }); };
  const namedArcane: Record<string, [string, number, string?, number?]> = {
    'Arcane Avenger': ['flatCrit', .45, 'Weapons'], 'Arcane Guardian': ['flatArmor', 900, 'Warframe'],
    'Arcane Rage': ['damage', 1.8, 'Primary'], 'Arcane Fury': ['damage', 1.8, 'Melee'],
    'Arcane Acceleration': ['fireRate', .9, 'Rifle'], 'Arcane Velocity': ['fireRate', 1.2, 'Secondary'],
    'Arcane Strike': ['fireRate', .6, 'Melee'], 'Molt Augmented': ['strength', .0024, 'Warframe', 250],
    'Molt Vigor': ['strength', .45, 'Warframe'],
    'Primary Merciless': ['damage', .3, undefined, 12], 'Secondary Merciless': ['damage', .3, undefined, 12],
  };
  if (namedArcane[mod.name]) {
    const [stat, max, target, stacks = 1] = namedArcane[mod.name];
    maxStacks = stacks; add(stat, max * scale * Math.min(slot.stacks, stacks), target);
    if (/Merciless$/.test(mod.name) && slot.rank === 5) add('reloadSpeed', .3);
    return { effects, omitted, maxStacks };
  }
  if (mod.type.includes('Arcane') || mod.set) return { effects, omitted: texts, maxStacks };
  if (mod.type === 'Stance Mod') return { effects, omitted: ['Stance combos and forced procs are not simulated.'], maxStacks };
  texts.forEach((text, index) => {
    if (/^Galvanized (Chamber|Hell|Diffusion)$/.test(mod.name) && index === 1) {
      maxStacks = mod.name === 'Galvanized Chamber' ? 5 : mod.name === 'Galvanized Hell' ? 4 : 4;
      add('multishot', .3 * scale * Math.min(slot.stacks, maxStacks)); return;
    }
    if (mod.name === 'Blood Rush' || mod.name === 'Weeping Wounds') {
      add(mod.name === 'Blood Rush' ? 'criticalChance' : 'procChance', .4 * scale * (conditions.combo - 1)); return;
    }
    const aura = { 'Steel Charge': ['damage', .6, 'Melee'], 'Rifle Amp': ['damage', .27, 'Rifle'], 'Pistol Amp': ['damage', .27, 'Secondary'], 'Shotgun Amp': ['damage', .18, 'Shotgun'] } as Record<string, [string, number, string]>;
    if (aura[mod.name]) { const [stat, value, target] = aura[mod.name]; add(stat, value * scale, target); return; }
    const factionMultiplier = text.match(/^x([\d.]+) Damage to (Grineer|Corpus|Infested|Corrupted|Orokin|Sentients?|Murmur)$/);
    if (factionMultiplier) {
      const maximum = mod.ranks[mod.maxRank][index].match(/^x([\d.]+)/);
      if (factionName(factionMultiplier[2]) === conditions.faction && maximum) add('faction', (Number(maximum[1]) - 1) * scale);
      return;
    }
    const match = text.match(percent);
    const maxMatch = mod.ranks[mod.maxRank]?.[index]?.match(percent);
    if (match && maxMatch) {
      const label = match[2].replace(' (x2 for Bows)', '');
      const stat = statNames[label];
      if (stat) { add(stat === 'fireRate' && text.includes('x2 for Bows') ? 'bowFireRate' : stat, Number(maxMatch[1]) / 100 * scale); return; }
      if (/^Damage to (Grineer|Corpus|Infested|Corrupted|Orokin|Sentients?|Murmur)$/.test(label)) {
        if (factionName(label.slice(10)) === conditions.faction) add('faction', Number(maxMatch[1]) / 100 * scale);
        return;
      }
    }
    omitted.push(text);
  });
  return { effects, omitted, maxStacks };
}

export function buildEffects(build: EquipmentBuild, conditions: ModderConditions) {
  return [...build.mods, build.exilus, build.special, ...build.arcanes].flatMap(slot => upgradeEffects(slot, conditions).effects);
}
const applies = (effect: Effect, item: CatalogItem) => !effect.target || effect.target === item.slot || effect.target === 'Weapons' && item.slot !== 'Warframe' || effect.target === 'Rifle' && item.slot === 'Primary' && item.weaponType !== 'Shotgun' || effect.target === 'Shotgun' && item.weaponType === 'Shotgun';
const total = (effects: Effect[], key: string) => sum(effects.filter(effect => effect.stat === key).map(effect => effect.value));
const contributors = (effects: Effect[], ...keys: string[]) => effects.filter(effect => keys.includes(effect.stat) && effect.value !== 0).map(effect => `${effect.source}: ${effect.value >= 0 ? '+' : ''}${fmt(effect.value * (effect.stat.startsWith('flatArmor') ? 1 : 100))}${effect.stat.startsWith('flatArmor') ? ' armor' : '%'}`);

export interface CalculationStep { label: string; value: number | null; unit?: string; formula: string; explanation: string; sources: string[] }
export interface Calculation { steps: CalculationStep[]; warnings: string[]; damage: { type: string; value: number }[]; headline: CalculationStep[] }
export function validateEquipment(build: EquipmentBuild, item: CatalogItem | undefined): string[] {
  if (!item) return build.id ? ['Equipment is no longer in this catalog. The build has been kept.'] : [];
  const warnings: string[] = [], families = new Set<string>(), arcIds = new Set<string>();
  const inspect = (slot: UpgradeSlot, kind: UpgradeKind) => {
    if (!slot.id) return;
    const mod = upgrades.get(slot.id);
    if (!mod) { warnings.push('An upgrade is unavailable in this snapshot.'); return; }
    if (slot.rank > mod.maxRank) warnings.push(`${mod.name}: rank ${slot.rank} exceeds its maximum.`);
    if (slot.polarity === 'universal' && kind !== 'special') warnings.push(`${mod.name}: universal polarity is only supported for aura / stance slots.`);
    if (!compatible(mod, item, kind)) warnings.push(`${mod.name} does not fit this ${kind === 'special' ? 'aura / stance' : kind} slot.`);
    if (kind === 'arcane') { if (arcIds.has(slot.id)) warnings.push(`${mod.name} cannot be equipped twice.`); arcIds.add(slot.id); }
    else { const family = modFamily(mod); if (families.has(family)) warnings.push(`${mod.name} conflicts with another equipped mod.`); families.add(family); }
  };
  build.mods.forEach(slot => inspect(slot, 'mod')); inspect(build.exilus, 'exilus'); inspect(build.special, 'special'); build.arcanes.forEach(slot => inspect(slot, 'arcane'));
  const cap = capacity(build);
  if (build.capacity > (item.maxRank ?? 30)) warnings.push(`${item.name} has a maximum base capacity of ${item.maxRank ?? 30}.`);
  if (cap.used > cap.total) warnings.push(`Over capacity by ${cap.used - cap.total}. Change ranks or polarities to make this build fit.`);
  return warnings;
}

const basicElements = ['heat', 'cold', 'electricity', 'toxin'];
const combinations: Record<string, string> = { 'cold+heat': 'blast', 'electricity+heat': 'radiation', 'heat+toxin': 'gas', 'cold+electricity': 'magnetic', 'cold+toxin': 'viral', 'electricity+toxin': 'corrosive' };
export function elementalDamage(base: Record<string, number>, effects: { stat: string; value: number }[]) {
  const result: Record<string, number> = {}, elements = new Map<string, number>();
  const baseTotal = sum(Object.values(base));
  const multiplier = Math.max(0, 1 + sum(effects.filter(e => e.stat === 'damage').map(e => e.value)));
  for (const effect of effects) if (basicElements.includes(effect.stat)) elements.set(effect.stat, (elements.get(effect.stat) ?? 0) + baseTotal * effect.value);
  for (const [type, value] of Object.entries(base)) {
    if (basicElements.includes(type)) elements.set(type, (elements.get(type) ?? 0) + value);
    else result[type] = value * (['impact', 'puncture', 'slash'].includes(type) ? Math.max(0, 1 + sum(effects.filter(e => e.stat === type).map(e => e.value))) : 1);
  }
  // Innate combined elements stay separate; mods add combined damage directly.
  for (const effect of effects) if (['radiation', 'magnetic', 'blast', 'viral', 'corrosive', 'gas'].includes(effect.stat)) result[effect.stat] = (result[effect.stat] ?? 0) + baseTotal * effect.value;
  const ordered = [...elements];
  for (let i = 0; i < ordered.length; i += 2) {
    const a = ordered[i], b = ordered[i + 1];
    const type = b ? combinations[[a[0], b[0]].sort().join('+')] : a[0];
    result[type] = (result[type] ?? 0) + a[1] + (b?.[1] ?? 0);
  }
  return Object.entries(result).filter(([, value]) => value > 0).map(([type, value]) => ({ type, value: value * multiplier }));
}

export function calculate(build: ArsenalBuild, slot: ArsenalSlot, items: Map<string, CatalogItem>): Calculation {
  const equipment = build.equipment[slot], item = items.get(equipment.id), c = build.conditions;
  const steps: CalculationStep[] = [], warnings = validateEquipment(equipment, item);
  const add = (label: string, value: number | null, formula: string, explanation: string, sources: string[] = [], unit?: string) => { const step = { label, value, formula, explanation, sources, unit }; steps.push(step); return step; };
  if (!item) return { steps, warnings, damage: [], headline: [] };
  const own = buildEffects(equipment, c);
  const shared = slot === 'Warframe' ? [] : buildEffects(build.equipment.Warframe, c).filter(effect => effect.target && applies(effect, item));
  const effects = [...own, ...shared].filter(effect => applies(effect, item));
  if (slot !== 'Warframe') {
    for (const upgrade of [build.equipment.Warframe.special, ...build.equipment.Warframe.arcanes]) {
      if (upgrade.id && upgrade.enabled && upgradeEffects(upgrade, c).omitted.length) warnings.push(`${upgrades.get(upgrade.id)?.name ?? 'Unknown Warframe effect'} on the Warframe is not included in weapon results.`);
    }
    warnings.push('Warframe passives, set bonuses, ability effects other than the selected Roar scenario, and weapon-specific bonuses are not simulated.');
  }
  for (const upgrade of [...equipment.mods, equipment.exilus, equipment.special, ...equipment.arcanes]) {
    if (!upgrade.id || !upgrade.enabled) continue;
    const result = upgradeEffects(upgrade, c);
    if (result.omitted.length) warnings.push(`${upgrades.get(upgrade.id)?.name ?? 'Unknown upgrade'}: ${result.omitted.join(' · ')} — not calculated.`);
  }
  const stat = (label: string, key: string, base: number | null, unit?: string, flatKey?: string) => {
    const modifier = total(effects, key), flat = flatKey ? total(effects, flatKey) : 0;
    const value = base === null ? null : Math.max(0, base * (1 + modifier) + flat);
    return add(label, value, base === null ? 'Enter the unmodded stat to calculate.' : `${fmt(base)} × (1 ${modifier < 0 ? '−' : '+'} ${fmt(Math.abs(modifier) * 100)}%)${flat ? ` + ${fmt(flat)}` : ''}`, 'Bonuses to the same stat add together before multiplying the unmodded stat.', contributors(effects, key, flatKey ?? ''), unit);
  };
  if (slot === 'Warframe') {
    const frame = frames.get(item.id);
    const base = frame?.ranked;
    if (!base) warnings.push('Rank 30 base stats are unavailable for this Warframe. Enter its unmodded Arsenal stats below.');
    if (item.name === 'Jade') warnings.push('Jade’s second aura slot is not modeled.');
    const get = (key: 'health' | 'shield' | 'armor' | 'energy') => equipment.overrides[key] ?? base?.[key] ?? null;
    const health = stat('Health', 'health', get('health'));
    stat('Shields', 'shield', get('shield'));
    const armor = stat('Armor', 'armor', get('armor'), undefined, 'flatArmor');
    stat('Energy', 'energy', get('energy'));
    const strength = stat('Ability strength', 'strength', 100, '%');
    const duration = stat('Ability duration', 'duration', 100, '%');
    stat('Ability range', 'range', 100, '%');
    const efficiency = 1 + total(effects, 'efficiency');
    add('Ability efficiency', Math.min(175, efficiency * 100), `${fmt(efficiency * 100)}%, capped at 175% for casting`, 'Excess efficiency can still reduce channel drain when duration is below 100%.', contributors(effects, 'efficiency'), '%');
    const cost = Math.max(.25, 2 - efficiency);
    add('Example cast cost', c.abilityCost * cost, `${fmt(c.abilityCost)} × max(0.25, 2 − ${fmt(efficiency)})`, 'Generic energy cost. Enter an ability’s base cost under Conditions; exceptions and cooldown abilities are not simulated.');
    const drain = duration.value! > 0 ? Math.max(.25, (2 - efficiency) / (duration.value! / 100)) : null;
    add('Example channel / s', drain === null ? null : c.abilityDrain * drain, `${fmt(c.abilityDrain)} × max(0.25, (2 − ${fmt(efficiency)}) ÷ ${fmt(duration.value! / 100)})`, 'Generic channel drain, with a minimum of 25% of base. Duration and uncapped efficiency both contribute.');
    add('Armor reduction', armor.value === null ? null : 100 * armor.value / (armor.value + 300), `${armor.value === null ? '?' : fmt(armor.value)} ÷ (armor + 300)`, 'Applies to health damage. Does not protect shields or stop damage that bypasses armor.', [], '%');
    add('Effective health', health.value === null || armor.value === null ? null : health.value * (1 + armor.value / 300), 'Health × (1 + armor ÷ 300)', 'Health pool against neutral damage after armor. Excludes shields, gating, Overguard, adaptation, healing, and ability damage reduction.');
    warnings.push('Warframe passives, ability-specific damage, augments, Archon Shards, and set bonuses are not simulated.');
    return { steps, warnings, damage: [], headline: [strength, health, armor] };
  }
  const alternate = equipment.attack >= 0 ? item.attacks?.[equipment.attack] : undefined;
  if (equipment.attack >= 0 && !alternate) {
    warnings.push('The selected attack component is unavailable. Choose an available attack; the saved selection has been kept.');
    return { steps, warnings, damage: [], headline: [] };
  }
  const values = { ...weaponSourceStats(item, equipment.attack), ...equipment.overrides };
  const get = (key: string): number | null => {
    const value = values[key];
    return typeof value === 'number' ? value : null;
  };
  const raw = alternate?.damage ?? item.damage ?? {};
  if (!Object.keys(raw).length) { warnings.push('Base damage is unavailable for this equipment.'); return { steps, warnings, damage: [], headline: [] }; }
  if (alternate) warnings.push('Only this attack component is calculated. Confirm its projectile count, fire rate, magazine, and reload in Base stats; other attack components are not added.');
  if (/Held/.test(String(values.trigger))) warnings.push('Beam ramp-up, ammo consumption, chaining, and beam status behavior are not simulated. Sustained DPS is unavailable.');
  if (item.modular || /^(Kuva|Tenet|Coda) /.test(item.name)) warnings.push('Assembly / progenitor bonuses are not simulated. This result uses the source damage only.');
  const damage = elementalDamage(raw, effects), damageValue = sum(damage.map(entry => entry.value));
  const damageFactor = Math.max(0, 1 + total(effects, 'damage'));
  const baseTotal = sum(Object.values(raw));
  const additions = sum(elementalDamage(raw, effects.filter(effect => effect.stat !== 'damage')).map(entry => entry.value)) - baseTotal;
  add('Damage / projectile', damageValue, `(${fmt(baseTotal)} base ${additions < 0 ? '−' : '+'} ${fmt(Math.abs(additions))} from elemental / physical mods) × ${fmt(damageFactor)} = ${fmt(damageValue)}`, 'Base damage bonuses add together. Elemental mods use total base damage; physical mods scale only their own damage type. Element order follows slots 1–8, then Exilus; innate basic elements join last.', contributors(effects, 'damage', ...basicElements, 'impact', 'puncture', 'slash', 'radiation', 'magnetic', 'blast'));
  add('Base damage factor', damageFactor, `1 + ${effects.filter(effect => effect.stat === 'damage' && effect.value).map(effect => `${fmt(effect.value * 100)}%`).join(' + ') || '0%'} = ${fmt(damageFactor)}`, 'Serration-style bonuses and active damage arcanes add in the same group. This factor multiplies every damage type.', contributors(effects, 'damage'), '×');
  const critBase = get('criticalChance');
  const crit = stat('Critical chance', 'criticalChance', critBase === null ? null : critBase * 100, '%');
  // Flat critical chance is added after relative critical-chance mods.
  const flatCrit = total(effects, 'flatCrit') * 100;
  if (crit.value !== null && flatCrit) { crit.value += flatCrit; crit.formula += ` + ${fmt(flatCrit)} percentage points`; crit.sources.push(...contributors(effects, 'flatCrit')); crit.explanation += ' Arcane Avenger adds percentage points after mod scaling.'; }
  const multiplier = stat('Critical multiplier', 'criticalMultiplier', get('criticalMultiplier'), '×');
  const chance = crit.value === null ? null : crit.value / 100;
  const averageCrit = chance === null || multiplier.value === null ? null : 1 + chance * (multiplier.value - 1);
  add('Average critical factor', averageCrit, `1 + ${chance === null ? '?' : fmt(chance)} × (${multiplier.value === null ? '?' : fmt(multiplier.value)} − 1)`, chance === null ? 'Base critical chance is unavailable.' : `${fmt((1 - chance % 1) * 100)}% tier ${Math.floor(chance)}${chance % 1 ? `, ${fmt(chance % 1 * 100)}% tier ${Math.floor(chance) + 1}` : ''}. Each tier adds one more (critical multiplier − 1).`, [], '×');
  const statusBase = get('procChance');
  stat('Status / projectile', 'procChance', statusBase === null ? null : statusBase * 100, '%');
  const multishot = slot === 'Melee' ? 1 : stat('Projectiles / trigger', 'multishot', get('multishot'), '×').value;
  if (total(effects, 'bowFireRate')) effects.push({ stat: 'fireRate', value: total(effects, 'bowFireRate') * (item.weaponType === 'Bow' ? 2 : 1), source: 'Fire-rate mods (bow scaling)' });
  const rate = stat(slot === 'Melee' ? 'Attack speed' : 'Fire rate', 'fireRate', get('fireRate'), '/ s');
  const frameStrength = Math.max(0, 1 + total(buildEffects(build.equipment.Warframe, c), 'strength'));
  const roar = c.roar ? c.roarBase / 100 * frameStrength : 0;
  const faction = 1 + total(effects, 'faction') + roar;
  if (c.roar) add('Roar bonus', roar * 100, `${fmt(c.roarBase)}% × ${fmt(frameStrength * 100)}% strength`, 'Scenario buff at the selected Warframe’s strength. Adds to faction damage for these direct hits; choose the base value for native or Helminth Roar.', [], '%');
  const armor = c.armor * (1 - c.strip / 100), mitigation = 300 / (300 + armor);
  const hit = averageCrit === null || multishot === null ? null : damageValue * averageCrit * multishot * faction;
  const average = add(slot === 'Melee' ? 'Average hit' : 'Average trigger', hit, `${fmt(damageValue)} × ${averageCrit === null ? '?' : fmt(averageCrit)} crit × ${multishot === null ? '?' : fmt(multishot)} projectiles × ${fmt(faction)} faction / Roar`, 'Expected direct damage before enemy defenses. Fractional projectiles and critical tiers represent averages over many attacks.', contributors(effects, 'faction'));
  const burst = add(slot === 'Melee' ? 'Base-swing DPS' : 'Burst DPS', hit === null || rate.value === null ? null : hit * rate.value, `Average hit × ${rate.value === null ? '?' : fmt(rate.value)} attacks / s`, slot === 'Melee' ? 'Normalized base swings. Stance timing, combo damage, heavy attacks, and forced procs are excluded.' : 'Continuous fire at the listed rate. Charge time, spool-up, burst delays, accuracy, falloff, and status damage are excluded.');
  let sustained: CalculationStep | undefined;
  if (slot !== 'Melee') {
    const magazine = stat('Magazine', 'magazineSize', get('magazineSize'));
    if (magazine.value !== null) magazine.value = Math.max(1, Math.round(magazine.value));
    magazine.explanation = 'Magazine size is rounded to a whole round. Assumes one round per trigger.';
    const reloadBase = get('reloadTime'), reloadFactor = 1 + total(effects, 'reloadSpeed');
    const reload = reloadBase === null || reloadFactor <= 0 ? null : reloadBase / reloadFactor;
    add('Reload time', reload, `${reloadBase === null ? '?' : fmt(reloadBase)} ÷ ${fmt(reloadFactor)}`, 'Reload-speed bonuses divide the reload time; +100% speed halves the time.', contributors(effects, 'reloadSpeed'), 's');
    const validCycle = !alternate && !/Held|Charge|Burst/.test(String(values.trigger)) && rate.value !== null && rate.value > 0 && magazine.value !== null && reload !== null && hit !== null;
    sustained = add('Sustained DPS', validCycle ? hit! * magazine.value! / (magazine.value! / rate.value! + reload!) : null, 'Average trigger × magazine ÷ (magazine ÷ fire rate + reload)', 'Ideal repeated magazines, one round per trigger. Unknown for beams, charge / burst attacks, alternate attacks, or missing cycle data.');
  }
  add('After target armor', hit === null ? null : hit * mitigation, `Average hit × 300 ÷ (300 + ${fmt(armor)})`, `Neutral health target with ${fmt(c.armor)} armor and ${fmt(c.strip)}% total strip. Excludes shields, resistances, vulnerabilities, status effects, headshots, damage attenuation, and damage that bypasses armor.`);
  return { steps, warnings, damage, headline: [average, burst, ...(sustained ? [sustained] : [crit])] };
}
