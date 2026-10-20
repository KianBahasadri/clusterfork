import data from './crafting.json' with { type: 'json' };
import { catalog } from './catalog';
import { craftingIndex } from '../lib/crafting';
import type { CraftingMaterial } from '../lib/crafting';

export const craftingMaterials = data.materials as CraftingMaterial[];
export const craftingItems = craftingIndex(catalog, craftingMaterials);
export const craftingDate = data.retrievedAt;
export const craftingDataSources = data.sources;
