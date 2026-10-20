import { normalizeName } from './model.ts';
import type { CatalogItem } from './model.ts';

export interface OcrLine { text: string; confidence: number; pass?: number; masteryBadge?: boolean; bbox?: { x0: number; y0: number; x1: number; y1: number } }
export interface Detection {
  id: string;
  item: CatalogItem;
  recognizedItemId: string;
  reviewed: boolean;
  rawText: string;
  confidence: number;
  exact: boolean;
  selected: boolean;
  owned: boolean;
  mastered: boolean;
  masterySource: 'rank' | 'text' | 'badge' | 'review' | null;
  rank: number | null;
  filename: string;
}
export type ScreenshotSource = 'inventory' | 'codex' | 'relics';

function distance(a: string, b: string): number {
  let row = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const next = [i];
    for (let j = 1; j <= b.length; j++) next[j] = Math.min(next[j - 1] + 1, row[j] + 1, row[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    row = next;
  }
  return row[b.length];
}

function tokens(text: string): string[] {
  return text.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/\bmk\s*[1il]\s*-?\s*/g, 'mk1 ').replace(/[^a-z0-9]+/g, ' ').trim().split(/\s+/).filter(Boolean);
}

const component = /\b(blueprints?|chassis|neuroptics|systems|receiver|barrel|stock|handle|blade|ornament|grip|link)\b/i;
const variantWords = /\b(prime|umbra|wraith|vandal|prisma|kuva|tenet|dex|coda|mk1|sancti|secura|synoid|rakta|vaykor|telos|mara)\b/gi;
const rankAnnotation = /\[\s*\d+\s*\]|\brank\s*[:\-]?\s*\d+\b|\bunranked\b/gi;
const statusAnnotation = /\b(?:(?:not\s+|un)?mastered|(?:not\s+|un)?owned|max(?:\s+rank)?|equip|purchase)\b/gi;

function readRank(text: string): number | null {
  const values = [...text.matchAll(rankAnnotation)].map(match => /unranked/i.test(match[0]) ? 0 : Number(match[0].match(/\d+/)![0]));
  return values.length && values.every(value => value === values[0]) && values[0] <= 40 ? values[0] : null;
}

export function rankEstablishesMastery(item: CatalogItem, rank: number | null): boolean {
  // Modular equipment needs gilding, which an inventory label cannot establish.
  return item.masterable === true && !item.modular && item.slot !== 'Amp' && rank !== null && rank === item.maxRank;
}

export function reviewDetection(detection: Detection, patch: Partial<Detection>): Detection {
  const next = { ...detection, ...patch, reviewed: true };
  if (patch.mastered !== undefined) next.masterySource = 'review';
  else if ((patch.item || patch.rank !== undefined) && detection.masterySource !== 'review') {
    const explicit = !patch.item && (detection.masterySource === 'text' || detection.masterySource === 'badge');
    next.mastered = explicit || rankEstablishesMastery(next.item, next.rank);
    next.masterySource = explicit ? detection.masterySource : next.mastered ? 'rank' : null;
  }
  return next;
}

export function appendDetections(current: Detection[], incoming: Detection[]): Detection[] {
  const result = [...current];
  for (const detection of incoming) {
    // Remember the original match after a name correction, so another image
    // cannot reintroduce the rejected name or overwrite the corrected row.
    const index = result.findIndex(previous => previous.item.id === detection.item.id || previous.recognizedItemId === detection.item.id);
    if (index === -1) { result.push(detection); continue; }
    const previous = result[index];
    if (previous.reviewed) continue;
    const best = (detection.exact && !previous.exact) || (detection.exact === previous.exact && detection.confidence > previous.confidence) ? detection : previous;
    result[index] = {
      ...best, id: previous.id,
      mastered: previous.mastered || detection.mastered, owned: previous.owned || detection.owned,
      masterySource: previous.masterySource === 'badge' || detection.masterySource === 'badge' ? 'badge'
        : previous.masterySource === 'text' || detection.masterySource === 'text' ? 'text' : previous.masterySource ?? detection.masterySource,
      rank: detection.rank ?? previous.rank,
    };
  }
  return result;
}

function wrappedRanks(lines: OcrLine[], source: ScreenshotSource): Map<OcrLine, OcrLine> {
  const paired = new Map<OcrLine, OcrLine[]>();
  if (source !== 'inventory') return new Map();
  for (const number of lines) {
    if (!number.bbox || number.confidence < 80 || !/^(?:\[\s*\d{1,2}\s*\]|\d{1,2})$/.test(number.text.trim())) continue;
    const rank = Number(number.text.replace(/\D/g, ''));
    if (rank > 40) continue;
    const box = number.bbox;
    const height = box.y1 - box.y0;
    // Bare digits only count inside a row of other bracketed inventory labels.
    // A price or currency elsewhere on screen is not an equipment rank.
    if (!number.text.includes('[') && lines.filter(line => line.pass === number.pass && line.bbox && /\[\s*\d{1,2}\s*\]/.test(line.text) && Math.abs((line.bbox.y0 + line.bbox.y1) / 2 - (box.y0 + box.y1) / 2) < height).length < 2) continue;
    const names = lines.filter(line => {
      if (!line.bbox || line.pass !== number.pass || !/[a-z]/i.test(line.text) || line.text.match(rankAnnotation)) return false;
      const name = line.bbox;
      const nameHeight = name.y1 - name.y0;
      const width = name.x1 - name.x0;
      const gap = box.y0 - name.y1;
      return gap >= 0 && gap <= nameHeight * 0.65 && height >= nameHeight * 0.35 && height <= nameHeight * 1.3
        && box.x0 >= name.x0 && box.x1 <= name.x1 && box.x1 - box.x0 <= width * 0.6
        && Math.abs((box.x0 + box.x1) / 2 - (name.x0 + name.x1) / 2) <= width * 0.2;
    });
    if (names.length === 1) paired.set(names[0], [...paired.get(names[0]) || [], number]);
  }
  return new Map([...paired].filter(([, numbers]) => numbers.length === 1).map(([name, numbers]) => [name, numbers[0]]));
}

