import { Fragment, lazy, Suspense, useEffect, useRef, useState } from 'react';
import type { MarketItem, MarketOrder, MarketSubject, MarketTarget, MarketQuote, MarketSnapshot } from '../lib/market';
import { marketPlatforms, platinum } from '../lib/market';
import { loadMarket, marketCatalogPath, marketPath, marketQuote, marketTarget, useMarketPlatform, useMarketResource } from '../lib/marketClient';
import { Tooltip } from './Tooltip';
import { Currency } from './Currency';
import './Market.css';

const MarketDialog = lazy(() => import('./MarketDialog').then(module => ({ default: module.MarketDialog })));

export function MarketPrice({ subject }: { subject: MarketSubject }) {
  const host = useRef<HTMLSpanElement>(null), [visible, setVisible] = useState(false), [open, setOpen] = useState(false);
  const platform = useMarketPlatform();
  useEffect(() => {
    const observer = new IntersectionObserver(entries => { setVisible(entries.some(entry => entry.isIntersecting)); }, { rootMargin: '100px' });
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const catalog = useMarketResource<MarketItem[]>(marketCatalogPath, visible);
  const target = catalog.snapshot ? marketTarget(subject) : null;
  const path = target ? marketPath('orders', target.item.slug, platform) : marketCatalogPath;
  const orders = useMarketResource<MarketOrder[]>(path, visible && Boolean(target));
  const quote = target ? marketQuote(target, platform) : undefined;
  const loading = !catalog.snapshot || target && !orders.snapshot;
  const error = target ? orders.error : catalog.error;
  return <span ref={host} className="market-cell">
    <Tooltip className={`market-price${error ? ' is-stale' : ''}`} label={`WFM: ${subject.name}${subject.refinement ? ` ${subject.refinement}` : ''}${target ? `: ${platinum(quote?.price)}, price details` : ''}`} hasPopup={Boolean(target)}
      onClick={() => { if (target) setOpen(true); else void loadMarket(marketCatalogPath, true); }}
      content={target ? <MarketTooltip target={target} quote={quote} snapshot={orders.snapshot} error={orders.error} platform={platform} /> : <span>{catalog.error || (catalog.snapshot ? 'Not listed on Warframe Market.' : 'Loading the Warframe Market catalog…')}</span>}>
      {loading ? error ? 'Unavailable' : '…' : <Currency value={quote?.price} currency="platinum" />}
      {target?.qualifier && subject.kind === 'equipment' && <small>{target.qualifier}</small>}
      {error && orders.snapshot && <small>stale</small>}
    </Tooltip>
    {open && target && <Suspense fallback={<span role="status" className="sr-only">Loading price details…</span>}><MarketDialog target={target} onClose={() => setOpen(false)} /></Suspense>}
  </span>;
}

export function MarketTooltip({ target, quote, snapshot, error, platform }: { target: MarketTarget; quote?: MarketQuote; snapshot?: MarketSnapshot<MarketOrder[]>; error?: string; platform: string }) {
  return <span className="market-tooltip">
    <strong>{target.label}</strong><span>{marketPlatforms.find(item => item.value === platform)?.label} · Platinum per item</span>
    {quote ? <>
      <dl><dt>WFM estimate</dt><dd><Currency value={quote.price} currency="platinum" /></dd>
        <dt>Cheapest sellers sampled</dt><dd>{quote.sample} / 5</dd>
        <dt>Sell min / median / max</dt><dd>{[quote.sell.min, quote.sell.median, quote.sell.max].map((value, index) => <Fragment key={index}>{index > 0 && ' / '}<Currency value={value} currency="platinum" /></Fragment>)}</dd>
        <dt>Sell average</dt><dd><Currency value={quote.sell.mean} currency="platinum" /></dd>
        <dt>Sell listings / units</dt><dd>{quote.sell.listings} / {quote.sell.units}</dd>
        <dt>Best buy / buy median</dt><dd><Currency value={quote.buy.max} currency="platinum" /> / <Currency value={quote.buy.median} currency="platinum" /></dd>
        <dt>Buy average / listings</dt><dd><Currency value={quote.buy.mean} currency="platinum" /> / {quote.buy.listings}</dd>
        <dt>Buy–sell spread</dt><dd><Currency value={quote.spread} currency="platinum" /></dd>
        <dt>Offline listings excluded</dt><dd>{quote.offline}</dd>
      </dl>
      <span>Median of up to five cheapest distinct online sellers. Asking prices, not confirmed trades.</span>
      {quote.sample > 0 && quote.sample < 3 && <span className="market-caution">Thin market: fewer than three sellers.</span>}
      {!quote.sample && <span>No matching online sellers. History may still be available.</span>}
    </> : <span>{error || 'Loading online orders…'}</span>}
    {target.variant.rank !== undefined && <span>Rank {target.variant.rank}{target.variant.charges !== undefined ? ` · ${target.variant.charges} charges` : ''}</span>}
    {target.variant.amberStars !== undefined && <span>Unfilled sculpture</span>}
    {target.note && <span>{target.note}</span>}
    {snapshot && <span>{error || snapshot.stale ? 'Stale · ' : 'Updated '}{new Date(snapshot.fetchedAt).toLocaleString()}</span>}
    <span>Click for history and market details.</span>
  </span>;
}
