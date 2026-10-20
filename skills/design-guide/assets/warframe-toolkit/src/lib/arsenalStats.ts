import reference from '../data/arsenalWeaponStats.json' with { type: 'json' };
import { calculate, fmt } from './modder.ts';
import type { Calculation } from './modder.ts';
import type { ArsenalBuild, ArsenalSlot } from './modderModel.ts';
import type { CatalogItem } from './model.ts';

interface AttackReference {
  ammoCost?: number; minSpread?: number; maxSpread?: number; range?: number; punchThrough?: number;
  shotSpeed?: number; shotType?: string; trigger?: string; chargeTime?: number; incarnonCharges?: number;
  falloff?: { start: number; end: number; reduction: number };
}
interface WeaponReference extends AttackReference { ammoMax?: number; ammoPickup?: number; beam: boolean; attacks: Record<string, AttackReference> }
const references = reference.weapons as Record<string, WeaponReference>;
export const weaponReferenceSources = reference.sources;
export interface ArsenalStat {
  key: string; label: string; value: number | string | null; unit?: string; damageType?: string;
  disposition?: number; base?: boolean; title?: string; details?: ArsenalStat[];
}
export interface ArsenalStatGroup { title?: string; note?: string; rows: ArsenalStat[] }
export const arsenalStatText = (row: ArsenalStat) => row.value === null ? 'Unavailable' : `${typeof row.value === 'number' ? fmt(row.value) : row.value}${row.unit ? ` ${row.unit}` : ''}`;

// WARFRAME Wiki Module:Weapons AccuracyBucket uses aimed minimum spread.
export function accuracyLabel(minSpread: number | undefined) {
  if (minSpread === undefined || !Number.isFinite(minSpread) || minSpread < 0) return null;
  return minSpread <= 1 ? 'Very High' : minSpread <= 3 ? 'High' : minSpread <= 6 ? 'Medium' : minSpread <= 10 ? 'Low' : 'Very Low';
}
export const damageComponents: Record<string, string[]> = {
  blast: ['heat', 'cold'], radiation: ['heat', 'electricity'], gas: ['heat', 'toxin'],
  magnetic: ['cold', 'electricity'], viral: ['cold', 'toxin'], corrosive: ['electricity', 'toxin'],
};
const damageOrder = ['impact', 'puncture', 'slash', 'heat', 'cold', 'electricity', 'toxin', 'blast', 'radiation', 'gas', 'magnetic', 'viral', 'corrosive', 'void', 'tau', 'true'];
const sameDamage = (a: Record<string, number>, b: Record<string, number>) => [...new Set([...Object.keys(a), ...Object.keys(b)])].every(key => Math.abs((a[key] ?? 0) - (b[key] ?? 0)) < .01);

