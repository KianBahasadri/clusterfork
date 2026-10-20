import dropData from './sampleDrops.json' with { type: 'json' };
import { catalog } from './catalog';
import { relicNames } from './relicCatalog';
import { equipItem, mods, arcanes } from '../lib/modder';
import { newArsenal } from '../lib/modderModel';
import type { ArsenalBuild } from '../lib/modderModel';
import type { CraftingPlan } from '../lib/crafting';
import type { SavedDropView } from '../lib/account';
import type { DropDataset } from '../lib/dropTables';
import type { MarketHistory, MarketHistoryRow, MarketItem, MarketOrder, MarketSnapshot } from '../lib/market';
import type { OcrResult } from '../lib/ocr';
import type { ScreenshotSource } from '../lib/matching';

export const sampleDropDataset = dropData as unknown as DropDataset;

export function defaultCraftingPlans(): CraftingPlan[] {
  const boltor = catalog.find(item => item.name === 'Boltor');
  const rhino = catalog.find(item => item.name === 'Rhino');
  const plans: CraftingPlan[] = [];
  if (boltor?.recipes?.[0]) {
    const recipe = boltor.recipes[0];
    const polymer = recipe.ingredients.find(part => part.name === 'Polymer Bundle');
    plans.push({
      id: 'sample-plan-boltor',
      itemId: boltor.id,
      quantity: 1,
      completed: [],
      progress: polymer ? { [`root/${encodeURIComponent(recipe.id)}/${encodeURIComponent(polymer.id)}`]: Math.floor(polymer.count / 2) } : {},
      choices: {},
    });
  }
  if (rhino?.recipes?.[0]) {
    const recipe = rhino.recipes[0];
    const firstPart = recipe.ingredients[0];
    plans.push({
      id: 'sample-plan-rhino',
      itemId: rhino.id,
      quantity: 1,
      completed: firstPart ? [`root/${encodeURIComponent(recipe.id)}/${encodeURIComponent(firstPart.id)}`] : [],
      progress: {},
      choices: {},
    });
  }
  return plans;
}

export function defaultDropViews(): SavedDropView[] {
  return [
    {
      name: 'Rare Relic Rewards',
      view: {
        query: '',
        filters: { category: { kind: 'choices', values: ['Relics'] }, rarity: { kind: 'choices', values: ['Rare'] } },
        sorting: [{ key: 'chance', direction: -1 }],
        browse: { mode: 'relic' },
      },
    },
    {
      name: 'Cetus & Orb Vallis Bounties',
      view: {
        query: '',
        filters: { category: { kind: 'choices', values: ['Cetus Bounty Rewards', 'Orb Vallis Bounty Rewards'] } },
        sorting: [{ key: 'chance', direction: -1 }],
        browse: { mode: 'bounty' },
      },
    },
  ];
}

function findMod(name: string) {
  return mods.find(m => m.name === name);
}
function findArcane(name: string) {
  return arcanes.find(a => a.name === name);
}

