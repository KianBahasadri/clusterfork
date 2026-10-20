import type { OcrLine } from './matching.ts';
import { createRelic, MAX_RELIC_QUANTITY, parseRelicName, refinements, relicKey } from './relics.ts';
import type { Relic, Refinement } from './relics.ts';

export interface RelicDetection {
  id: string;
  name: string;
  refinement: Refinement | null;
  quantity: number | null;
  rawText: string;
  filename: string;
  confidence: number;
  selected: boolean;
  reviewed: boolean;
  recognizedKey: string;
  issues: string[];
}
type LocatedLine = OcrLine & { bbox: NonNullable<OcrLine['bbox']> };
const middleX = (line: LocatedLine) => (line.bbox.x0 + line.bbox.x1) / 2;
const middleY = (line: LocatedLine) => (line.bbox.y0 + line.bbox.y1) / 2;
const height = (line: LocatedLine) => line.bbox.y1 - line.bbox.y0;
const width = (line: LocatedLine) => line.bbox.x1 - line.bbox.x0;
const nameLabel = /^(Lith|Meso|Neo|Axi|Requiem|Vanguard)\s+(.+?)\s+Relic\b(.*)$/i;
const clean = (text: string) => text.trim().replace(/[\u2018\u2019]/g, "'");
const median = (values: number[]) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];

function readName(line: OcrLine, names: Map<string, string>) {
  const complete = nameLabel.exec(clean(line.text));
  const match = complete ?? /^(Lith|Meso|Neo|Axi|Requiem|Vanguard)\s+([A-Z]\d+|[IV]+|Eterna)\s+(.+)$/i.exec(clean(line.text));
  if (!match) return null;
  const code = match[2].replace(/\s+/g, '');
  // The game's S can read as $. Only suggest that single glyph correction
  // before digits in a complete label, and still require name review. Never
  // substitute letters/digits in an otherwise valid relic code.
  const corrected = Boolean(complete) && /^\$\d+$/.test(code);
  const parsed = parseRelicName(`${match[1]} ${corrected ? `S${code.slice(1)}` : code}`);
  return { name: parsed && names.get(parsed.name.toLowerCase()) || '', tail: match[3].trim(), complete: Boolean(complete), corrected };
}

/** Match text within its grid position; never attach the screen's totals or a
 * neighboring stack's count/refinement. Uncertain fields require review. */
