import type { CSSProperties } from 'react';
import type { Upgrade } from '../lib/modder';
import artwork from '../data/arsenalArt.json';
import maskData from '../data/maskIcons.json';

const setCrests: Record<string, string> = artwork.crests;
const maskIcons: Record<string, string> = maskData;

export type GameIconName = 'warframe' | 'primary' | 'secondary' | 'melee' | 'aura' | 'stance' | 'exilus' | 'all' | 'search' | 'copy' | 'edit' | 'add' | 'back' | 'forward' | 'down' | 'close' | 'stats' | 'reactor' | 'polarize' | 'swap';
export function GameIcon({ name, size = 24 }: { name: GameIconName; size?: number }) {
  return <span className="modder-game-icon" aria-hidden="true" style={{ width: size, height: size, maskImage: `url(${maskIcons[`images/arsenal/${name}.png`] ?? `images/arsenal/${name}.png`})` }} />;
}

const damageSymbols: Record<string, string> = Object.fromEntries(['impact', 'puncture', 'slash', 'heat', 'cold', 'electricity', 'toxin', 'blast', 'radiation', 'gas', 'magnetic', 'viral', 'corrosive', 'void'].map(type => [type, type]));
damageSymbols.tau = 'sentient';
damageSymbols.true = 'true';
export function DamageIcon({ type }: { type: string }) {
  return damageSymbols[type] ? <img className="modder-damage-icon" src={`images/arsenal/damage-${damageSymbols[type]}.png`} alt="" aria-hidden="true" draggable={false} /> : null;
}

export const rankText = (mod: Upgrade, rank: number) => [...new Set((mod.ranks[rank] ?? ['Rank unavailable']).flatMap(text => text.split('\n')).map(line => line.trim()).filter(Boolean))].join('\n');

// The texture names are the game's original polarity names.
const symbols: Record<string, string> = { madurai: 'Triangle', vazarin: 'Point', naramon: 'Circle', zenurik: 'Mark', unairu: 'Ward', penjaga: 'Precept', umbra: 'Umbra', universal: 'Universal' };
export function PolarityIcon({ polarity }: { polarity: string }) {
  return symbols[polarity] ? <span className="modder-polarity" aria-hidden="true" style={{ maskImage: `url(${maskIcons[`images/modding/Polarity${symbols[polarity]}.png`] ?? `images/modding/Polarity${symbols[polarity]}.png`})` }} /> : <span className="modder-no-polarity" aria-hidden="true" />;
}

// Frame composition, rarity colors, drain backers and rank dots adapted from
// Reuzehagel/arsenyx at de051e179996b9b39459e86f55036ea7361319c0:
// apps/web/src/components/build-editor/mod-card-frame.tsx and mod-card.tsx,
// apps/web/src/lib/mod-card-config.ts. Copyright (c) 2026 Reuzehagel, MIT.
// See /licenses/arsenyx-MIT.txt and /images/modding/SOURCES.md.
const rarityStyles: Record<string, { prefix: string; color: string }> = {
  common: { prefix: 'Bronze', color: '#C79989' },
  uncommon: { prefix: 'Silver', color: '#BEC0C2' },
  rare: { prefix: 'Gold', color: '#FBECC4' },
  legendary: { prefix: 'Legendary', color: '#DFDFDF' },
  amalgam: { prefix: 'Amalgam', color: '#98D9EB' },
  galvanized: { prefix: 'Galvanized', color: '#7CB8E4' },
};

function modAppearance(mod?: Upgrade) {
  const rarity = mod?.name.startsWith('Amalgam ') ? 'amalgam' : mod?.name.startsWith('Galvanized ') ? 'galvanized' : mod?.rarity?.toLowerCase() ?? 'uncommon';
  const key = rarity in rarityStyles ? rarity : 'uncommon';
  return { rarity: key, ...rarityStyles[key] };
}

function RankDots({ rank, maxRank }: { rank: number; maxRank: number }) {
  return maxRank > 0 && <span className="modder-rank-pips" aria-hidden="true">{Array.from({ length: maxRank }, (_, i) => <i key={i} className={i < rank ? 'is-ranked' : ''} />)}</span>;
}

