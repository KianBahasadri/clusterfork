export const DROP_TABLES_URL = 'https://warframe.com/droptables';
export const DROP_REFRESH_INTERVAL_MS = 60 * 60_000;
export const DROP_RETRY_INTERVAL_MS = 5 * 60_000;
export const DROP_CACHE_POLL_MS = 60_000;

export interface DropRow {
  id: string;
  section: string;
  category: string;
  reward: string;
  source: string;
  location: string;
  mission: string;
  rotation: string;
  stage: string;
  era: string;
  refinement: string;
  rarity: string;
  /** Percent per opportunity. Unknown for source tables reporting multiple rolls. */
  chance: number | null;
  tableChance: number;
  sourceChance: number | null;
  anchor: string;
}

export interface DropDataset {
  rows: DropRow[];
  updated: string;
  fetchedAt: string;
  sourceUrl: string;
  skippedRows: number;
  reverseIndexRows: number;
}

/** Validate cached/public data before it can replace the last usable snapshot. */
export function isDropDataset(value: unknown): value is DropDataset {
  if (!value || typeof value !== 'object') return false;
  const data = value as DropDataset;
  const textFields = ['id', 'section', 'category', 'reward', 'source', 'location', 'mission', 'rotation', 'stage', 'era', 'refinement', 'rarity', 'anchor'] as const;
  const nonnegative = (number: unknown): number is number => typeof number === 'number' && Number.isFinite(number) && number >= 0;
  return typeof data.fetchedAt === 'string' && Number.isFinite(Date.parse(data.fetchedAt)) && typeof data.updated === 'string' && data.sourceUrl === DROP_TABLES_URL
    && Number.isInteger(data.skippedRows) && data.skippedRows >= 0 && Number.isInteger(data.reverseIndexRows) && data.reverseIndexRows >= 0
    && Array.isArray(data.rows) && data.rows.length > 0 && data.rows.every(row => row && textFields.every(key => typeof row[key] === 'string')
      && nonnegative(row.tableChance) && (row.chance === null || nonnegative(row.chance) && row.chance <= 100) && (row.sourceChance === null || nonnegative(row.sourceChance)));
}

export const dropColumns = [
  { key: 'reward', title: 'Reward', required: true },
  { key: 'source', title: 'Source' },
  { key: 'category', title: 'Category' },
  { key: 'location', title: 'Location' },
  { key: 'mission', title: 'Mission type' },
  { key: 'rotation', title: 'Rotation' },
  { key: 'stage', title: 'Stage' },
  { key: 'era', title: 'Relic era' },
  { key: 'refinement', title: 'Refinement' },
  { key: 'rarity', title: 'Rarity' },
  { key: 'chance', title: 'Drop chance', numeric: true },
  { key: 'tableChance', title: 'Within table', numeric: true },
  { key: 'sourceChance', title: 'Table roll rate', numeric: true },
] satisfies { key: keyof DropRow; title: string; required?: boolean; numeric?: boolean }[];
export type DropColumnKey = typeof dropColumns[number]['key'];
export type DropColumn = { key: DropColumnKey; title: string; required?: boolean; numeric?: boolean };
export type DropSort = { key: DropColumnKey; direction: 1 | -1 };
export type DropFilter = { kind: 'values'; values: string[] } | { kind: 'text'; value: string; operator: 'contains' | 'equals' | 'excludes' } | { kind: 'range'; min: string; max: string; unknown?: boolean };
export type DropFilters = Partial<Record<DropColumnKey, DropFilter>>;
export const dropBrowseModes = ['mission', 'item', 'location', 'relic', 'bounty', 'source'] as const;
export type DropBrowseMode = typeof dropBrowseModes[number];
export interface DropView {
  query: string; filters: DropFilters; sorting: DropSort[]; columns: DropColumnKey[];
  /** Retained only for old saved views and links; results are no longer paginated. */
  pageSize?: number;
  /** Optional for compatibility with saved views from the table interface. */
  browse?: { mode: DropBrowseMode; group?: string };
}
export const dropPageSizes = [12, 24, 48, 96, 25, 50, 100, 250];
export const defaultDropColumns: DropColumnKey[] = ['reward', 'source', 'category', 'rotation', 'stage', 'chance'];
export const defaultDropView = (): DropView => ({ query: '', filters: {}, sorting: [], columns: [...defaultDropColumns] });
export const numericDropColumns = new Set<DropColumnKey>(['chance', 'tableChance', 'sourceChance']);
const columnKeys = new Set<string>(dropColumns.map(column => column.key));
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

export function formatChance(value: number | null): string {
  if (value === null) return '—';
  if (value > 0 && value < 0.0001) return '<0.0001%';
  return `${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}%`;
}

export function dropFilterLabel(filter: DropFilter): string {
  if (filter.kind === 'values') return filter.values.map(value => value || 'Not specified').join(', ');
  if (filter.kind === 'text') return `${filter.operator === 'excludes' ? 'excludes ' : filter.operator === 'equals' ? '= ' : ''}${filter.value}`;
  return filter.unknown ? 'Multiple rolls / unspecified' : `${filter.min || '0'}–${filter.max || '∞'}%`;
}

