import { memo } from 'react';
import { ArrowRight, Check, ChevronRight } from 'lucide-react';
import { formatChance } from '../lib/dropTables';
import { dropModeArt, dropRarityTone, groupArtwork, rewardArtwork, sourceArtwork } from '../lib/dropArtwork';
import type { DropBrowseMode, DropRow } from '../lib/dropTables';
import type { DropGroup } from '../lib/dropExplorer';
import { DropArtwork } from './DropArtwork';
import { DropResults } from './DropResults';

export const dropBrowseOptions = [
  { mode: 'mission', label: 'Mission type', description: 'Rewards by mission and rotation', noun: 'mission types' },
  { mode: 'item', label: 'Item', description: 'Find where an item drops', noun: 'items' },
  { mode: 'location', label: 'Location', description: 'Planets and open-world locations', noun: 'locations' },
  { mode: 'relic', label: 'Relic', description: 'Contents and refinement chances', noun: 'relics' },
  { mode: 'bounty', label: 'Bounty', description: 'Rewards by tier, stage and rotation', noun: 'bounties' },
  { mode: 'source', label: 'Enemy / source', description: 'Enemies, containers and all other sources', noun: 'sources' },
] as const;

export function DropModePicker({ mode, onSelect }: { mode?: DropBrowseMode; onSelect: (mode: DropBrowseMode) => void }) {
  return <div className={`drop-browse-picker ${mode ? 'is-compact' : ''}`}>
    <h2>Browse by</h2>
    <div className="drop-mode-options" role="group" aria-label="Browse drop tables by">
      {dropBrowseOptions.map(option => <button type="button" key={option.mode} className={`drop-mode-button drop-tone-${option.mode}`} aria-label={`Browse by ${option.label.toLowerCase()}`} aria-pressed={mode === option.mode} onClick={() => onSelect(option.mode)}>
        <DropArtwork art={dropModeArt[option.mode]} />
        <span className="drop-mode-copy"><span className="drop-mode-title"><strong>{option.label}</strong><span className="drop-mode-indicator" aria-hidden="true">{mode === option.mode ? <Check size={16} /> : <ArrowRight size={18} />}</span></span><span className="drop-mode-description">{option.description}</span></span>
      </button>)}
    </div>
  </div>;
}

export const DropGroupCard = memo(function DropGroupCard({ group, mode, onSelect }: { group: DropGroup; mode: DropBrowseMode; onSelect: (group: string) => void }) {
  const label = dropBrowseOptions.find(option => option.mode === mode)!.label;
  const preview = mode === 'item' ? group.sources : mode === 'relic' ? group.rewards : mode === 'mission' || mode === 'bounty' ? group.locations : [...new Set(group.rows.map(row => row.mission || row.category))];
  const count = mode === 'item' || mode === 'mission' ? group.sources.length : mode === 'relic' ? new Set(group.rows.map(row => row.refinement)).size : group.rewards.length;
  const noun = mode === 'item' ? 'source' : mode === 'mission' ? 'mission' : mode === 'relic' ? 'refinement' : 'reward';
  return <button type="button" className={`drop-group-card drop-tone-${mode}`} aria-label={`Open ${group.title}`} onClick={() => onSelect(group.key)}>
    <span className="drop-card-heading"><span className="drop-card-type">{label}</span><strong className="drop-group-title">{group.title}</strong></span>
    <DropArtwork art={groupArtwork(group, mode)} />
    <span className="drop-group-body"><span className="drop-group-top"><span>{count.toLocaleString()} {noun}{count !== 1 ? 's' : ''}</span><ChevronRight size={15} aria-hidden="true" /></span>
      <span className="drop-group-preview">{preview.slice(0, 3).join(' · ')}{preview.length > 3 ? ` · +${(preview.length - 3).toLocaleString()}` : ''}</span>
    </span>
    <span className="drop-group-bottom"><span>{group.rows.length.toLocaleString()} entries</span>{mode === 'item' && <span className="drop-group-rate"><small>Highest chance</small><strong>{group.bestChance === null ? 'Multiple rolls' : formatChance(group.bestChance)}</strong></span>}</span>
  </button>;
});

export const DropRewardCards = memo(function DropRewardCards({ rows, mode, onOpen }: { rows: DropRow[]; mode: DropBrowseMode; onOpen: (row: DropRow) => void }) {
  return <DropResults className="drop-reward-grid">{rows.map(row => <button type="button" className={`drop-result-card drop-rarity-${dropRarityTone(row.rarity)}`} key={row.id}
    aria-label={`${row.reward} from ${row.source}: details`} onClick={() => onOpen(row)}>
    <span className="drop-card-heading"><span className="drop-card-type">{row.category}</span><strong className="drop-result-title">{mode === 'item' ? row.source : row.reward}</strong></span>
    <DropArtwork art={mode === 'item' ? sourceArtwork(row) : rewardArtwork(row.reward)} />
    <span className="drop-result-main"><span className="drop-result-rarity">{row.rarity || 'Rarity unspecified'}</span><span className="drop-result-source">{mode === 'item' ? row.reward : row.source}</span>
      <span className="drop-result-tags">{row.rotation && <span className="drop-rotation">Rotation {row.rotation}</span>}{row.stage && <span>{row.stage}</span>}{row.refinement && <span>{row.refinement}</span>}</span>
    </span>
    <span className={`drop-result-rate ${row.chance === null ? 'is-unknown' : ''}`}><span><small>Drop chance</small><strong>{row.chance === null ? 'Multiple rolls' : formatChance(row.chance)}</strong></span><span className="drop-result-more">Rates & odds <ChevronRight size={13} aria-hidden="true" /></span>{row.chance !== null && <span className="drop-chance-track" aria-hidden="true"><span style={{ width: `${Math.min(100, Math.max(0, row.chance))}%` }} /></span>}</span>
  </button>)}</DropResults>;
});
