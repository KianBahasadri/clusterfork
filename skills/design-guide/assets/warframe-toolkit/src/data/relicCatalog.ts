import data from './relics.json';
import type { RelicCatalogItem } from '../lib/relicCatalog';

export const relicCatalog = data.items as RelicCatalogItem[];
export const relicNames = data.names;
export const relicCatalogDate = data.retrievedAt.slice(0, 10);