const aliases: Record<string, DropColumnKey> = { reward: 'reward', source: 'source', category: 'category', location: 'location', mission: 'mission', rotation: 'rotation', stage: 'stage', era: 'era', refinement: 'refinement', rarity: 'rarity', chance: 'chance', within: 'tableChance', roll: 'sourceChance' };
export function compileDropQuery(query: string): { matches: (row: DropRow) => boolean; error: string } {
  const tests: ((row: DropRow) => boolean)[] = [];
  if ((query.match(/"/g) ?? []).length % 2) return { matches: () => false, error: 'Close the quoted phrase to search.' };
  for (const match of query.matchAll(/(-?)(?:([a-z]+):)?("[^"]*"|[^\s]+)/gi)) {
    const [, negation, field, raw] = match;
    const key = field && Object.hasOwn(aliases, field.toLowerCase()) ? aliases[field.toLowerCase()] : undefined;
    if (field && !key) return { matches: () => false, error: `Unknown search field “${field}”. Open Search help for supported fields.` };
    const value = raw.replace(/^"|"$/g, '').toLocaleLowerCase();
    let test: (row: DropRow) => boolean;
    if (key && numericDropColumns.has(key)) {
      const number = /^(>=|<=|>|<|=)?(\d+(?:\.\d+)?)%?$/.exec(value);
      if (!number) return { matches: () => false, error: `Use a percentage such as ${field}:>=5.` };
      const target = Number(number[2]);
      test = row => { const actual = row[key]; return typeof actual === 'number' && (number[1] === '>=' ? actual >= target : number[1] === '<=' ? actual <= target : number[1] === '>' ? actual > target : number[1] === '<' ? actual < target : actual === target); };
    } else {
      test = row => key ? String(row[key] ?? '').toLocaleLowerCase().includes(value) : [row.reward, row.source, row.category, row.location, row.mission, row.rotation, row.stage, row.era, row.refinement, row.rarity].some(text => text.toLocaleLowerCase().includes(value));
    }
    tests.push(negation ? row => !test(row) : test);
  }
  return { matches: row => tests.every(test => test(row)), error: '' };
}

export function filterDropRows(rows: DropRow[], query: ReturnType<typeof compileDropQuery>, filters: DropFilters): DropRow[] {
  const entries = Object.entries(filters) as [DropColumnKey, DropFilter][];
  return rows.filter(row => query.matches(row) && entries.every(([key, filter]) => {
    const value = row[key];
    if (filter.kind === 'values') return filter.values.includes(String(value ?? ''));
    if (filter.kind === 'range') return filter.unknown ? value === null : typeof value === 'number' && (filter.min === '' || value >= Number(filter.min)) && (filter.max === '' || value <= Number(filter.max));
    const text = String(value ?? '').toLocaleLowerCase(), target = filter.value.toLocaleLowerCase();
    return filter.operator === 'equals' ? text === target : filter.operator === 'excludes' ? !text.includes(target) : text.includes(target);
  }));
}

export function sortDropRows(rows: DropRow[], sorting: DropSort[]): DropRow[] {
  if (!sorting.length) return rows;
  return [...rows].sort((a, b) => {
    for (const { key, direction } of sorting) {
      const av = a[key], bv = b[key];
      if (av === null || bv === null || av === '' || bv === '') {
        if ((av === null || av === '') !== (bv === null || bv === '')) return av === null || av === '' ? 1 : -1;
        continue;
      }
      const order = typeof av === 'number' && typeof bv === 'number' ? av - bv : collator.compare(String(av), String(bv));
      if (order) return order * direction;
    }
    return 0;
  });
}

export function setDropSort(sorts: DropSort[], key: DropColumnKey, direction: 1 | -1 | 0): DropSort[] {
  if (!direction) return sorts.filter(sort => sort.key !== key);
  return sorts.some(sort => sort.key === key) ? sorts.map(sort => sort.key === key ? { key, direction } : sort) : [...sorts, { key, direction }];
}

export function moveDropSort(sorts: DropSort[], key: DropColumnKey, index: number): DropSort[] {
  const selected = sorts.find(sort => sort.key === key);
  if (!selected) return sorts;
  const rest = sorts.filter(sort => sort !== selected);
  rest.splice(Math.max(0, Math.min(rest.length, index)), 0, selected);
  return rest;
}

/** Each attempt is one independent opportunity at the displayed rate. */
export function successChance(percent: number, attempts: number): number {
  if (attempts <= 0 || percent <= 0) return 0;
  if (percent >= 100) return 100;
  return -Math.expm1(Math.floor(attempts) * Math.log1p(-percent / 100)) * 100;
}
export function attemptsForChance(percent: number, target: number): number | null {
  if (target <= 0) return 0;
  if (percent <= 0) return null;
  if (percent >= 100) return 1;
  if (target >= 100) return null;
  return Math.ceil(Math.log1p(-target / 100) / Math.log1p(-percent / 100));
}

function csvCell(value: unknown) {
  let text = String(value ?? '');
  if (/^[\s]*[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}
export function dropRowsCsv(rows: DropRow[], columns: DropColumnKey[]): string {
  const visible = columns.map(key => dropColumns.find(column => column.key === key)!);
  return '\uFEFF' + [visible.map(column => csvCell(column.title)).join(','), ...rows.map(row => visible.map(column => csvCell(row[column.key])).join(','))].join('\r\n');
}

/** View links contain display preferences only; live drop data is never saved in them. */
export function dropViewHash(view: DropView): string {
  return `#droptables?view=${encodeURIComponent(JSON.stringify(view))}`;
}

/** Saved settings must be valid before editing them, so damaged preferences are never overwritten. */
export function isDropView(value: unknown): value is DropView {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const view = value as DropView;
  if (typeof view.query !== 'string' || (view.pageSize !== undefined && !dropPageSizes.includes(view.pageSize))) return false;
  if (view.browse !== undefined && (!view.browse || !dropBrowseModes.includes(view.browse.mode) || (view.browse.group !== undefined && typeof view.browse.group !== 'string'))) return false;
  if (!Array.isArray(view.columns) || !view.columns.includes('reward') || view.columns.some(key => !columnKeys.has(key)) || new Set(view.columns).size !== view.columns.length) return false;
  if (!Array.isArray(view.sorting) || view.sorting.some(sort => !sort || !columnKeys.has(sort.key) || ![1, -1].includes(sort.direction)) || new Set(view.sorting.map(sort => sort.key)).size !== view.sorting.length) return false;
  if (!view.filters || typeof view.filters !== 'object' || Array.isArray(view.filters)) return false;
  return Object.entries(view.filters).every(([key, filter]) => {
    if (!columnKeys.has(key) || !filter || typeof filter !== 'object') return false;
    if (filter.kind === 'values') return Array.isArray(filter.values) && filter.values.every(value => typeof value === 'string');
    if (filter.kind === 'text') return typeof filter.value === 'string' && ['contains', 'equals', 'excludes'].includes(filter.operator);
    if (filter.kind === 'range') return numericDropColumns.has(key as DropColumnKey) && [filter.min, filter.max].every(value => typeof value === 'string' && (value === '' || /^\d+(\.\d+)?$/.test(value))) && (filter.unknown === undefined || typeof filter.unknown === 'boolean');
    return false;
  });
}

export function readDropView(hash: string): DropView {
  const fallback = defaultDropView();
  try {
    const encoded = new URLSearchParams(hash.split('?')[1] ?? '').get('view');
    if (!encoded || encoded.length > 16000) return fallback;
    const value = JSON.parse(encoded) as Record<string, unknown>;
    if (!value || typeof value !== 'object') return fallback;
    if (typeof value.query === 'string') fallback.query = value.query.slice(0, 2000);
    if (dropPageSizes.includes(Number(value.pageSize))) fallback.pageSize = Number(value.pageSize);
    if (value.browse && typeof value.browse === 'object') {
      const browse = value.browse as DropView['browse'];
      if (browse && dropBrowseModes.includes(browse.mode)) fallback.browse = { mode: browse.mode, ...(typeof browse.group === 'string' && browse.group.length <= 2000 ? { group: browse.group } : {}) };
    }
    if (Array.isArray(value.columns)) fallback.columns = [...new Set(['reward', ...value.columns.filter(key => typeof key === 'string' && columnKeys.has(key))])] as DropColumnKey[];
    if (Array.isArray(value.sorting)) for (const sort of value.sorting) {
      if (sort && columnKeys.has(sort.key) && [1, -1].includes(sort.direction) && !fallback.sorting.some(item => item.key === sort.key)) fallback.sorting.push({ key: sort.key, direction: sort.direction });
    }
    if (value.filters && typeof value.filters === 'object') for (const [key, raw] of Object.entries(value.filters)) {
      if (!columnKeys.has(key) || !raw || typeof raw !== 'object') continue;
      const filter = raw as DropFilter;
      if (filter.kind === 'values' && Array.isArray(filter.values) && filter.values.every(value => typeof value === 'string')) fallback.filters[key as DropColumnKey] = { kind: 'values', values: filter.values.slice(0, 100) };
      if (filter.kind === 'text' && typeof filter.value === 'string' && ['contains', 'equals', 'excludes'].includes(filter.operator)) fallback.filters[key as DropColumnKey] = { kind: 'text', operator: filter.operator, value: filter.value.slice(0, 500) };
      if (filter.kind === 'range' && numericDropColumns.has(key as DropColumnKey) && [filter.min, filter.max].every(value => typeof value === 'string' && (value === '' || /^\d+(\.\d+)?$/.test(value)))) fallback.filters[key as DropColumnKey] = { kind: 'range', min: filter.min, max: filter.max, unknown: filter.unknown === true };
    }
    return fallback;
  } catch { return fallback; }
}
