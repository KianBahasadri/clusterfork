import { Fragment, useEffect, useId, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ExternalLink } from 'lucide-react';
import { Dialog } from './Dialog';
import { Select } from './Select';
import { MarketChart, timeLabel } from './MarketChart';
import type { MarketHistory, MarketOrder, MarketTarget, MarketVariant, PriceSample } from '../lib/market';
import { marketPlatforms, marketSamples, marketUrl, quoteMarketOrders } from '../lib/market';
import { marketPath, setMarketPlatform, useMarketPlatform, useMarketResource } from '../lib/marketClient';
import { marketHistoryCsv, panRange, priceRangeStats, priceSeries, zoomRange } from '../lib/marketChart';
import type { PriceSeries } from '../lib/marketChart';
import { downloadFile } from '../lib/storage';
import { setColumnSort, sortRows } from '../lib/tableView';
import type { ColumnSort } from '../lib/tableView';
import { useSwipeTabs } from './useSwipeTabs';
import { Currency, CurrencySymbol } from './Currency';

interface View {
  range: '90days' | '48hours'; start: number; end: number; cursor: number; pinned: boolean;
  tab: 'Chart' | 'Exact data'; visible: PriceSeries[]; area: boolean; zero: boolean; grid: boolean; markers: boolean;
  target: boolean; targetValue: string; sorting: ColumnSort[];
}
const views = new Map<string, View>();
const defaults = (): View => ({ range: '90days', start: 0, end: Infinity, cursor: 0, pinned: false, tab: 'Chart', visible: ['closed', 'sell', 'buy'], area: false, zero: true, grid: true, markers: false, target: false, targetValue: '', sorting: [] });
const dataColumns: { key: keyof PriceSample; label: string }[] = [{ key: 'time', label: 'Time (UTC)' }, { key: 'closed', label: 'Closed median' }, { key: 'sell', label: 'Sell median' }, { key: 'buy', label: 'Buy median' }, { key: 'volume', label: 'Reported volume' }, { key: 'min', label: 'Closed min' }, { key: 'max', label: 'Closed max' }, { key: 'average', label: 'Closed mean' }, { key: 'weighted', label: 'Weighted mean' }];