export function defaultArsenals(): ArsenalBuild[] {
  const buildA = newArsenal();
  buildA.id = 'sample-arsenal-a';
  buildA.name = 'Config A · Corrosive Crit';

  const buildB = newArsenal();
  buildB.id = 'sample-arsenal-b';
  buildB.name = 'Config B · Viral Heat';

  const excal = catalog.find(item => item.name === 'Excalibur');
  const bratonP = catalog.find(item => item.name === 'Braton Prime');
  const lexP = catalog.find(item => item.name === 'Lex Prime');
  const orthosP = catalog.find(item => item.name === 'Orthos Prime');

  for (const [build, viral] of [[buildA, false], [buildB, true]] as const) {
    if (excal) {
      const eq = equipItem(excal, 'Warframe');
      const aura = findMod('Steel Charge');
      if (aura) eq.special = { id: aura.id, rank: aura.maxRank, polarity: 'madurai', enabled: true, stacks: 0 };
      const exilus = findMod('Power Drift');
      if (exilus) eq.exilus = { id: exilus.id, rank: exilus.maxRank, polarity: 'zenurik', enabled: true, stacks: 0 };
      for (const [idx, modName] of ['Umbral Intensify', 'Primed Continuity', 'Stretch', 'Streamline', 'Vitality', 'Steel Fiber', 'Adaptation', 'Augur Secrets'].entries()) {
        const mod = findMod(modName);
        if (mod) eq.mods[idx] = { id: mod.id, rank: mod.maxRank, polarity: eq.mods[idx].polarity || mod.polarity as any, enabled: true, stacks: 0 };
      }
      const arc0 = findArcane('Molt Augmented'), arc1 = findArcane('Arcane Energize');
      if (arc0) eq.arcanes[0] = { id: arc0.id, rank: arc0.maxRank, polarity: '', enabled: true, stacks: viral ? 250 : 120 };
      if (arc1) eq.arcanes[1] = { id: arc1.id, rank: arc1.maxRank, polarity: '', enabled: true, stacks: 0 };
      build.equipment.Warframe = eq;
    }
    if (bratonP) {
      const eq = equipItem(bratonP, 'Primary');
      const modList = viral
        ? ['Serration', 'Galvanized Chamber', 'Critical Delay', 'Vital Sense', 'Malignant Force', 'Rime Rounds', 'Hellfire', 'Speed Trigger']
        : ['Serration', 'Split Chamber', 'Point Strike', 'Vital Sense', 'Infected Clip', 'Stormbringer', 'Hellfire', 'Vigilante Armaments'];
      for (const [idx, modName] of modList.entries()) {
        const mod = findMod(modName);
        if (mod) eq.mods[idx] = { id: mod.id, rank: mod.maxRank, polarity: eq.mods[idx].polarity || mod.polarity as any, enabled: true, stacks: modName.startsWith('Galvanized') ? 4 : 0 };
      }
      const exilus = findMod('Vigilante Supplies');
      if (exilus) eq.exilus = { id: exilus.id, rank: exilus.maxRank, polarity: 'naramon', enabled: true, stacks: 0 };
      const arc = findArcane('Primary Merciless');
      if (arc) eq.arcanes[0] = { id: arc.id, rank: arc.maxRank, polarity: '', enabled: true, stacks: viral ? 12 : 6 };
      build.equipment.Primary = eq;
    }
    if (lexP) {
      const eq = equipItem(lexP, 'Secondary');
      for (const [idx, modName] of ['Hornet Strike', 'Barrel Diffusion', 'Lethal Torrent', 'Primed Pistol Gambit', 'Primed Target Cracker', 'Convulsion', 'Pathogen Rounds', 'Heated Charge'].entries()) {
        const mod = findMod(modName);
        if (mod) eq.mods[idx] = { id: mod.id, rank: mod.maxRank, polarity: eq.mods[idx].polarity || mod.polarity as any, enabled: true, stacks: 0 };
      }
      const arc = findArcane('Secondary Merciless');
      if (arc) eq.arcanes[0] = { id: arc.id, rank: arc.maxRank, polarity: '', enabled: true, stacks: 8 };
      build.equipment.Secondary = eq;
    }
    if (orthosP) {
      const eq = equipItem(orthosP, 'Melee');
      const stance = findMod('Shimmering Blight');
      if (stance) eq.special = { id: stance.id, rank: stance.maxRank, polarity: 'zenurik', enabled: true, stacks: 0 };
      for (const [idx, modName] of ['Condition Overload', 'Blood Rush', 'Organ Shatter', 'Weeping Wounds', 'Berserker Fury', 'Primed Reach', 'Virulent Scourge', 'Vicious Frost'].entries()) {
        const mod = findMod(modName);
        if (mod) eq.mods[idx] = { id: mod.id, rank: mod.maxRank, polarity: eq.mods[idx].polarity || mod.polarity as any, enabled: true, stacks: modName === 'Berserker Fury' ? 2 : 0 };
      }
      const arc = findArcane('Melee Influence');
      if (arc) eq.arcanes[0] = { id: arc.id, rank: arc.maxRank, polarity: '', enabled: true, stacks: 0 };
      build.equipment.Melee = eq;
    }
  }
  return [buildA, buildB];
}

const slugify = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');

