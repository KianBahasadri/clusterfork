import type { CatalogItem, Recipe } from './model.ts';
import type { DropRow } from './dropTables.ts';

export const craftingKinds = ['Weapons', 'Warframes', 'Companions', 'Vehicles', 'Gear', 'Cosmetics', 'Materials'] as const;
export type CraftingKind = typeof craftingKinds[number];
export interface CraftingMaterial {
  id: string; name: string; description: string; location: string; image?: string; wikiUrl: string;
  blueprintCredits: number | null; tradable: boolean;
  kind?: CraftingKind; equipment?: boolean; recipes?: Recipe[]; modular?: string | null;
}
export interface CraftingItem {
  id: string; name: string; image?: string; wikiUrl: string; description: string; location: string;
  kind: CraftingKind; equipment: boolean; recipes: Recipe[];
  blueprintCredits: number | null; catalogItem?: CatalogItem; modular?: string | null;
}
export interface CraftingPlan {
  id: string; itemId: string; quantity: number;
  /** Paths distinguish repeated ingredients in different branches. */
  completed: string[];
  /** Completed units per branch. Missing in older saves that only tracked checkmarks. */
  progress?: Record<string, number>;
  /** A recipe ID, or "gather" for an ingredient acquired without crafting. */
  choices: Record<string, string>;
}
export interface CraftingNode {
  key: string; item: CraftingItem; quantity: number; recipe?: Recipe; batches: number;
  choice: string; acquired: number; remaining: number; completed: boolean; children: CraftingNode[]; problem?: string;
}
export const MAX_CRAFTING_PLANS = 50;

export function craftingPlanLabel(plans: CraftingPlan[], index: number, name: string): string {
  const plan = plans[index];
  const duplicate = plans.some(other => other.id !== plan.id && other.itemId === plan.itemId);
  return `${name}${plan.quantity > 1 ? ` × ${plan.quantity}` : ''}${duplicate ? ` · Plan ${plans.slice(0, index + 1).filter(other => other.itemId === plan.itemId).length}` : ''}`;
}

export function validateCraftingPlans(value: unknown): CraftingPlan[] {
  const text = (value: unknown, max: number): value is string => typeof value === 'string' && value.length > 0 && value.length <= max;
  const invalid = () => { throw new Error('Crafting plans could not be read. The original data has been kept.'); };
  if (!Array.isArray(value) || value.length > MAX_CRAFTING_PLANS) return invalid();
  const ids = new Set<string>();
  return value.map(plan => {
    if (!plan || !text(plan.id, 200) || ids.has(plan.id) || !text(plan.itemId, 2048)
      || !Number.isSafeInteger(plan.quantity) || plan.quantity < 1 || plan.quantity > 100
      || !Array.isArray(plan.completed) || plan.completed.length > 2000 || !plan.completed.every((key: unknown) => text(key, 16384))
      || new Set(plan.completed).size !== plan.completed.length
      || !plan.choices || typeof plan.choices !== 'object' || Array.isArray(plan.choices)
      || Object.keys(plan.choices).length > 2000 || !Object.entries(plan.choices).every(([key, choice]) => text(key, 16384) && text(choice, 2048))
      || plan.progress !== undefined && (!plan.progress || typeof plan.progress !== 'object' || Array.isArray(plan.progress)
        || Object.keys(plan.progress).length > 2000 || !Object.entries(plan.progress).every(([key, amount]) => text(key, 16384) && Number.isSafeInteger(amount) && (amount as number) >= 0))) return invalid();
    ids.add(plan.id);
    return { id: plan.id, itemId: plan.itemId, quantity: plan.quantity, completed: [...plan.completed], choices: { ...plan.choices }, ...(plan.progress !== undefined ? { progress: { ...plan.progress } } : {}) };
  });
}

