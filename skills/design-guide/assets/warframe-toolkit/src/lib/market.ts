export const marketPlatforms = [{ value: 'pc', label: 'PC' }, { value: 'ps4', label: 'PlayStation' }, { value: 'xbox', label: 'Xbox' }, { value: 'switch', label: 'Switch' }, { value: 'mobile', label: 'Mobile' }] as const;
export type MarketPlatform = typeof marketPlatforms[number]['value'];
export const MARKET_ORDERS_TTL = 5 * 60_000;
export const MARKET_HISTORY_TTL = 60 * 60_000;
export const MARKET_ITEMS_TTL = 24 * 60 * 60_000;
export interface MarketItem {
  id: string; slug: string; name: string; gameRef: string; tags: string[]; subtypes: string[];
  maxRank?: number; maxCharges?: number; maxAmberStars?: number; maxCyanStars?: number; ducats?: number;
}
export interface MarketVariant { subtype?: string; rank?: number; charges?: number; amberStars?: number; cyanStars?: number }
export interface MarketSubject { name: string; kind?: 'equipment' | 'relic' | 'item'; refinement?: string; gameRef?: string }
export interface MarketTarget { item: MarketItem; variant: MarketVariant; label: string; qualifier: string; note: string }
export interface MarketOrder extends MarketVariant {
  id: string; type: 'buy' | 'sell'; price: number; quantity: number; perTrade: number; userId: string; status: 'online' | 'ingame' | 'offline';
}
export interface MarketHistoryRow extends MarketVariant {
  time: string; type: 'closed' | 'buy' | 'sell'; volume: number | null;
  min: number | null; max: number | null; median: number | null; average: number | null; weighted: number | null;
}
export interface MarketHistory { '48hours': MarketHistoryRow[]; '90days': MarketHistoryRow[] }
export interface MarketSnapshot<T> { data: T; fetchedAt: string; stale: boolean; error?: string; retryAt?: string }
export interface PriceStats { min: number | null; max: number | null; mean: number | null; median: number | null; listings: number; units: number }
export interface MarketQuote { price: number | null; sample: number; sell: PriceStats; buy: PriceStats; offline: number; ingame: number; spread: number | null }
export interface PriceSample { time: string; closed: number | null; sell: number | null; buy: number | null; volume: number | null; min: number | null; max: number | null; average: number | null; weighted: number | null }

