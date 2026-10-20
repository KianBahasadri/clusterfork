import { maximumRank, normalizeName } from './model.ts';
import type { CatalogItem, Equipment } from './model.ts';

export const recommendedActions = ['Master', 'Acquire & master', 'Use for crafting', 'Craft a spare', 'Add Incarnon', 'Remove Incarnon', 'Check Helminth', 'Subsume', 'Keep', 'Sell', 'Review', 'Use', 'No action'] as const;
export const warframeRecommendedActions = recommendedActions.filter(action => !['Use for crafting', 'Craft a spare', 'Add Incarnon', 'Remove Incarnon'].includes(action));
export interface Recommendation {
  action: typeof recommendedActions[number];
  reason: string;
}
type EquipmentFacts = Equipment & { catalogItem?: CatalogItem };
const emptyInventory: ReadonlyMap<string, Equipment> = new Map();
// These quest rewards cannot be removed from the inventory. Source: Warframe
// Support, Quest Tips (Post Second Dream), The Sacrifice.
const permanentRewards = new Set(['excaliburumbra', 'skiajati']);
const result = (action: Recommendation['action'], reason: string): Recommendation => ({ action, reason });

function craftingTargets(item: EquipmentFacts, inventory: ReadonlyMap<string, Equipment>) {
  if (item.slot === 'Warframe') return [];
  const targets = item.catalogItem?.craftingTargets ?? (item.craftingIngredient
    ? item.craftingTarget.split(/[,;]/).map(name => ({ name: name.trim(), count: 1 })).filter(target => target.name)
    : []);
  // Ownership and completed mastery each make a target unnecessary for this
  // collection plan. Match exact variants, never their shared weapon family.
  return targets.filter(target => {
    const saved = inventory.get(normalizeName(target.name));
    return !saved?.owned && !saved?.mastered;
  });
}

export function canSubsume(item: EquipmentFacts): boolean {
  // Necramechs share the Warframes collection, but cannot be subsumed.
  return item.slot === 'Warframe' && item.variant === 'Base' && (!item.catalogItem || item.catalogItem.weaponType === 'Warframe');
}

export function helminthStatus(item: EquipmentFacts): string {
  return canSubsume(item) ? item.helminth ?? 'Unknown' : 'Not applicable';
}

function helminthAction(item: EquipmentFacts): Recommendation | undefined {
  if (!canSubsume(item) || item.helminth === 'Yes') return;
  return item.helminth === 'No'
    ? result('Subsume', 'Unlock its ability in Helminth before discarding it.')
    : result('Check Helminth', 'Record whether its Helminth ability is unlocked before discarding it.');
}

// Priority rules, not a weighted score: no preference or investment can
// outweigh unfinished mastery, and a Prime counterpart always excludes Keep.
export function calculateRecommendation(item: EquipmentFacts, inventory: ReadonlyMap<string, Equipment> = emptyInventory): Recommendation {
  const warframe = item.slot === 'Warframe';
  const noun = warframe ? item.catalogItem?.weaponType === 'Necramech' ? 'Necramech' : 'Warframe' : 'weapon';
  if (item.slot === 'Exalted') return item.owned
    ? result('Use', 'Built-in equipment; no separate mastery or sale.')
    : result('No action', 'Built-in equipment belongs to its Warframe or Necramech.');
  if (!item.owned) return item.mastered
    ? result('No action', 'Mastery complete; not currently owned.')
    : result('Acquire & master', 'Not owned; acquire it to earn its mastery.');
  if (!item.mastered) return result('Master', maximumRank(item.catalogItem ?? item) === 40
    ? 'Earn all mastery through rank 40 before any other action.'
    : warframe ? canSubsume(item) ? 'Earn all mastery before upgrading, subsuming, or selling.' : 'Earn all mastery before upgrading or selling.' : 'Earn all mastery before crafting, upgrading, or selling.');

  if (permanentRewards.has(normalizeName(item.name))) return result('Use', 'This quest reward cannot be sold or consumed.');

  const incarnon = warframe ? undefined : item.catalogItem?.incarnon?.type;
  const specialVariant = !['Base', 'MK1', 'Other'].includes(item.variant);
  const targets = craftingTargets(item, inventory);
  if (targets.length) {
    const targetText = targets.map(target => `${target.name} (${target.count}× ${item.name})`).join(', ');
    const preserveCopy = item.upgraded === 'Yes' || item.powerful || item.questWeapon || item.incarnonInstalled === 'Yes'
      || (!item.hasPrimeVariant && (item.override === 'Keep' || specialVariant || incarnon === 'genesis' || incarnon === 'native'));
    if (preserveCopy) return result('Craft a spare', `For ${targetText}; preserve this invested or useful copy.`);
    return result('Use for crafting', `For ${targetText}.${incarnon === 'genesis' ? ' Remove any installed adapter first.' : ''}`);
  }
  // Unknown adapter state stays unknown. Eligibility and the generic Upgraded
  // flag are never interpreted as evidence that a Genesis adapter is installed.
  const sell = (reason: string): Recommendation => incarnon === 'genesis' && item.incarnonInstalled === 'Yes'
    ? result('Remove Incarnon', `${reason} Recover the adapter at Cavalero before selling.`)
    : item.questWeapon ? result('Review', `${reason} Check quest use, replacement, and sale restrictions.`)
    : result('Sell', `${reason}${incarnon === 'genesis' && item.incarnonInstalled !== 'No' ? ' Check for an installed adapter first.' : ''}`);

  if (item.hasPrimeVariant) return helminthAction(item) ?? sell('Mastery complete; a Prime counterpart exists.');
  if (item.override === 'Keep') return result('Keep', 'Your Keep override is set; no Prime counterpart exists.');
  if (item.override === "Don't keep" || item.dontKeep) return helminthAction(item) ?? sell(warframe ? 'Mastery complete; marked Don’t keep.' : 'Mastery and recorded crafting uses are complete; marked Don’t keep.');

  if (incarnon === 'genesis') return item.incarnonInstalled === 'Yes'
    ? result('Keep', 'Genesis adapter installed; no Prime counterpart exists.')
    : result('Add Incarnon', item.incarnonInstalled === 'No'
      ? 'Install its Genesis adapter at Cavalero.'
      : 'Genesis compatible; install an adapter if not already fitted.');
  if (incarnon === 'native') return result('Keep', 'Native Incarnon weapon; no adapter needed.');
  if (!warframe && item.incarnon && !item.catalogItem) return result('Review', 'Incarnon marked, but its type and compatibility are unconfirmed.');
  if (item.powerful) return result('Keep', 'Marked as a favorite or powerful build.');
  if (item.upgraded === 'Yes') return result('Keep', `Preserve the upgrades invested in this ${noun}.`);
  if (item.questWeapon) return result('Keep', `Quest ${noun}; preserve it for its unique use or replacement cost.`);
  if (specialVariant) return result('Keep', 'Preserve this special variant; no Prime replacement is recorded.');
  if (warframe) return result('Keep', `No Prime counterpart exists for this ${noun}.`);
  if (!item.catalogItem) return result('Review', 'No catalog match; confirm crafting and upgrade uses before selling.');
  return sell('Mastery complete; no remaining crafting or upgrade use recorded.');
}

export function recommendationFor(item: EquipmentFacts & { recommendation?: Recommendation }): Recommendation {
  return item.recommendation ?? calculateRecommendation(item);
}