/** Combine inventory and standalone Foundry recipes, resolving shared ingredients by game ID. */
export function craftingIndex(catalog: CatalogItem[], materials: CraftingMaterial[]): Map<string, CraftingItem> {
  const index = new Map<string, CraftingItem>(materials.map(item => [item.id, { ...item, kind: item.kind ?? 'Materials', equipment: item.equipment ?? false, recipes: item.recipes ?? [] }]));
  for (const item of catalog) index.set(item.id, { ...item, description: item.description || '', location: '', kind: item.slot === 'Warframe' ? 'Warframes' : 'Weapons', equipment: true, recipes: item.recipes || [], blueprintCredits: item.market?.blueprintCredits ?? null, catalogItem: item });
  const visited = new Set<Recipe>();
  function visit(recipe: Recipe) {
    if (visited.has(recipe)) return;
    visited.add(recipe);
    for (const part of recipe.ingredients) {
      let item = index.get(part.id);
      if (!item) {
        item = { id: part.id, name: part.name, kind: 'Materials', equipment: part.equipment, description: '', location: '', blueprintCredits: null, wikiUrl: `https://wiki.warframe.com/w/${encodeURIComponent(part.name.replaceAll(' ', '_'))}`, recipes: [] };
        index.set(part.id, item);
      }
      for (const child of part.recipes || []) {
        if (!item.recipes.some(entry => entry.id === child.id)) item.recipes = [...item.recipes, child];
        visit(child);
      }
    }
  }
  for (const item of catalog) item.recipes?.forEach(visit);
  for (const item of materials) item.recipes?.forEach(visit);
  return index;
}

export function defaultCraftingChoice(item: CraftingItem, root = false): string {
  // Rare-resource blueprints are optional alternatives to normal resource drops.
  return !item.recipes.length || !root && item.recipes.every(recipe => /ResourceBlueprint$/.test(recipe.id)) ? 'gather' : item.recipes[0].id;
}

export function buildCraftingTree(index: Map<string, CraftingItem>, plan: CraftingPlan): CraftingNode | null {
  const item = index.get(plan.itemId);
  if (!item) return null;
  const completed = new Set(plan.completed);
  let remaining = 2000;
  const node = (item: CraftingItem, quantity: number, key: string, ancestors: string[]): CraftingNode => {
    const choice = Object.hasOwn(plan.choices, key) ? plan.choices[key] : defaultCraftingChoice(item, key === 'root');
    const recipe = item.recipes.find(recipe => recipe.id === choice);
    const acquired = Math.min(quantity, Object.hasOwn(plan.progress ?? {}, key) ? plan.progress![key] : completed.has(key) ? quantity : 0);
    const needed = quantity - acquired;
    const batches = recipe ? Math.ceil(needed / recipe.quantity) : 0;
    const result: CraftingNode = { key, item, quantity, acquired, remaining: needed, recipe, batches, choice, completed: needed === 0, children: [] };
    if (--remaining < 0 || ancestors.length >= 12 || ancestors.includes(item.id)) return { ...result, problem: 'Recipe chain unavailable.' };
    if (!recipe) return choice === 'gather' && key !== 'root' ? result : { ...result, problem: 'This recipe is no longer available. Choose another recipe.' };
    result.children = recipe.ingredients.map(part => {
      const ingredient = index.get(part.id)!;
      // Keep covered descendants so undo restores their independent progress.
      const childBatches = result.completed ? Math.ceil(quantity / recipe.quantity) : batches;
      return node(ingredient, part.count * childBatches, `${key}/${encodeURIComponent(recipe.id)}/${encodeURIComponent(part.id)}`, [...ancestors, item.id]);
    });
    return result;
  };
  return node(item, plan.quantity, 'root', []);
}

export function craftingSummary(root: CraftingNode) {
  const materials = new Map<string, { item: CraftingItem; quantity: number }>();
  let total = 0, completed = 0, credits = 0, unknownCredits = false;
  function visit(node: CraftingNode, covered = false) {
    total++;
    const done = covered || node.completed;
    if (done) completed++;
    if (!done) {
      if (node.problem) unknownCredits = true;
      if (node.recipe) {
        if (node.recipe.credits === null) unknownCredits = true;
        else credits += node.recipe.credits * node.batches;
      } else {
        const current = materials.get(node.item.id);
        materials.set(node.item.id, { item: node.item, quantity: (current?.quantity ?? 0) + node.remaining });
      }
    }
    node.children.forEach(child => visit(child, done));
  }
  visit(root);
  return { total, completed, credits, unknownCredits, materials: [...materials.values()].sort((a, b) => a.item.name.localeCompare(b.item.name)) };
}