export function MarketDialog({ target, onClose }: { target: MarketTarget; onClose: () => void }) {
  const platform = useMarketPlatform(), [variant, setVariant] = useState<MarketVariant>(target.variant);
  const orders = useMarketResource<MarketOrder[]>(marketPath('orders', target.item.slug, platform));
  const history = useMarketResource<MarketHistory>(marketPath('history', target.item.slug, platform));
  const quote = useMemo(() => orders.snapshot ? quoteMarketOrders(orders.snapshot.data, variant) : undefined, [orders.snapshot, variant]);
  const choice = (field: keyof MarketVariant, value: string) => setVariant(current => ({ ...current, [field]: field === 'subtype' ? value : Number(value) }));
  return <Dialog title={`${target.item.name} · WFM`} className="market-dialog" initialFocus=".market-controls [role=combobox]" onClose={onClose}>
    <div className="market-dialog-body">
      <div className="market-controls"><label>Market<Select label="WFM market" value={platform} options={marketPlatforms} onChange={value => setMarketPlatform(value as typeof platform)} /></label>
        {target.item.subtypes.length > 0 && target.qualifier !== 'Unrefined' && <label>Type<Select label="WFM item type" value={variant.subtype ?? ''} options={target.item.subtypes.map(value => ({ value, label: value.charAt(0).toUpperCase() + value.slice(1) }))} onChange={value => choice('subtype', value)} /></label>}
        {([['rank', 'maxRank', 'Rank'], ['charges', 'maxCharges', 'Charges'], ['amberStars', 'maxAmberStars', 'Amber stars'], ['cyanStars', 'maxCyanStars', 'Cyan stars']] as const).map(([field, maximum, label]) => target.item[maximum] !== undefined && <label key={field}>{label}<Select label={`WFM ${label.toLowerCase()}`} value={String(variant[field] ?? 0)} options={Array.from({ length: Math.min(100, target.item[maximum]!) + 1 }, (_, value) => ({ value: String(value), label: String(value) }))} onChange={value => choice(field, value)} /></label>)}
      </div>
      {target.note && <p className="small muted">{target.note}</p>}
      <dl className="market-metrics" aria-label="Live market prices"><div><dt>WFM estimate</dt><dd>{quote ? <Currency value={quote.price} currency="platinum" /> : '…'}</dd></div><div><dt>Lowest online sell</dt><dd><Currency value={quote?.sell.min} currency="platinum" /></dd></div><div><dt>Highest online buy</dt><dd><Currency value={quote?.buy.max} currency="platinum" /></dd></div></dl>
      <p className="small muted">Median of the five cheapest distinct online sellers{quote ? ` (${quote.sample} available)` : ''}. Per-item asking prices; offline listings excluded.</p>
      {quote && <dl className="market-order-stats"><div><dt>Sell median / mean / max</dt><dd>{[quote.sell.median, quote.sell.mean, quote.sell.max].map((value, index) => <Fragment key={index}>{index > 0 && ' / '}<Currency value={value} currency="platinum" /></Fragment>)}</dd></div><div><dt>Buy min / median / mean</dt><dd>{[quote.buy.min, quote.buy.median, quote.buy.mean].map((value, index) => <Fragment key={index}>{index > 0 && ' / '}<Currency value={value} currency="platinum" /></Fragment>)}</dd></div><div><dt>Sell listings / units</dt><dd>{quote.sell.listings} / {quote.sell.units}</dd></div><div><dt>Buy listings / units</dt><dd>{quote.buy.listings} / {quote.buy.units}</dd></div><div><dt>Spread</dt><dd><Currency value={quote.spread} currency="platinum" /></dd></div><div><dt>Offline listings excluded</dt><dd>{quote.offline}</dd></div></dl>}
      {quote && quote.sample < 3 && <p className="small market-caution">{quote.sample ? 'Thin market: fewer than three online sellers.' : 'No matching online sellers. No current estimate.'}</p>}
      {(orders.error || orders.snapshot?.stale) && <p className="callout caution" role="status"><strong>{orders.snapshot ? 'Stale prices:' : 'Prices unavailable:'}</strong> {orders.error || orders.snapshot?.error}</p>}
      <p className="small muted">Orders {orders.snapshot ? <time dateTime={orders.snapshot.fetchedAt}>updated {new Date(orders.snapshot.fetchedAt).toLocaleString()}</time> : orders.error ? 'unavailable' : 'loading…'}{target.item.ducats !== undefined && ` · ${target.item.ducats} ducats`}</p>
      <h3>Price history</h3>
      <p className="small muted">WFM reported closed orders and historical listings. Closed orders are user-reported, not verified in-game sales. Missing values stay empty.</p>
      {history.error && <p className="callout caution" role="status"><strong>{history.snapshot ? 'Stale history:' : 'History unavailable:'}</strong> {history.error}</p>}
      {history.snapshot ? <HistoryExplorer key={`${target.item.slug}:${platform}:${JSON.stringify(variant)}`} cacheKey={`${target.item.slug}:${platform}:${JSON.stringify(variant)}`} history={history.snapshot.data} variant={variant} slug={target.item.slug} platform={platform} /> : <p role="status" className="muted small">{history.loading ? 'Loading price history…' : 'No price history is available.'}</p>}
      {history.snapshot && <p className="small muted">History updated <time dateTime={history.snapshot.fetchedAt}>{new Date(history.snapshot.fetchedAt).toLocaleString()}</time></p>}
    </div>
    <footer className="market-source-footer"><a className="button secondary" href={marketUrl(target)} target="_blank" rel="noreferrer"><ExternalLink size={16} aria-hidden="true" />Open on Warframe Market</a><span className="small muted">Cached orders · 5 min · History · 1 hour</span></footer>
  </Dialog>;
}

