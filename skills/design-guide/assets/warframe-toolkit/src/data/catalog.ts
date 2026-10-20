import data from './catalog.json' with { type: 'json' };
import type { CatalogItem } from '../lib/model';

// JSON inference widens the heterogeneous stat keys to optional properties.
// The generated catalog's shape and local assets are checked in catalog.test.ts.
export const catalog = data.items as unknown as CatalogItem[];
export const catalogDate = data.retrievedAt;