export function matchRelicOcrLines(lines: OcrLine[], catalogNames: string[], filename: string, imageSize?: { width: number; height: number }): RelicDetection[] {
  const names = new Map(catalogNames.map(name => [name.toLowerCase(), name]));
  const located = lines.filter((line): line is LocatedLine => Boolean(line.bbox));
  const labels = located.filter(line => readName(line, names));
  const groups: LocatedLine[][] = [];
  for (const line of labels) {
    const group = groups.find(group => group.some(other => Math.abs(middleX(line) - middleX(other)) < Math.min(width(line), width(other)) * 0.3
      && Math.abs(middleY(line) - middleY(other)) < Math.max(height(line), height(other)) * 1.2));
    if (group) group.push(line); else groups.push([line]);
  }
  const best = groups.map(group => [...group].sort((a, b) => Number(Boolean(readName(b, names)?.name)) - Number(Boolean(readName(a, names)?.name))
    || Number(readName(a, names)?.corrected) - Number(readName(b, names)?.corrected) || b.confidence - a.confidence)[0]);
  if (!best.length) return [];
  const textHeight = median(best.map(height));
  const rows: LocatedLine[][] = [];
  for (const line of [...best].sort((a, b) => a.bbox.y1 - b.bbox.y1)) {
    const row = rows.find(row => Math.abs(row[0].bbox.y1 - line.bbox.y1) <= textHeight * 2.5);
    if (row) row.push(line); else rows.push([line]);
  }
  const steps = rows.flatMap(row => {
    const centers = row.map(middleX).sort((a, b) => a - b);
    return centers.slice(1).map((center, index) => center - centers[index]);
  }).filter(step => step > textHeight * 3);
  const columnStep = steps.length >= 2 && rows.some(row => row.length >= 3) ? median(steps) : null;
  const ordered = best.map((line, index) => ({ line, group: groups[index] })).sort((a, b) => rows.findIndex(row => row.includes(a.line)) - rows.findIndex(row => row.includes(b.line)) || middleX(a.line) - middleX(b.line));
  return ordered.map(({ line, group }) => {
    const parsed = readName(line, names)!;
    const issues: string[] = [];
    const recognizedNames = new Set(group.map(line => readName(line, names)?.name).filter(Boolean));
    if (!parsed.name || !parsed.complete || parsed.corrected || recognizedNames.size > 1 || line.confidence < 65) issues.push('Check name');
    const row = rows.find(row => row.includes(line))!;
    const bottom = Math.max(...row.map(line => line.bbox.y1));
    const rowCounters = columnStep ? located.filter(counter => /^[x×]\s*[\d,]+$/i.test(clean(counter.text)) && counter.confidence >= 65
      && middleX(counter) >= Math.min(...row.map(middleX)) - columnStep * 0.43 && middleX(counter) <= Math.max(...row.map(middleX))
      && middleY(counter) >= bottom - columnStep * 0.9 && middleY(counter) <= bottom - columnStep * 0.5) : [];
    const annotations = located.filter(annotation => !readName(annotation, names) && annotation.confidence >= 40
      && Math.abs(middleX(annotation) - middleX(line)) < width(line) * 0.3
      && annotation.bbox.y0 >= line.bbox.y1 - textHeight * 0.25 && annotation.bbox.y0 <= line.bbox.y1 + textHeight * 0.85);
    const suffixes = [parsed.tail, ...annotations.map(line => clean(line.text))].filter(Boolean);
    const readings = suffixes.map(text => refinements.find(value => text.replace(/[\[\]()]/g, '').trim().toLowerCase() === value.toLowerCase())).filter((value): value is Refinement => Boolean(value));
    const unique = [...new Set(readings)];
    let refinement: Refinement | null = null;
    if (parsed.name === 'Requiem Eterna' && !unique.length) refinement = 'Unrefined';
    else if (unique.length === 1 && unique[0] !== 'Unrefined') refinement = unique[0];
    else if (!suffixes.length && columnStep && rowCounters.length && imageSize && line.bbox.y1 + textHeight < imageSize.height
      && line.bbox.y1 - median(rowCounters.map(middleY)) > columnStep * 0.6
      && Math.max(...row.map(line => line.bbox.y1)) - line.bbox.y1 < textHeight * 0.5) refinement = 'Intact';
    if (!refinement) issues.push('Choose refinement');
    // Counts sit in the upper-left corner. Use the detected grid scale, not a
    // fixed screenshot resolution. A missing count is only a review suggestion.
    const counters = columnStep ? located.filter(counter => /^[x×]\s*[\d,]+$/i.test(clean(counter.text))
      && middleX(counter) >= middleX(line) - columnStep * 0.43 && middleX(counter) < middleX(line) - columnStep * 0.08
      && middleY(counter) >= bottom - columnStep * 0.9 && middleY(counter) <= bottom - columnStep * 0.5) : [];
    const counts = [...new Set(counters.map(counter => Number(counter.text.replace(/[^\d]/g, ''))))];
    let quantity: number | null = counts.length === 1 && counts[0] > 0 && counts[0] <= MAX_RELIC_QUANTITY ? counts[0] : counts.length ? null : 1;
    if (counts.length > 1 || quantity === null) issues.push('Conflicting quantities');
    else if (!counters.length) issues.push('Confirm quantity 1');
    else if (Math.max(...counters.map(counter => counter.confidence)) < 65) { quantity = null; issues.push('Check quantity'); }
    const rawText = [...new Set([line.text, ...annotations.map(line => line.text), ...counters.map(line => line.text)])].join(' · ');
    const recognizedKey = parsed.name && refinement ? relicKey({ name: parsed.name, refinement }) : `${parsed.name || line.text}|unknown:${Math.round(middleX(line))}:${Math.round(middleY(line))}`;
    return { id: crypto.randomUUID(), name: parsed.name, refinement, quantity, rawText, filename, confidence: Math.round(line.confidence), selected: issues.length === 0, reviewed: false, recognizedKey, issues };
  });
}