const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const nonnegative = (value: unknown): number | undefined => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const nameKey = (value: string) => value.normalize('NFKC').toLowerCase().replace(/[’']/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
const variantFields = ['rank', 'charges', 'amberStars', 'cyanStars'] as const;

export function parseMarketItems(value: unknown): MarketItem[] {
  const data = record(value).data;
  if (!Array.isArray(data) || !data.length) throw new Error('WFM returned an invalid item catalog.');
  const items = data.flatMap(raw => {
    const item = record(raw), en = record(record(item.i18n).en);
    if (typeof item.id !== 'string' || typeof item.slug !== 'string' || !/^[a-z0-9_]+$/.test(item.slug) || typeof en.name !== 'string' || item.tradable === false) return [];
    const result: MarketItem = { id: item.id, slug: item.slug, name: en.name, gameRef: typeof item.gameRef === 'string' ? item.gameRef : '', tags: strings(item.tags), subtypes: strings(item.subtypes) };
    for (const key of ['maxRank', 'maxCharges', 'maxAmberStars', 'maxCyanStars', 'ducats'] as const) if (nonnegative(item[key]) !== undefined) result[key] = nonnegative(item[key]);
    return [result];
  });
  if (!items.length) throw new Error('WFM returned an empty item catalog.');
  return items;
}

export function marketIndex(items: MarketItem[]) {
  return { byName: new Map(items.map(item => [nameKey(item.name), item])), byRef: new Map(items.filter(item => item.gameRef).map(item => [item.gameRef, item])) };
}
export type MarketIndex = ReturnType<typeof marketIndex>;
export function resolveMarketTarget(subject: MarketSubject, index: MarketIndex): MarketTarget | null {
  let name = subject.name.trim(), refinement = subject.refinement;
  const relic = /^(Lith|Meso|Neo|Axi|Requiem|Vanguard)\s+([A-Z]\d+|[IV]+|Eterna)(?:\s+Relic)?(?:\s*[\[(]?(Intact|Exceptional|Flawless|Radiant|Unrefined)[\])]?)?$/i.exec(name);
  if (relic) { name = `${relic[1]} ${relic[2]} Relic`; refinement ??= relic[3] ?? 'Intact'; }
  let item = index.byName.get(nameKey(name));
  if (!item && subject.kind === 'equipment') item = index.byName.get(nameKey(`${name} Set`)) ?? index.byName.get(nameKey(`${name} Blueprint`)) ?? (subject.gameRef ? index.byRef.get(subject.gameRef) : undefined);
  if (!item) return null;
  const variant: MarketVariant = {};
  let qualifier = '', note = '';
  if (item.tags.includes('set')) { qualifier = 'set'; note = 'Price of the tradeable component set, not built equipment.'; }
  else if (subject.kind === 'equipment' && item.tags.includes('blueprint')) { qualifier = 'blueprint'; note = 'Price of the blueprint, not built equipment.'; }
  else if (subject.kind === 'equipment') note = 'For a tradeable, unranked copy; your equipped item may not be tradeable.';
  if (relic || subject.kind === 'relic') {
    variant.subtype = (refinement ?? 'Intact').toLowerCase();
    if (/requiem eterna/i.test(name)) { variant.subtype = 'intact'; refinement = 'Unrefined'; note = 'WFM labels this unrefined relic “Intact”.'; }
    if (item.subtypes.length && !item.subtypes.includes(variant.subtype)) return null;
    qualifier = refinement ?? 'Intact';
  } else if (item.subtypes.length) {
    variant.subtype = item.subtypes.includes('blueprint') ? 'blueprint' : item.subtypes.length === 1 ? item.subtypes[0] : undefined;
    if (!variant.subtype) return null;
    qualifier = [qualifier, variant.subtype].filter(Boolean).join(' · ');
  }
  if (item.maxRank !== undefined) variant.rank = 0;
  if (item.maxCharges !== undefined) variant.charges = item.maxCharges;
  if (item.maxAmberStars !== undefined) variant.amberStars = 0;
  if (item.maxCyanStars !== undefined) variant.cyanStars = 0;
  return { item, variant, qualifier, label: `${item.name}${qualifier && !['set', 'blueprint'].includes(qualifier) ? ` · ${qualifier}` : ''}`, note };
}

function readVariant(row: Record<string, unknown>, legacy = false): MarketVariant {
  const result: MarketVariant = {};
  if (typeof row.subtype === 'string') result.subtype = row.subtype;
  for (const key of variantFields) {
    const oldKey = key === 'rank' ? 'mod_rank' : key === 'amberStars' ? 'amber_stars' : key === 'cyanStars' ? 'cyan_stars' : key;
    const value = nonnegative(row[key] ?? (legacy ? row[oldKey] : undefined));
    if (value !== undefined) result[key] = value;
  }
  return result;
}
export function parseMarketOrders(value: unknown): MarketOrder[] {
  const data = record(value).data;
  if (!Array.isArray(data)) throw new Error('WFM returned invalid orders.');
  return data.flatMap(raw => {
    const row = record(raw), user = record(row.user), price = nonnegative(row.platinum), quantity = nonnegative(row.quantity), perTrade = nonnegative(row.perTrade ?? 1);
    if (row.visible !== true || !['buy', 'sell'].includes(String(row.type)) || !price || !quantity || !perTrade || perTrade > 6 || typeof row.id !== 'string' || typeof user.id !== 'string' || !['online', 'ingame', 'offline'].includes(String(user.status))) return [];
    return [{ id: row.id, type: row.type as MarketOrder['type'], price: price / perTrade, quantity, perTrade, userId: user.id, status: user.status as MarketOrder['status'], ...readVariant(row) }];
  });
}
export function parseMarketHistory(value: unknown): MarketHistory {
  const payload = record(record(value).payload), closed = record(payload.statistics_closed), live = record(payload.statistics_live);
  if (!Array.isArray(closed['90days']) || !Array.isArray(closed['48hours']) || !Array.isArray(live['90days']) || !Array.isArray(live['48hours'])) throw new Error('WFM price history is unavailable.');
  const result: MarketHistory = { '48hours': [], '90days': [] };
  for (const range of ['48hours', '90days'] as const) for (const [rows, kind] of [[closed[range], 'closed'], [live[range], 'live']] as const) {
    for (const raw of rows as unknown[]) {
      const row = record(raw), time = typeof row.datetime === 'string' ? Date.parse(row.datetime) : NaN;
      const type = kind === 'closed' ? 'closed' : row.order_type;
      if (!Number.isFinite(time) || !['closed', 'buy', 'sell'].includes(String(type))) continue;
      result[range].push({ time: new Date(time).toISOString(), type: type as MarketHistoryRow['type'], volume: nonnegative(row.volume) ?? null,
        min: nonnegative(row.min_price) ?? null, max: nonnegative(row.max_price) ?? null, median: nonnegative(row.median) ?? null,
        average: nonnegative(row.avg_price) ?? null, weighted: nonnegative(row.wa_price) ?? null, ...readVariant(row, true) });
    }
  }
  return result;
}
export function matchesMarketVariant(row: MarketVariant, variant: MarketVariant): boolean {
  // Missing dimensions are unknown, not a different refinement or rank zero.
  return (!variant.subtype || row.subtype === variant.subtype) && variantFields.every(key => variant[key] === undefined || row[key] === variant[key]);
}
export const median = (values: number[]): number | null => { if (!values.length) return null; const ordered = [...values].sort((a, b) => a - b), middle = Math.floor(ordered.length / 2); return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2; };
function stats(orders: MarketOrder[]): PriceStats {
  const prices = orders.map(order => order.price);
  return { min: prices.length ? Math.min(...prices) : null, max: prices.length ? Math.max(...prices) : null, mean: prices.length ? prices.reduce((sum, price) => sum + price, 0) / prices.length : null, median: median(prices), listings: prices.length, units: orders.reduce((sum, order) => sum + order.quantity, 0) };
}
export function quoteMarketOrders(orders: MarketOrder[], variant: MarketVariant): MarketQuote {
  const matching = orders.filter(order => matchesMarketVariant(order, variant));
  const online = matching.filter(order => order.status !== 'offline');
  const sells = online.filter(order => order.type === 'sell'), buys = online.filter(order => order.type === 'buy');
  const sellers = new Map<string, number>();
  for (const order of sells) sellers.set(order.userId, Math.min(order.price, sellers.get(order.userId) ?? Infinity));
  const cheapest = [...sellers.values()].sort((a, b) => a - b).slice(0, 5), sell = stats(sells), buy = stats(buys);
  return { price: median(cheapest), sample: cheapest.length, sell, buy, offline: matching.length - online.length, ingame: online.filter(order => order.status === 'ingame').length, spread: sell.min !== null && buy.max !== null ? sell.min - buy.max : null };
}
export function marketSamples(history: MarketHistory, variant: MarketVariant, range: keyof MarketHistory): PriceSample[] {
  const samples = new Map<string, PriceSample>();
  for (const row of history[range].filter(row => matchesMarketVariant(row, variant))) {
    const sample = samples.get(row.time) ?? { time: row.time, closed: null, sell: null, buy: null, volume: null, min: null, max: null, average: null, weighted: null };
    sample[row.type] = row.median;
    if (row.type === 'closed') Object.assign(sample, { volume: row.volume, min: row.min, max: row.max, average: row.average, weighted: row.weighted });
    samples.set(row.time, sample);
  }
  return [...samples.values()].sort((a, b) => a.time.localeCompare(b.time));
}
export const platinum = (value: number | null | undefined) => value === null || value === undefined ? '—' : `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} platinum`;
export const marketUrl = (target: MarketTarget) => `https://warframe.market/items/${target.item.slug}`;