export function arsenalWeaponStats(build: ArsenalBuild, slot: ArsenalSlot, items: Map<string, CatalogItem>, result: Calculation): ArsenalStatGroup[] {
  const equipment = build.equipment[slot], item = items.get(equipment.id);
  if (!item || slot === 'Warframe') return [];
  const source = references[item.id], values = item.stats ?? {}, attacks = item.attacks ?? [];
  const defaultAttack = attacks.findIndex(attack => sameDamage(attack.damage, item.damage ?? {}));
  const selected = equipment.attack >= 0 ? equipment.attack : defaultAttack;
  const selectedAttack = attacks[selected];
  const attackReference = (index: number): AttackReference => ({ ...(index === defaultAttack ? source : {}), ...source?.attacks[attacks[index]?.name ?? ''] });
  const meta = attackReference(selected);
  const groups: ArsenalStatGroup[] = [];
  const step = (calculation: Calculation, key: string, label = key, unit?: string): ArsenalStat => {
    const value = calculation.steps.find(step => step.label === key);
    return { key, label, value: value?.value ?? null, unit: unit ?? value?.unit, title: value?.explanation };
  };
  const base = (key: string, value: unknown, unit?: string): ArsenalStat[] => typeof value === 'number' && Number.isFinite(value) || typeof value === 'string' ? [{ key, label: key, value: value as number | string, unit, base: true }] : [];
  const damageRows = (calculation: Calculation, attackMeta: AttackReference): ArsenalStat[] => [
    step(calculation, 'Critical chance', 'Critical Chance'), step(calculation, 'Critical multiplier', 'Critical Damage'), step(calculation, 'Status / projectile', 'Status'),
    ...calculation.damage.slice().sort((a, b) => damageOrder.indexOf(a.type) - damageOrder.indexOf(b.type)).map(part => ({ key: part.type, label: part.type[0].toUpperCase() + part.type.slice(1), value: part.value, damageType: part.type })),
    ...base('Falloff', attackMeta.falloff && `${fmt(attackMeta.falloff.start)}–${fmt(attackMeta.falloff.end)} m (${fmt(attackMeta.falloff.reduction * 100)}%)`),
    step(calculation, 'Damage / projectile', 'Total', ''),
  ];
  const riven = Number(values.disposition);
  const disposition: ArsenalStat[] = Number.isInteger(riven) && riven >= 1 && riven <= 5 ? [{ key: 'Riven disposition', label: 'Riven Disposition', value: riven, disposition: riven, title: `${riven} of 5${typeof values.omegaAttenuation === 'number' ? ` · ${fmt(values.omegaAttenuation)}× Riven multiplier` : ''}. Catalog snapshot; Riven mods are not simulated.` }] : [];
  const range = (attackMeta: AttackReference, beam: boolean) => base(beam ? 'Beam Range' : 'Range', attackMeta.range ?? values.range, 'm');
  if (slot === 'Melee') {
    groups.push({ rows: [step(result, 'Attack speed', 'Attack Speed', ''), ...range(meta, false), ...base('Combo Duration', values.comboDuration, 's'), ...base('Follow Through', values.followThrough), ...base('Blocking Angle', values.blockingAngle, '°'), ...disposition] });
  } else {
    const magazine = step(result, 'Magazine');
    magazine.details = [...base('Ammo Maximum', source?.ammoMax), ...base('Ammo Pickup', source?.ammoPickup), ...base('Ammo Cost', meta.ammoCost)];
    const accuracy = base('Accuracy', accuracyLabel(meta.minSpread) ?? (equipment.attack < 0 ? values.accuracy : undefined))[0];
    if (accuracy) accuracy.details = [...base('Deviation With Aim', meta.minSpread, '°'), ...base('Max Deviation', meta.maxSpread, '°')];
    const trigger = base('Trigger', meta.trigger ?? (equipment.attack < 0 ? values.trigger : undefined))[0];
    if (trigger) trigger.details = [...base('Noise', values.noise), ...base('Shot Type', meta.shotType)];
    const ammunition = meta.incarnonCharges === undefined ? [magazine, step(result, 'Reload time', 'Reload')] : base('Incarnon Charges', meta.incarnonCharges);
    groups.push({ rows: [step(result, 'Fire rate', 'Fire Rate', ''), step(result, 'Projectiles / trigger', 'Multishot', ''), ...ammunition,
      ...(accuracy ? [accuracy] : []), ...(trigger ? [trigger] : []),
      ...(values.noise === 'Silent' ? base('Noise', values.noise) : []), ...range(meta, source?.beam === true && !/Incarnon|Radial|Explosion/i.test(selectedAttack?.name ?? '')),
      ...base('Punch Through', meta.punchThrough && meta.punchThrough > 0 ? meta.punchThrough : undefined, 'm'),
      ...base('Projectile Speed', meta.shotSpeed, 'm/s'), ...base('Charge Time', meta.chargeTime, 's'), ...disposition] });
  }
  groups.push({ title: selectedAttack && equipment.attack >= 0 ? selectedAttack.name : 'Damage', note: /Incarnon/i.test(selectedAttack?.name ?? '') ? 'Evolutions excluded' : undefined, rows: damageRows(result, meta) });
  for (const [index, attack] of attacks.entries()) {
    if (index === selected) continue;
    const attackMeta = attackReference(index);
    const alternate = calculate({ ...build, equipment: { ...build.equipment, [slot]: { ...equipment, attack: index, overrides: {} } } }, slot, items);
    groups.push({ title: attack.name, note: /Incarnon/i.test(attack.name) ? 'Evolutions excluded' : undefined, rows: [
      step(alternate, slot === 'Melee' ? 'Attack speed' : 'Fire rate', slot === 'Melee' ? 'Attack Speed' : 'Fire Rate', ''),
      ...(slot === 'Melee' ? [] : [step(alternate, 'Projectiles / trigger', 'Multishot', '')]),
      ...base('Range', attackMeta.range, 'm'), ...base('Incarnon Charges', attackMeta.incarnonCharges), ...damageRows(alternate, attackMeta),
    ] });
  }
  if (slot === 'Melee') {
    groups.push({ title: 'Heavy Attack', note: 'Base values', rows: [...base('Damage', values.heavyAttackDamage), ...base('Slam Attack', values.heavySlamAttack), ...base('Slam Radius', values.heavySlamRadius, 'm'), ...base('Wind Up', values.windUp, 's')] });
    groups.push({ title: 'Other Attacks', note: 'Base values', rows: [...base('Slide Attack', values.slideAttack), ...base('Slam Attack', values.slamAttack), ...base('Slam Radius', values.slamRadius, 'm')] });
  }
  return groups.filter(group => group.rows.length);
}
