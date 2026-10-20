export const MINING_GUIDE_URL = 'https://wiki.warframe.com/w/Mining';

type Rarity = 'Common' | 'Uncommon' | 'Rare' | 'Very rare';

// Raw mining resources, checked against the wiki's ore/gem tables on 2026-10-13.
// Keep separate from generated crafting data and drop-table reward rarity.
const ores: Record<string, Rarity> = {
  Coprun: 'Common', Pyrol: 'Common', Ferros: 'Uncommon', Auron: 'Rare',
  Axidite: 'Common', Travoride: 'Common', Venerol: 'Uncommon', Hesperon: 'Rare',
  Adramalium: 'Common', Bapholite: 'Common', Namalon: 'Uncommon', Thaumica: 'Rare',
};
const gems: Record<string, Rarity> = {
  Azurite: 'Common', Devar: 'Common', Veridos: 'Uncommon', Crimzian: 'Rare', Sentirum: 'Very rare', Nyth: 'Very rare',
  Phasmin: 'Common', Noctrul: 'Common', Goblite: 'Uncommon', Amarast: 'Rare', Zodian: 'Very rare', Thyst: 'Very rare',
  Dagonic: 'Common', Tiametrite: 'Common', Heciphron: 'Uncommon', Necrathene: 'Rare', Embolos: 'Very rare', Xenorhast: 'Very rare',
};

export function miningInfo(name: string) {
  const ore = Object.hasOwn(ores, name), gem = Object.hasOwn(gems, name);
  if (!ore && !gem) return undefined;
  const rarity = ore ? ores[name] : gems[name];
  let cutter: string, advice: string;
  if (ore && rarity !== 'Rare') {
    cutter = 'Nosam Cutter';
    advice = 'Community recommendation for common and uncommon ores. Exact odds by cutter are not published.';
  } else if (ore) {
    cutter = 'Sunpoint Plasma Drill';
    advice = 'All cutters can mine this ore; no confirmed cutter odds advantage. Sunpoint helps find veins. Mine farther from settlements for better rare-resource chances.';
  } else if (rarity === 'Very rare') {
    cutter = 'Sunpoint Plasma Drill';
    // Since Update 36, Advanced Nosam also mines Zodian and Thyst.
    // https://www.warframe.com/en/patch-notes/pc/36-0-0
    advice = 'Can mine the rarest gems in every landscape. Advanced Nosam Cutter also works; basic and Focused Nosam cannot mine this gem.';
  } else {
    cutter = 'Focused Nosam Cutter';
    advice = 'Community pick: excludes the rarest gems from the reward pool, favoring other gems. Exact odds by cutter are not published.';
  }
  return { rarity, kind: ore ? 'Ore' : 'Gem', cutter, advice, cutterUrl: `https://wiki.warframe.com/w/${cutter.replaceAll(' ', '_')}` };
}