export function UpgradeFace({ mod, rank = 0, drain, polarity = '', label, arcane = false, match = '', unavailable = false }: { mod?: Upgrade; rank?: number; drain?: number; polarity?: string; label: string; arcane?: boolean; match?: string; unavailable?: boolean }) {
  const { rarity, prefix, color } = modAppearance(mod);
  const path = `images/modding/frames/${rarity}/`;
  const backer = rarity === 'amalgam' || rarity === 'galvanized' ? 'Silver' : prefix;
  const name = mod?.name ?? (unavailable ? 'Unavailable upgrade' : arcane ? 'Empty arcane slot' : label);
  const style = { '--rarity-color': color } as CSSProperties;
  const crest = mod?.set && setCrests[mod.id.match(/\/Sets\/([^/]+)\//)?.[1] ?? ''];

  if (arcane) return <span className={`modder-face is-arcane${mod ? ' is-filled' : ''}`} style={style} data-rarity={rarity}>
    <span className="modder-arcane-socket" aria-hidden="true">
      {!mod && <img className="modder-empty-arcane" src="images/arsenal/arcane.png" alt="" draggable={false} />}
      {mod && <img className="modder-arcane-surround" src="images/arsenal/arcaneSurround.png" alt="" draggable={false} />}
      {mod?.image && <img className="modder-arcane-art" src={mod.image} alt="" loading="lazy" draggable={false} />}
    </span>
    <strong className="modder-face-name">{name}</strong>
    {mod && <span className="modder-arcane-ranks" role="img" aria-label={`Rank ${rank} of ${mod.maxRank}`}>{Array.from({ length: mod.maxRank }, (_, index) => <i key={index} className={index < rank ? 'is-ranked' : ''} />)}</span>}
  </span>;

  if (!mod && !unavailable) return <span className="modder-face is-empty">
    <span className="modder-empty-glass" aria-hidden="true" />
    <span className="modder-empty-rim" aria-hidden="true" />
    <img className="modder-glass-glow" src="images/arsenal/modGlass.png" alt="" draggable={false} />
    <span className="modder-empty-label">{label}</span>
    {label === 'Exilus' && <span className="modder-exilus-symbol"><GameIcon name="exilus" size={40} /></span>}
    {(label === 'Aura' || label === 'Stance') && <span className="modder-aura-symbol"><GameIcon name={label === 'Aura' ? 'aura' : 'stance'} size={22} /></span>}
    {polarity && <span className="modder-empty-polarity"><PolarityIcon polarity={polarity} /></span>}
  </span>;

  return <span className={`modder-face${mod ? ' is-filled' : ' is-empty'}${rarity === 'amalgam' || rarity === 'galvanized' ? ' is-ornate' : ''}`} data-rarity={rarity} style={style}>
    <span className="modder-face-interior">
      {mod?.image && <img src={mod.image} alt="" loading="lazy" draggable={false} />}
      <span className="modder-face-tint" />
    </span>
    <img className="modder-frame-top" src={`${path}${prefix}FrameTop.webp`} alt="" draggable={false} />
    <img className="modder-frame-bottom" src={`${path}${prefix}FrameBottom.webp`} alt="" draggable={false} />
    {crest && <img className="modder-set-crest" src={crest} alt="" draggable={false} />}
    <span className="modder-face-label">{label}</span>
    {(mod?.compat === 'AURA' || mod?.type === 'Stance Mod' || mod?.exilus) && <span className="modder-aura-symbol"><GameIcon name={mod.compat === 'AURA' ? 'aura' : mod.type === 'Stance Mod' ? 'stance' : 'exilus'} size={20} /></span>}
    {(mod || polarity) && <span className={`modder-face-drain ${match}`} title={match === 'is-match' ? 'Matching polarity' : match === 'is-mismatch' ? 'Mismatched polarity' : undefined}>
      {mod && <img src={`${path}${backer}TopRightBacker.webp`} alt="" draggable={false} />}
      <span>{mod && drain}<PolarityIcon polarity={polarity} /></span>
    </span>}
    <strong className="modder-face-name">{name}</strong>
    {mod && rank === mod.maxRank && mod.maxRank > 0 && <img className="modder-rank-complete" src={`${path}RankCompleteLine.webp`} alt="" draggable={false} />}
    {mod && <RankDots rank={rank} maxRank={mod.maxRank} />}
  </span>;
}
