import { Parser } from 'htmlparser2';
import { DROP_TABLES_URL } from './dropTables.ts';
import type { DropDataset, DropRow } from './dropTables.ts';

const clean = (text: string) => text.replace(/\s+/g, ' ').trim();
const percent = (text: string) => { const match = /([\d.]+)\s*%/.exec(text); return match && Number.isFinite(Number(match[1])) ? Number(match[1]) : null; };
const locations: Record<string, string> = { cetusRewards: 'Cetus', solarisRewards: 'Orb Vallis', deimosRewards: 'Cambion Drift', zarimanRewards: 'Zariman', entratiLabRewards: "Albrecht's Laboratories", hexRewards: 'Höllvania' };

/** Parse the publisher's hierarchy, preserving duplicate reward entries and conditional rates. */
export function parseDropTables(html: string, fetchedAt = new Date().toISOString()): DropDataset {
  const rows: DropRow[] = [];
  const sections = new Set<string>();
  let section = '', category = '', heading: { id: string; text: string } | null = null;
  let inTable = false, cells: { tag: string; text: string }[] | null = null, cell: { tag: string; text: string } | null = null;
  let rowAnchor = '', source = '', rotation = '', stage = '', anchor = '', sourceChance: number | null = null;
  let preamble = '', skippedRows = 0, ignoredTag = '';
  const consumeRow = () => {
    if (!cells || !section) return;
    const headers = cells.filter(cell => cell.tag === 'th').map(cell => clean(cell.text));
    const values = cells.filter(cell => cell.tag === 'td').map(cell => clean(cell.text));
    if (headers.length) {
      if (headers[0] === 'Source' && headers.some(text => text.includes('Drop Chance'))) return;
      if (/^Rotation\s+/i.test(headers[0])) { rotation = headers[0].replace(/^Rotation\s+/i, ''); stage = ''; return; }
      if (/^(?:Stage\s|Final\s+Stage)/i.test(headers[0])) { stage = headers[0]; return; }
      source = headers[0]; rotation = ''; stage = ''; sourceChance = null; anchor = rowAnchor || section;
      const chanceHeader = headers.find(text => /Drop Chance:/i.test(text));
      if (chanceHeader) sourceChance = percent(chanceHeader);
      return;
    }
    if (!values.some(Boolean)) return;
    const rate = values.at(-1) ?? '', tableChance = percent(rate);
    if (!source || tableChance === null || values.length < 2) { skippedRows++; return; }
    const inverse = /ByDrop$/.test(section);
    const reward = inverse ? source : values.at(-2)!;
    const rowSource = inverse ? values[0] : source;
    const rollRate = inverse ? percent(values[1]) : sourceChance;
    if (!reward || !rowSource || (/By(?:Avatar|Drop)$/.test(section) && rollRate === null)) { skippedRows++; return; }
    const mission = section === 'missionRewards' ? /^(.*?)\/(.*?)\s*\(([^()]*)\)$/.exec(rowSource) : null;
    const relic = section === 'relicRewards' ? /^(Lith|Meso|Neo|Axi|Requiem)\s+.*?\s*\(([^)]+)\)$/.exec(rowSource) : null;
    rows.push({
      id: `${section}-${rows.length}`, section, category, reward, source: rowSource,
      location: mission?.[1] ?? locations[section] ?? '', mission: mission?.[3] ?? '', rotation, stage,
      era: relic?.[1] ?? '', refinement: relic?.[2] ?? '', rarity: clean(rate.replace(/\([^)]*%\)/, '')),
      tableChance, sourceChance: rollRate,
      // Rates above 100% denote a multi-roll table, whose per-opportunity odds are not specified here.
      chance: tableChance <= 100 && (rollRate === null || rollRate <= 100) ? tableChance * (rollRate ?? 100) / 100 : null,
      anchor,
    });
  };
  const parser = new Parser({
    onopentag(tag, attributes) {
      if (tag === 'script' || tag === 'style') ignoredTag = tag;
      if (tag === 'h3') heading = { id: attributes.id ?? '', text: '' };
      if (tag === 'table') { inTable = true; source = ''; rotation = ''; stage = ''; sourceChance = null; }
      if (inTable && tag === 'tr') { cells = []; rowAnchor = attributes.id ?? ''; }
      if (cells && (tag === 'td' || tag === 'th')) cell = { tag, text: '' };
      if (tag === 'br' && cell) cell.text += ' ';
    },
    ontext(text) {
      if (ignoredTag) return;
      if (heading) heading.text += text;
      if (cell) cell.text += text;
      if (!section && preamble.length < 6000) preamble += text;
    },
    onclosetag(tag) {
      if (tag === ignoredTag) ignoredTag = '';
      if (tag === 'h3' && heading) { section = heading.id; category = clean(heading.text).replace(/:$/, ''); if (section) sections.add(section); heading = null; }
      if ((tag === 'td' || tag === 'th') && cell) { cells?.push(cell); cell = null; }
      if (tag === 'tr') { consumeRow(); cells = null; }
      if (tag === 'table') { inTable = false; cells = null; cell = null; }
    },
  }, { decodeEntities: true });
  parser.end(html);
  // The publisher repeats the same enemy drops in reverse indexes. Use the source index once.
  const canonical = rows.filter(row => !/ByDrop$/.test(row.section) || !sections.has(row.section.replace(/ByDrop$/, 'ByAvatar')));
  if (!canonical.length || !sections.size) throw new Error('The official page did not contain recognizable drop tables. Its format may have changed.');
  const updated = /Last Update:\s*(\d{1,2}\s+\w+,?\s*\d{4})/i.exec(preamble)?.[1].replace(/,\s*/, ', ') ?? '';
  return { rows: canonical, updated, fetchedAt, sourceUrl: DROP_TABLES_URL, skippedRows, reverseIndexRows: rows.length - canonical.length };
}