/** Keep source alternatives and mission restrictions together; do not guess missing locations. */
export function craftingAreas(materials: { item: CraftingItem; quantity: number }[]) {
  const areas = new Map<string, { location: string; items: CraftingItem[] }>();
  for (const { item, quantity } of materials) {
    if (quantity <= 0) continue;
    // Mining advice belongs in source details, after the location's first sentence.
    const location = item.location.replace(/\.\s+This\b.*/, '').replace(/[.\s]+$/, '').trim();
    const area = areas.get(location) ?? { location, items: [] };
    if (!area.items.some(entry => entry.id === item.id)) area.items.push(item);
    areas.set(location, area);
  }
  return [...areas.values()].sort((a, b) => Number(!a.location) - Number(!b.location) || a.location.localeCompare(b.location));
}

export function setCraftingProgress(plan: CraftingPlan, key: string, amount: number, quantity: number): CraftingPlan {
  if (!Number.isSafeInteger(amount) || !Number.isSafeInteger(quantity) || quantity < 1 || amount < 0 || amount > quantity) throw new Error('Invalid completed quantity.');
  const progress = { ...plan.progress };
  if (amount) progress[key] = amount; else delete progress[key];
  return { ...plan, progress, completed: amount === quantity ? [...new Set([...plan.completed, key])] : plan.completed.filter(path => path !== key) };
}

export function checkCraftingNode(plan: CraftingPlan, key: string, checked: boolean, quantity?: number): CraftingPlan {
  if (quantity !== undefined) return setCraftingProgress(plan, key, checked ? quantity : 0, quantity);
  return { ...plan, ...(plan.progress ? { progress: Object.fromEntries(Object.entries(plan.progress).filter(([path]) => path !== key)) } : {}), completed: checked ? [...new Set([...plan.completed, key])] : plan.completed.filter(path => path !== key) };
}

export function chooseCraftingRecipe(plan: CraftingPlan, key: string, choice: string): CraftingPlan {
  // Retain independent branches. A changed recipe requires reviewing its ingredients.
  return { ...plan, choices: { ...Object.fromEntries(Object.entries(plan.choices).filter(([path]) => !path.startsWith(`${key}/`))), [key]: choice }, completed: plan.completed.filter(path => path !== key && !path.startsWith(`${key}/`)), ...(plan.progress ? { progress: Object.fromEntries(Object.entries(plan.progress).filter(([path]) => path !== key && !path.startsWith(`${key}/`))) } : {}) };
}

/** Older clients omit partial quantities. Keep them unless that branch was explicitly changed. */
export function preserveCraftingProgress(next: CraftingPlan[], previous: CraftingPlan[]): CraftingPlan[] {
  return next.map(plan => {
    const old = previous.find(entry => entry.id === plan.id && entry.itemId === plan.itemId && entry.quantity === plan.quantity);
    if (plan.progress !== undefined || !old?.progress) return plan;
    const changed = [...new Set([...Object.keys(old.choices), ...Object.keys(plan.choices)])].filter(key => old.choices[key] !== plan.choices[key]);
    return { ...plan, progress: Object.fromEntries(Object.entries(old.progress).filter(([key]) => old.completed.includes(key) === plan.completed.includes(key) && !changed.some(path => key === path || key.startsWith(`${path}/`)))) };
  });
}

export function craftingDuration(seconds: number | null): string {
  if (seconds === null) return 'Time unknown';
  if (seconds === 0) return 'Instant';
  const days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600), minutes = Math.floor(seconds % 3600 / 60);
  return [days ? `${days}d` : '', hours ? `${hours}h` : '', minutes ? `${minutes}m` : '', !days && !hours && !minutes ? `${seconds}s` : ''].filter(Boolean).join(' ');
}

/** Match the requested variant, including published resource bundles, never substrings. */
export function craftingSources(rows: DropRow[], name: string, recipeName?: string): DropRow[] {
  const normalized = (text: string) => text.trim().toLowerCase().replace(/^\d[\d,]*\s*[x×]\s*/i, '');
  const names = new Set([name, ...(recipeName ? [recipeName] : [])].map(normalized));
  return rows.filter(row => names.has(normalized(row.reward))).sort((a, b) => (b.chance ?? -1) - (a.chance ?? -1) || a.source.localeCompare(b.source));
}
