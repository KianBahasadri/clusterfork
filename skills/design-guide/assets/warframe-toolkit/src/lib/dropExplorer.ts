import { dropBrowseModes } from './dropTables.ts';
import type { DropBrowseMode, DropRow, DropSort } from './dropTables.ts';

const groupCollator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

export interface DropGroup {
  key: string;
  title: string;
  rows: DropRow[];
  sources: string[];
  rewards: string[];
  locations: string[];
  bestChance: number | null;
}

/** Group only by published facts. Refinements share a relic, but retain separate rows. */
function groupName(row: DropRow, mode: DropBrowseMode): string | null {
  switch (mode) {
    case 'mission': return row.section === 'missionRewards' ? row.mission || 'Unspecified mission type' : null;
    case 'item': return row.reward;
    case 'location': return row.location || null;
    case 'relic': return row.section === 'relicRewards' ? row.refinement && row.source.endsWith(` (${row.refinement})`) ? row.source.slice(0, -row.refinement.length - 3) : row.source : null;
    case 'bounty': return /bounty/i.test(row.category) ? JSON.stringify([row.location, row.source]) : null;
    case 'source': return row.source;
  }
}

export function dropBrowseCounts(rows: DropRow[]): Record<DropBrowseMode, number> {
  return Object.fromEntries(dropBrowseModes.map(mode => [mode, new Set(rows.map(row => groupName(row, mode)).filter(name => name !== null)).size])) as Record<DropBrowseMode, number>;
}

/** No rates are added together, and repeated entries keep their original identities. */
export function groupDropRows(rows: DropRow[], mode: DropBrowseMode, sorting: DropSort[] = []): DropGroup[] {
  const groups = new Map<string, DropGroup>();
  for (const row of rows) {
    const key = groupName(row, mode);
    if (key === null) continue;
    let group = groups.get(key);
    if (!group) {
      group = { key, title: mode === 'bounty' ? row.source : key, rows: [], sources: [], rewards: [], locations: [], bestChance: null };
      groups.set(key, group);
    }
    group.rows.push(row);
    if (row.chance !== null) group.bestChance = Math.max(group.bestChance ?? 0, row.chance);
  }
  const result = [...groups.values()];
  for (const group of result) {
    group.sources = [...new Set(group.rows.map(row => row.source))];
    group.rewards = [...new Set(group.rows.map(row => row.reward))];
    group.locations = [...new Set(group.rows.map(row => row.location).filter(Boolean))];
  }
  // Overview cards compare their displayed highest chance; unknown odds always go last.
  if (sorting[0]?.key === 'chance') return result.sort((a, b) => {
    if (a.bestChance === null || b.bestChance === null) return a.bestChance === b.bestChance ? 0 : a.bestChance === null ? 1 : -1;
    return (a.bestChance - b.bestChance) * sorting[0].direction;
  });
  // Other explicit sorts preserve the order of first occurrence in sorted rows.
  return sorting.length ? result : result.sort((a, b) => groupCollator.compare(a.title, b.title));
}
