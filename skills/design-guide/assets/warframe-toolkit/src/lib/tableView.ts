export type ColumnKey = string;
export type TableValue = string | number | boolean | null;
export type FilterChoice = { value: string; label: string };
export type TableColumn = {
  key: ColumnKey; title: string; width: number; kind?: 'boolean' | 'number'; required?: boolean; wrap?: boolean;
  choices?: 'values' | FilterChoice[];
  number?: { min: number; max: number; step?: number | 'any'; unknown?: boolean };
};
export interface TableModel<Row> {
  value: (item: Row, key: ColumnKey) => TableValue;
  compare?: (a: Row, b: Row, key: ColumnKey) => number | undefined;
  matchesChoice?: (item: Row, key: ColumnKey, choice: string) => boolean | undefined;
}
export type ColumnSort = { key: ColumnKey; direction: 1 | -1 };
export type ColumnFilter = { kind: 'text'; value: string } | { kind: 'choice'; values: string[]; excludedValues?: string[] } | { kind: 'number'; operator: 'equals' | 'atLeast' | 'atMost' | 'unknown'; value: number };
export type ColumnFilters = Partial<Record<ColumnKey, ColumnFilter>>;
export type ColumnPreferences = { order: ColumnKey[]; visible: ColumnKey[] };
export type ColumnDropTarget = { key: ColumnKey; edge: 'before' | 'after' };

export function moveColumn(order: ColumnKey[], key: ColumnKey, target: ColumnDropTarget): ColumnKey[] {
  if (key === target.key || !order.includes(key) || !order.includes(target.key)) return order;
  const next = order.filter(column => column !== key);
  next.splice(next.indexOf(target.key) + (target.edge === 'after' ? 1 : 0), 0, key);
  return next;
}

export function sortRows<Row>(items: Row[], sorting: ColumnSort[], model: TableModel<Row>): Row[] {
  return [...items].sort((a, b) => {
    for (const sort of sorting) {
      const av = model.value(a, sort.key), bv = model.value(b, sort.key);
      if (av === null || bv === null) {
        if (av !== bv) return av === null ? 1 : -1;
        continue;
      }
      const result = model.compare?.(a, b, sort.key) ?? (((typeof av === 'boolean' && typeof bv === 'boolean') || (typeof av === 'number' && typeof bv === 'number'))
        ? Number(av) - Number(bv) : String(av).localeCompare(String(bv), undefined, { sensitivity: 'base', numeric: true }));
      if (result) return result * sort.direction;
    }
    return 0;
  });
}

export function filterRows<Row>(items: Row[], filters: ColumnFilters, model: TableModel<Row>): Row[] {
  return items.filter(item => Object.entries(filters).every(([key, filter]) => {
    if (!filter) return true;
    const value = model.value(item, key);
    if (filter.kind === 'text') return String(value ?? '').toLocaleLowerCase().includes(filter.value.trim().toLocaleLowerCase());
    if (filter.kind === 'choice') {
      const matches = (choice: string) => model.matchesChoice?.(item, key, choice) ?? String(value) === choice;
      return (!filter.values.length || filter.values.some(matches)) && !filter.excludedValues?.some(matches);
    }
    if (filter.operator === 'unknown') return value === null;
    if (typeof value !== 'number') return false;
    return filter.operator === 'atLeast' ? value >= filter.value : filter.operator === 'atMost' ? value <= filter.value : value === filter.value;
  }));
}

export function setColumnSort(sorting: ColumnSort[], key: ColumnKey, direction: 1 | -1 | 0): ColumnSort[] {
  if (!direction) return sorting.filter(sort => sort.key !== key);
  return sorting.some(sort => sort.key === key) ? sorting.map(sort => sort.key === key ? { key, direction } : sort) : [...sorting, { key, direction }];
}

export function moveColumnSort(sorting: ColumnSort[], key: ColumnKey, priority: number): ColumnSort[] {
  const selected = sorting.find(sort => sort.key === key);
  if (!selected) return sorting;
  const remaining = sorting.filter(sort => sort.key !== key);
  remaining.splice(Math.max(0, Math.min(remaining.length, priority)), 0, selected);
  return remaining;
}

export function filterLabel(filter: ColumnFilter, choices?: FilterChoice[]): string {
  if (filter.kind === 'text') return `Contains “${filter.value}”`;
  if (filter.kind === 'choice') {
    const labels = (values: string[]) => values.map(value => choices?.find(choice => choice.value === value)?.label ?? value).join(' or ');
    const included = labels(filter.values) || 'Any';
    return filter.excludedValues?.length ? `${included} except ${labels(filter.excludedValues)}` : included;
  }
  return filter.operator === 'unknown' ? 'Unknown' : `${filter.operator === 'atLeast' ? 'At least' : filter.operator === 'atMost' ? 'At most' : 'Equals'} ${filter.value}`;
}

export function filterChoices<Row>(column: TableColumn, items: Row[], model: TableModel<Row>): FilterChoice[] | undefined {
  if (Array.isArray(column.choices)) return column.choices;
  if (column.kind === 'boolean') return [{ value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }];
  if (column.choices === 'values') return [...new Set(items.map(item => String(model.value(item, column.key))))].sort().map(value => ({ value, label: value }));
  return undefined;
}

export function parseColumnPreferences(saved: unknown, columns: TableColumn[], defaults: string[], retired: string[] = []): ColumnPreferences {
  const keys = columns.map(column => column.key);
  const validKeys = (value: unknown): value is string[] => Array.isArray(value) && value.every(key => typeof key === 'string' && (keys.includes(key) || retired.includes(key)));
  let order = keys, visible = defaults;
  // Older preferences store visibility alone; keep their original column layout.
  if (validKeys(saved)) visible = saved;
  else if (saved && typeof saved === 'object' && 'order' in saved && 'visible' in saved && validKeys(saved.order) && validKeys(saved.visible)) {
    order = [...new Set([...saved.order.filter(key => keys.includes(key)), ...keys])];
    visible = saved.visible;
  }
  return { order, visible: order.filter(key => visible.includes(key) || columns.some(column => column.key === key && column.required)) };
}

export function loadColumnPreferences(key: string, columns: TableColumn[], defaults: string[], retired: string[] = [], legacyKey?: string): ColumnPreferences {
  try {
    const raw = localStorage.getItem(key);
    if (raw !== null || !legacyKey) return parseColumnPreferences(JSON.parse(raw ?? 'null'), columns, defaults, retired);
    const migrated = parseColumnPreferences(JSON.parse(localStorage.getItem(legacyKey) ?? 'null'), columns, defaults, retired);
    // Snapshot the old shared layout once, so later weapon changes cannot alter Warframes.
    try { localStorage.setItem(key, JSON.stringify(migrated)); } catch { /* The current view still works without browser storage. */ }
    return migrated;
  } catch { /* Keep unreadable preferences intact and use the default view. */ }
  return parseColumnPreferences(null, columns, defaults);
}