const sampleMarketCatalog: MarketItem[] = [
  ...catalog.filter(item => item.variant === 'Prime' || item.tradable).map((item, idx): MarketItem => {
    const isPrime = item.variant === 'Prime';
    const isBp = !isPrime && item.name === 'Ignis Wraith';
    const name = isPrime ? `${item.name} Set` : isBp ? `${item.name} Blueprint` : item.name;
    return {
      id: `wfm-eq-${idx}`,
      slug: slugify(name),
      name,
      gameRef: item.id,
      tags: isPrime ? ['set', 'prime', item.slot === 'Warframe' ? 'warframe' : 'weapon'] : isBp ? ['blueprint', 'weapon'] : ['weapon'],
      subtypes: [],
      ...(isPrime ? { ducats: 100 } : {}),
    };
  }),
  ...relicNames.map((name, idx): MarketItem => ({
    id: `wfm-relic-${idx}`,
    slug: slugify(`${name} Relic`),
    name: `${name} Relic`,
    gameRef: '',
    tags: ['relic'],
    subtypes: ['intact', 'exceptional', 'flawless', 'radiant'],
  })),
];

function seedFromSlug(slug: string): number {
  let hash = 0;
  for (let i = 0; i < slug.length; i++) hash = (hash * 31 + slug.charCodeAt(i)) | 0;
  return Math.abs(hash);
}

function buildOrders(slug: string): MarketOrder[] {
  const seed = seedFromSlug(slug);
  const base = slug.endsWith('_relic') ? 4 + (seed % 9) : 18 + (seed % 65);
  const isRelic = slug.endsWith('_relic');
  const subtypes = isRelic ? (['intact', 'exceptional', 'flawless', 'radiant'] as const) : ([undefined] as const);
  const orders: MarketOrder[] = [];
  for (const subtype of subtypes) {
    const mult = subtype === 'radiant' ? 2.2 : subtype === 'flawless' ? 1.6 : subtype === 'exceptional' ? 1.25 : 1;
    const center = Math.max(2, Math.round(base * mult));
    [0, 1, 2, 4, 6].forEach((offset, idx) => {
      orders.push({
        id: `${slug}-${subtype ?? 'base'}-sell-${idx}`,
        type: 'sell',
        price: center + offset,
        quantity: 2 + ((seed + idx) % 5),
        perTrade: 1,
        userId: `tenno-seller-${idx}`,
        status: idx < 3 ? 'ingame' : 'online',
        ...(subtype ? { subtype } : {}),
      });
    });
    [2, 3, 5].forEach((offset, idx) => {
      orders.push({
        id: `${slug}-${subtype ?? 'base'}-buy-${idx}`,
        type: 'buy',
        price: Math.max(1, center - offset),
        quantity: 1 + idx,
        perTrade: 1,
        userId: `tenno-buyer-${idx}`,
        status: idx === 0 ? 'ingame' : 'online',
        ...(subtype ? { subtype } : {}),
      });
    });
    orders.push({
      id: `${slug}-${subtype ?? 'base'}-offline`,
      type: 'sell',
      price: Math.max(1, center - 1),
      quantity: 1,
      perTrade: 1,
      userId: 'tenno-offline',
      status: 'offline',
      ...(subtype ? { subtype } : {}),
    });
  }
  return orders;
}

function buildHistory(slug: string): MarketHistory {
  const seed = seedFromSlug(slug);
  const isRelic = slug.endsWith('_relic');
  const base = isRelic ? 4 + (seed % 9) : 18 + (seed % 65);
  const subtypes = isRelic ? (['intact', 'exceptional', 'flawless', 'radiant'] as const) : ([undefined] as const);
  const makeBins = (stepMs: number, count: number): MarketHistoryRow[] => {
    const rows: MarketHistoryRow[] = [];
    for (const subtype of subtypes) {
      const mult = subtype === 'radiant' ? 2.2 : subtype === 'flawless' ? 1.6 : subtype === 'exceptional' ? 1.25 : 1;
      const center = Math.max(2, Math.round(base * mult));
      for (let i = 0; i < count; i++) {
        const time = new Date(Date.UTC(2026, 9, 18, 0) - (count - 1 - i) * stepMs).toISOString();
        const wave = Math.round(Math.sin((i + (seed % 7)) * 0.35) * Math.max(1, center * 0.12));
        const closedMed = Math.max(1, center + wave);
        const vol = 8 + ((seed + i * 3) % 24);
        rows.push({
          time, type: 'closed', volume: vol,
          min: Math.max(1, closedMed - 3), max: closedMed + 4,
          median: closedMed, average: closedMed + 0.4, weighted: closedMed + 0.2,
          ...(subtype ? { subtype } : {}),
        });
        rows.push({
          time, type: 'sell', volume: vol,
          min: closedMed, max: closedMed + 5,
          median: closedMed + 2, average: closedMed + 2.2, weighted: closedMed + 2.1,
          ...(subtype ? { subtype } : {}),
        });
        rows.push({
          time, type: 'buy', volume: Math.max(2, Math.floor(vol * 0.7)),
          min: Math.max(1, closedMed - 5), max: Math.max(1, closedMed - 1),
          median: Math.max(1, closedMed - 2), average: Math.max(1, closedMed - 2.1), weighted: Math.max(1, closedMed - 2),
          ...(subtype ? { subtype } : {}),
        });
      }
    }
    return rows;
  };
  return {
    '90days': makeBins(86_400_000, 30),
    '48hours': makeBins(3_600_000, 24),
  };
}