export function reviewRelicDetection(detection: RelicDetection, patch: Partial<RelicDetection>): RelicDetection {
  return { ...detection, ...patch, reviewed: true };
}

export function appendRelicDetections(current: RelicDetection[], incoming: RelicDetection[]): RelicDetection[] {
  const result = [...current];
  for (const detection of incoming) {
    const key = detection.name && detection.refinement ? relicKey({ name: detection.name, refinement: detection.refinement }) : detection.recognizedKey;
    const index = result.findIndex(previous => previous.recognizedKey === key || previous.name && previous.refinement && relicKey({ name: previous.name, refinement: previous.refinement }) === key);
    if (index < 0) { result.push(detection); continue; }
    const previous = result[index];
    if (previous.reviewed) continue;
    if (previous.quantity !== detection.quantity) {
      result[index] = { ...previous, quantity: null, selected: false, issues: [...new Set([...previous.issues, ...detection.issues, 'Conflicting quantities'])] };
    } else if (detection.issues.length < previous.issues.length || detection.issues.length === previous.issues.length && detection.confidence > previous.confidence) result[index] = { ...detection, id: previous.id };
  }
  return result;
}

export function relicImportError(detections: RelicDetection[]): string | null {
  const keys = new Set<string>();
  for (const detection of detections.filter(detection => detection.selected)) {
    const parsed = parseRelicName(detection.name);
    if (!parsed || !detection.refinement) return 'Choose a name and refinement for every selected relic.';
    if (detection.quantity === null || !Number.isInteger(detection.quantity) || detection.quantity < 0 || detection.quantity > MAX_RELIC_QUANTITY) return 'Confirm a valid quantity for every selected relic.';
    if ((parsed.name === 'Requiem Eterna') !== (detection.refinement === 'Unrefined')) return 'Requiem Eterna is unrefined; other relics need a refinement.';
    const key = relicKey({ name: parsed.name, refinement: detection.refinement });
    if (keys.has(key)) return `Select only one row for ${parsed.name} ${detection.refinement}.`;
    keys.add(key);
  }
  return null;
}

export function mergeRelicObservations(existing: Relic[], observations: Pick<Relic, 'name' | 'refinement' | 'quantity'>[]): { relics: Relic[]; added: number; updated: number } {
  const relics = existing.map(item => ({ ...item }));
  const indices = new Map(relics.map((item, index) => [relicKey(item), index]));
  const seen = new Set<string>();
  let added = 0, updated = 0;
  for (const observation of observations) {
    const parsed = parseRelicName(observation.name);
    if (!parsed || !refinements.includes(observation.refinement) || (parsed.name === 'Requiem Eterna') !== (observation.refinement === 'Unrefined') || !Number.isInteger(observation.quantity) || observation.quantity < 0 || observation.quantity > MAX_RELIC_QUANTITY) throw new Error('Invalid relic observation.');
    const key = relicKey({ ...observation, name: parsed.name });
    if (seen.has(key)) throw new Error('Review duplicate relic observations before importing.');
    seen.add(key);
    const index = indices.get(key);
    if (index === undefined) { relics.push({ ...createRelic(), name: parsed.name, refinement: observation.refinement, quantity: observation.quantity }); added++; }
    else if (relics[index].quantity !== observation.quantity) {
      relics[index] = { ...relics[index], quantity: observation.quantity, updatedAt: new Date().toISOString() }; updated++;
    }
  }
  return { relics, added, updated };
}
