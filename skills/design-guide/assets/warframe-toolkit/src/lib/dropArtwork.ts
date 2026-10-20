import manifest from '../data/drop-art.json' with { type: 'json' };
import type { DropGroup } from './dropExplorer.ts';
import type { DropBrowseMode, DropRow } from './dropTables.ts';

export interface DropArt { src: string; label: string; fit: 'cover' | 'contain' }
const art = manifest as Record<keyof typeof manifest, Record<string, DropArt>>;
const lookup = (entries: Record<string, DropArt>, name: string): DropArt | undefined => Object.hasOwn(entries, name) ? entries[name] : undefined;

export const dropModeArt = art.modes;

/** Exact published names only; never strip Prime, Wraith or other variant names. */
export function rewardArtwork(reward: string): DropArt | undefined {
  return lookup(art.items, reward.trim().toLowerCase()) || relicArtwork(reward);
}

function relicArtwork(name: string): DropArt | undefined {
  const match = /^([A-Za-z]+) [A-Za-z0-9]{1,10} Relic(?: \((?:Intact|Exceptional|Flawless|Radiant)\))?$/.exec(name);
  return match ? lookup(art.relics, match[1]) : undefined;
}

function locationArtwork(location: string): DropArt | undefined {
  return lookup(art.locations, location.replace(/^Event: /, ''));
}

export function sourceArtwork(row: DropRow): DropArt | undefined {
  return lookup(art.sources, row.source.toLowerCase()) || relicArtwork(row.source) || lookup(art.missions, row.mission) || locationArtwork(row.location);
}

export function groupArtwork(group: DropGroup, mode: DropBrowseMode): DropArt | undefined {
  switch (mode) {
    case 'item': return rewardArtwork(group.title);
    case 'relic': return relicArtwork(group.title);
    case 'location': return locationArtwork(group.title);
    case 'mission': return lookup(art.missions, group.title) || group.locations.map(locationArtwork).find(Boolean);
    case 'bounty': return group.locations.map(locationArtwork).find(Boolean);
    case 'source': return sourceArtwork(group.rows[0]);
  }
}

/** Frame colours reflect only the publisher's within-table rarity, never inferred odds. */
export function dropRarityTone(rarity: string): string {
  return ({ 'Very Common': 'common', Common: 'common', Uncommon: 'uncommon', Rare: 'rare', 'Ultra Rare': 'rare', Legendary: 'legendary' } as Record<string, string>)[rarity] || 'neutral';
}
