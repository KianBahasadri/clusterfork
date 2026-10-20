import type { PriceSample } from './market.ts';
export type PriceSeries = 'closed' | 'sell' | 'buy';
export const priceSeries: { key: PriceSeries; label: string; dash?: string }[] = [{ key: 'closed', label: 'Closed median' }, { key: 'sell', label: 'Sell median', dash: '7 4' }, { key: 'buy', label: 'Buy median', dash: '2 4' }];
export type ChartRange = { start: number; end: number };
export function zoomRange(range: ChartRange, length: number, factor: number, cursor: number): ChartRange {
  if (length < 2) return { start: 0, end: Math.max(0, length - 1) };
  const width = Math.min(length - 1, Math.max(1, Math.round((range.end - range.start) * factor)));
  const anchor = Math.max(range.start, Math.min(range.end, cursor));
  const ratio = (anchor - range.start) / Math.max(1, range.end - range.start);
  const start = Math.max(0, Math.min(length - 1 - width, Math.round(anchor - width * ratio)));
  return { start, end: start + width };
}
export function panRange(range: ChartRange, length: number, direction: number): ChartRange {
  const width = range.end - range.start;
  const start = Math.max(0, Math.min(length - 1 - width, range.start + direction * Math.max(1, Math.round(width / 3))));
  return { start, end: start + width };
}
export function priceRangeStats(samples: PriceSample[]) {
  const traded = samples.filter(sample => sample.weighted !== null && sample.volume !== null && sample.volume > 0);
  const weight = traded.reduce((sum, sample) => sum + sample.volume!, 0);
  const lows = samples.flatMap(sample => sample.min === null ? [] : [sample.min]), highs = samples.flatMap(sample => sample.max === null ? [] : [sample.max]);
  return { volume: samples.some(sample => sample.volume !== null) ? samples.reduce((sum, sample) => sum + (sample.volume ?? 0), 0) : null,
    weighted: weight ? traded.reduce((sum, sample) => sum + sample.weighted! * sample.volume!, 0) / weight : null,
    min: lows.length ? Math.min(...lows) : null, max: highs.length ? Math.max(...highs) : null };
}
export function marketHistoryCsv(samples: PriceSample[]) {
  return [['Time (UTC)', 'Closed median (platinum)', 'Sell median (platinum)', 'Buy median (platinum)', 'Reported volume', 'Closed min (platinum)', 'Closed max (platinum)', 'Closed mean (platinum)', 'Closed weighted mean (platinum)'],
    ...samples.map(sample => [sample.time, sample.closed, sample.sell, sample.buy, sample.volume, sample.min, sample.max, sample.average, sample.weighted])]
    .map(row => row.map(value => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\r\n');
}