function HistoryExplorer({ history, variant, slug, platform, cacheKey }: { history: MarketHistory; variant: MarketVariant; slug: string; platform: string; cacheKey: string }) {
  const id = useId(), [view, setView] = useState<View>(() => views.get(cacheKey) ?? defaults());
  const [exportAll, setExportAll] = useState(false), [status, setStatus] = useState('');
  useEffect(() => { views.set(cacheKey, view); if (views.size > 100) views.delete(views.keys().next().value!); }, [cacheKey, view]);
  const samples = useMemo(() => marketSamples(history, variant, view.range), [history, variant, view.range]);
  const start = Math.min(view.start, Math.max(0, samples.length - 2)), end = Math.min(view.end, Math.max(0, samples.length - 1));
  const selection = samples.slice(start, end + 1), cursor = Math.max(start, Math.min(end, view.cursor));
  const stats = priceRangeStats(selection), range = { start, end };
  const sorted = sortRows(selection, view.sorting, { value: (sample, key) => sample[key as keyof PriceSample] });
  const patch = (change: Partial<View>) => setView(current => ({ ...current, ...change }));
  const swipe = useSwipeTabs(view.tab === 'Chart' ? 0 : 1, index => patch({ tab: index === 0 ? 'Chart' : 'Exact data' }));
  useEffect(() => { if (view.cursor < start || view.cursor > end) setView(current => ({ ...current, cursor: Math.max(start, Math.min(end, current.cursor)), pinned: false })); }, [start, end, view.cursor]);
  const zoom = (factor: number) => patch(zoomRange(range, samples.length, factor, cursor));
  const pan = (direction: number) => patch(panRange(range, samples.length, direction));
  const selectPreset = (days: number) => {
    if (days === 2) { patch({ range: '48hours', start: 0, end: Infinity, pinned: false, cursor: 0 }); return; }
    const all = marketSamples(history, variant, '90days'), cutoff = Date.parse(all.at(-1)?.time ?? '') - days * 86_400_000;
    const first = days === 90 ? 0 : Math.max(0, all.findIndex(sample => Date.parse(sample.time) >= cutoff));
    patch({ range: '90days', start: Math.min(first, Math.max(0, all.length - 2)), end: Infinity, cursor: first, pinned: false });
  };
  const target = view.targetValue.trim() === '' ? NaN : Number(view.targetValue), validTarget = Number.isFinite(target) && target >= 0 && target <= 1_000_000;
  const exportCsv = () => {
    const rows = exportAll ? samples : sorted;
    if (!rows.length) return;
    const first = [...rows].sort((a, b) => a.time.localeCompare(b.time))[0].time, last = [...rows].sort((a, b) => a.time.localeCompare(b.time)).at(-1)!.time;
    const variantName = Object.entries(variant).map(([key, value]) => `${key}-${value}`).join('-').replace(/[^a-zA-Z0-9_-]/g, '-');
    downloadFile(`wfm-${slug}-${platform}${variantName ? `-${variantName}` : ''}-${view.range}-${first.slice(0, 19).replaceAll(':', '-')}-${last.slice(0, 19).replaceAll(':', '-')}.csv`, marketHistoryCsv(rows), 'text/csv;charset=utf-8');
    setStatus(`Exported ${rows.length} timestamps${exportAll ? ' from the full dataset' : ' in the selected range'}.`);
  };
  return <section ref={swipe.group} className="market-explorer" aria-label="WFM history explorer">
    <div className="market-chart-toolbar"><div className="market-tabs" role="tablist" aria-label="Price history view">{(['Chart', 'Exact data'] as const).map((tab, index, tabs) => <button key={tab} id={`${id}-tab-${index}`} type="button" role="tab" aria-selected={view.tab === tab} aria-controls={`${id}-panel-${index}`} tabIndex={view.tab === tab ? 0 : -1} onClick={() => patch({ tab })} onKeyDown={event => {
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? 1 : event.key === 'ArrowRight' || event.key === 'ArrowLeft' ? 1 - index : null;
      if (next !== null) { event.preventDefault(); patch({ tab: tabs[next] }); document.getElementById(`${id}-tab-${next}`)?.focus(); }
    }}>{tab}</button>)}</div><button className="button quiet" type="button" onClick={() => { setView(defaults()); setStatus('View reset.'); }}>Reset view</button></div>
    <div className="market-chart-toolbar"><div className="market-presets" role="group" aria-label="History time range">{[90, 30, 7, 2].map(days => <button type="button" className="button quiet" key={days} aria-pressed={days === 2 ? view.range === '48hours' : view.range === '90days' && (days === 90 ? start === 0 : start > 0 && samples[start] && Math.abs(Date.parse(samples.at(-1)!.time) - Date.parse(samples[start].time) - days * 86_400_000) < 86_400_000)} onClick={() => selectPreset(days)}>{days === 2 ? '48 hours' : `${days} days`}</button>)}</div>
      <div className="market-presets" role="group" aria-label="History zoom and pan"><button className="button quiet" type="button" disabled={start === 0} onClick={() => pan(-1)}>Earlier</button><button className="button secondary" type="button" disabled={end - start < 2} onClick={() => zoom(0.5)}>Zoom in</button><button className="button secondary" type="button" disabled={start === 0 && end === samples.length - 1 || samples.length < 2} onClick={() => zoom(2)}>Zoom out</button><button className="button quiet" type="button" disabled={end >= samples.length - 1} onClick={() => pan(1)}>Later</button></div>
    </div>
    {!samples.length && <p role="status" className="small muted">No history for this exact variant and time range.</p>}
    <div className="market-chart-workspace">
      <div className="market-chart-main">
        <div ref={swipe.viewport} className="market-tab-viewport">
        <div role="tabpanel" id={`${id}-panel-0`} aria-labelledby={`${id}-tab-0`} hidden={view.tab !== 'Chart'}><MarketChart samples={selection} options={{ visible: view.visible, area: view.area, zero: view.zero, grid: view.grid, markers: view.markers, target: view.target && validTarget ? target : null }} cursor={cursor - start} pinned={view.pinned} interval={view.range === '48hours' ? 3_600_000 : 86_400_000} onCursor={index => patch({ cursor: index + start })} onPin={index => patch({ cursor: index + start, pinned: true })} onZoom={zoom} onPan={pan} /><div className="market-chart-toolbar"><span className="small muted">{selection.length} timestamps · {view.range === '48hours' ? 'Hourly' : 'Daily'} · UTC</span>{view.pinned && <button className="button quiet" type="button" onClick={() => patch({ pinned: false })}>Unpin sample</button>}</div></div>
        <div role="tabpanel" id={`${id}-panel-1`} aria-labelledby={`${id}-tab-1`} hidden={view.tab !== 'Exact data'}><div className="table-scroll market-data" role="region" aria-label="Exact WFM price history" tabIndex={0}><table><caption className="sr-only">Reported WFM statistics for the selected range. Prices in platinum per item; missing values are unavailable.</caption><thead><tr>{dataColumns.map(column => {
          const priority = view.sorting.findIndex(sort => sort.key === column.key), sort = view.sorting[priority];
          const price = column.key !== 'time' && column.key !== 'volume';
          return <th scope="col" key={column.key} aria-sort={priority === 0 ? sort.direction === 1 ? 'ascending' : 'descending' : undefined}><button type="button" className="button quiet" aria-label={`Sort ${column.label}${price ? ' (platinum)' : ''}`} aria-description={sort ? `Priority ${priority + 1}, ${sort.direction === 1 ? 'ascending' : 'descending'}` : 'Unsorted'} onClick={() => patch({ sorting: setColumnSort(view.sorting, column.key, sort?.direction === 1 ? -1 : sort?.direction === -1 ? 0 : 1) })}>{column.label}{price && <CurrencySymbol currency="platinum" decorative />}{sort && <span aria-hidden="true">{sort.direction === 1 ? <ArrowUp size={14} /> : <ArrowDown size={14} />}{priority + 1}</span>}</button></th>;
        })}</tr></thead><tbody>{sorted.map(sample => <tr key={sample.time}>{dataColumns.map(column => <td key={column.key}>{column.key === 'time' ? <time dateTime={sample.time}>{timeLabel(sample.time)}</time> : sample[column.key]?.toLocaleString(undefined, { maximumFractionDigits: 3 }) ?? '—'}</td>)}</tr>)}</tbody></table></div></div>
        </div>
        <div className="market-range-fields" role="group" aria-label="Custom history range"><label>From <output>{samples[start] ? timeLabel(samples[start].time) + ' UTC' : '—'}</output><input type="range" aria-label="History range start" aria-valuetext={samples[start]?.time} min={0} max={Math.max(0, end - 1)} step={1} value={start} disabled={samples.length < 2} onChange={event => patch({ start: Number(event.target.value) })} /></label><label>To <output>{samples[end] ? timeLabel(samples[end].time) + ' UTC' : '—'}</output><input type="range" aria-label="History range end" aria-valuetext={samples[end]?.time} min={Math.min(start + 1, end)} max={Math.max(0, samples.length - 1)} step={1} value={end} disabled={samples.length < 2} onChange={event => patch({ end: Number(event.target.value) })} /></label></div>
        <dl className="market-range-stats" aria-label="Selected history statistics"><div><dt>Weighted closed mean</dt><dd><Currency value={stats.weighted} currency="platinum" /></dd></div><div><dt>Closed low / high</dt><dd><Currency value={stats.min} currency="platinum" /> / <Currency value={stats.max} currency="platinum" /></dd></div><div><dt>Reported volume</dt><dd>{stats.volume?.toLocaleString() ?? '—'}</dd></div></dl>
      </div>
      <aside className="market-plot-controls" aria-label="Price plot controls" hidden={view.tab !== 'Chart'}>
        <fieldset><legend>Series</legend>{priceSeries.map(series => <label key={series.key}><input type="checkbox" checked={view.visible.includes(series.key)} disabled={view.visible.length === 1 && view.visible.includes(series.key)} onChange={event => patch({ visible: event.target.checked ? [...view.visible, series.key] : view.visible.filter(key => key !== series.key) })} />{series.label}</label>)}</fieldset>
        <fieldset><legend>Display</legend><label><input type="checkbox" checked={view.area} onChange={event => patch({ area: event.target.checked })} />Area fill</label><label><input type="checkbox" checked={view.zero} onChange={event => patch({ zero: event.target.checked })} />Start Y axis at zero</label><label><input type="checkbox" checked={view.grid} onChange={event => patch({ grid: event.target.checked })} />Gridlines</label><label><input type="checkbox" checked={view.markers} onChange={event => patch({ markers: event.target.checked })} />Sample markers</label><label><input type="checkbox" checked={view.target} onChange={event => patch({ target: event.target.checked })} />Target line</label><label className="market-target-field"><span>Target <CurrencySymbol currency="platinum" /></span><input type="number" min={0} max={1_000_000} step="any" value={view.targetValue} disabled={!view.target} aria-invalid={view.target && !validTarget} aria-describedby={view.target && !validTarget ? `${id}-target-error` : undefined} onChange={event => patch({ targetValue: event.target.value })} /></label>{view.target && !validTarget && <span className="market-error" id={`${id}-target-error`} role="alert">Enter a target from 0 to <Currency value={1_000_000} currency="platinum" /></span>}</fieldset>
      </aside>
    </div>
    <div className="market-export-footer"><span className="small muted" role="status">{status}</span><label><input type="checkbox" checked={exportAll} onChange={event => setExportAll(event.target.checked)} />Full dataset</label><button type="button" className="button secondary" disabled={!samples.length} onClick={exportCsv}>{exportAll ? 'Export full CSV' : 'Export range CSV'}</button></div>
  </section>;
}