export function matchOcrLines(lines: OcrLine[], catalog: CatalogItem[], filename: string, source: ScreenshotSource): Detection[] {
  if (source === 'relics') return [];
  const indexed = catalog.map(item => ({ item, parts: tokens(item.name), key: normalizeName(item.name) })).sort((a, b) => b.parts.length - a.parts.length || b.key.length - a.key.length);
  const found = new Map<string, Detection>();
  const wrapped = wrappedRanks(lines, source);
  for (const line of lines) {
    if (component.test(line.text)) continue;
    const clean = line.text.replace(rankAnnotation, '').replace(statusAnnotation, '').trim();
    const parts = tokens(clean);
    if (!parts.length) continue;
    const key = parts.join('');
    // Tiny, uncertain fragments often come from icons rather than weapon names.
    if (key.length <= 2 && line.confidence < 65 && readRank(line.text) === null) continue;
    const occupied = new Set<number>();
    const matches: { item: CatalogItem; exact: boolean; score: number }[] = [];
    const whole = indexed.find(candidate => candidate.key === key);
    if (whole) matches.push({ item: whole.item, exact: true, score: line.confidence });
    else for (const candidate of indexed) {
      for (let start = 0; start <= parts.length - candidate.parts.length; start++) {
        if (candidate.parts.every((token, i) => parts[start + i] === token && !occupied.has(start + i))) {
          matches.push({ item: candidate.item, exact: true, score: line.confidence });
          candidate.parts.forEach((_, i) => occupied.add(start + i));
          break;
        }
      }
    }
    // Every word must belong to a name. A damaged modifier such as "Probotcis"
    // must never leave behind an apparently exact base weapon ("Cernos").
    if (!whole && occupied.size !== parts.length) matches.length = 0;
    if (!matches.length) {
      if (key.length >= 5 && key.length <= 40) {
        const modifiers = (parts.join(' ').match(variantWords) || []).sort().join(' ');
        const ranked = indexed.filter(candidate => Math.abs(candidate.key.length - key.length) <= 3
          // Never discard a damaged modifier to suggest the intact base name.
          && !key.includes(candidate.key)
          && modifiers === (candidate.parts.join(' ').match(variantWords) || []).sort().join(' '))
          .map(candidate => ({ item: candidate.item, similarity: 1 - distance(key, candidate.key) / Math.max(key.length, candidate.key.length) }))
          .sort((a, b) => b.similarity - a.similarity);
        if (ranked[0]?.similarity >= 0.78 && (!ranked[1] || ranked[0].similarity - ranked[1].similarity >= 0.08)) {
          matches.push({ item: ranked[0].item, exact: false, score: Math.min(line.confidence, ranked[0].similarity * 100) });
        }
      }
    }
    const continuation = wrapped.get(line);
    const rankValue = continuation ? Number(continuation.text.replace(/\D/g, '')) : readRank(line.text);
    const rank = matches.length === 1 && rankValue !== null && rankValue <= (matches[0].item.maxRank ?? 40) ? rankValue : null;
    const explicitMastery = matches.length === 1 && /\bmastered\b/i.test(line.text) && !/\b(?:not|un)\s*mastered\b/i.test(line.text);
    for (const match of matches) {
      const owned = source === 'inventory' && !/\b(?:not owned|unowned|purchase)\b/i.test(line.text);
      const badgeMastery = source === 'codex' && matches.length === 1 && line.masteryBadge === true && match.item.masterable === true;
      const mastered = badgeMastery || explicitMastery || rankEstablishesMastery(match.item, rank);
      const detection: Detection = {
        id: crypto.randomUUID(), item: match.item, rawText: continuation ? `${line.text}\n${continuation.text}` : line.text, filename,
        recognizedItemId: match.item.id, reviewed: false,
        confidence: Math.round(match.score), exact: match.exact,
        selected: match.exact && match.score >= 65, owned, mastered, rank,
        masterySource: badgeMastery ? 'badge' : explicitMastery ? 'text' : mastered ? 'rank' : null,
      };
      const prior = found.get(match.item.id);
      if (!prior || (match.exact && !prior.exact) || (match.exact === prior.exact && match.score > prior.confidence)) found.set(match.item.id, detection);
    }
  }
  return [...found.values()];
}