export function sampleMarketResponse(path: string): MarketSnapshot<unknown> {
  const fetchedAt = new Date().toISOString();
  if (path === '/api/wfm/items') {
    return { data: sampleMarketCatalog, fetchedAt, stale: false };
  }
  const match = /^\/api\/wfm\/(orders|history)\/([^?]+)/.exec(path);
  const kind = match?.[1] ?? 'orders';
  const slug = match?.[2] ?? 'braton_prime_set';
  return {
    data: kind === 'history' ? buildHistory(slug) : buildOrders(slug),
    fetchedAt,
    stale: false,
  };
}

export function sampleOcrResult(source: ScreenshotSource): OcrResult {
  if (source === 'relics') {
    const lines = [
      { text: 'x4', confidence: 95, bbox: { x0: 210, y0: 340, x1: 248, y1: 368 }, pass: 0 },
      { text: 'Lith A13 Relic [Intact]', confidence: 94, bbox: { x0: 200, y0: 560, x1: 395, y1: 590 }, pass: 0 },
      { text: 'x2', confidence: 96, bbox: { x0: 570, y0: 340, x1: 608, y1: 368 }, pass: 0 },
      { text: 'Lith N1 Relic [Radiant]', confidence: 95, bbox: { x0: 560, y0: 560, x1: 755, y1: 590 }, pass: 0 },
      { text: 'x5', confidence: 93, bbox: { x0: 930, y0: 340, x1: 968, y1: 368 }, pass: 0 },
      { text: 'Axi H4 Relic [Intact]', confidence: 92, bbox: { x0: 920, y0: 560, x1: 1110, y1: 590 }, pass: 0 },
      { text: 'Lith $18 Relic [Radiant]', confidence: 82, bbox: { x0: 1280, y0: 560, x1: 1475, y1: 590 }, pass: 0 },
    ];
    return { text: lines.map(l => l.text).join('\\n'), lines, imageSize: { width: 1920, height: 1200 } };
  }
  if (source === 'codex') {
    const lines = [
      { text: 'Braton Prime', confidence: 96, masteryBadge: true, bbox: { x0: 180, y0: 260, x1: 360, y1: 292 } },
      { text: 'Nataruk', confidence: 95, masteryBadge: true, bbox: { x0: 420, y0: 260, x1: 600, y1: 292 } },
      { text: 'Lex Prime', confidence: 94, masteryBadge: true, bbox: { x0: 660, y0: 260, x1: 840, y1: 292 } },
      { text: 'Rubico Prime', confidence: 91, masteryBadge: false, bbox: { x0: 900, y0: 260, x1: 1080, y1: 292 } },
      { text: 'Glaive Prime', confidence: 89, masteryBadge: true, bbox: { x0: 1140, y0: 260, x1: 1320, y1: 292 } },
    ];
    return { text: lines.map(l => l.text).join('\\n'), lines, imageSize: { width: 1920, height: 1200 } };
  }
  const lines = [
    { text: 'Braton Prime MAX RANK', confidence: 95, pass: 0 },
    { text: 'Boltor MAX RANK', confidence: 94, pass: 0 },
    { text: 'Nataruk MAX RANK', confidence: 96, pass: 0 },
    { text: 'Paracesis RANK 38', confidence: 91, pass: 0 },
    { text: 'Ignis Wraith MAX RANK', confidence: 93, pass: 0 },
    { text: 'Grimoire MAX RANK', confidence: 95, pass: 0 },
    { text: 'Orthos Prime MAX RANK', confidence: 61, pass: 0 },
  ];
  return { text: lines.map(l => l.text).join('\\n'), lines, imageSize: { width: 1920, height: 1200 } };
}
