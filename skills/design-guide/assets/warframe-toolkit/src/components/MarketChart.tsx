import { useId, useLayoutEffect, useRef, useState } from 'react';
import type { PriceSample } from '../lib/market';
import { platinum } from '../lib/market';
import { priceSeries } from '../lib/marketChart';
import type { PriceSeries } from '../lib/marketChart';
import { Currency, currencySymbols } from './Currency';

export interface PricePlotOptions { visible: PriceSeries[]; area: boolean; zero: boolean; grid: boolean; markers: boolean; target: number | null }
export const timeLabel = (time: string) => new Date(time).toLocaleString(undefined, { timeZone: 'UTC', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });

export function MarketChart({ samples, options, cursor, pinned, interval, onCursor, onPin, onZoom, onPan }: {
  samples: PriceSample[]; options: PricePlotOptions; cursor: number; pinned: boolean; interval: number;
  onCursor: (index: number) => void; onPin: (index: number) => void; onZoom: (factor: number) => void; onPan: (direction: number) => void;
}) {
  const host = useRef<HTMLDivElement>(null), tip = useRef<HTMLDivElement>(null), id = useId();
  const [size, setSize] = useState({ width: 700, height: 300 }), [active, setActive] = useState(false), [announcement, setAnnouncement] = useState('');
  useLayoutEffect(() => { const observer = new ResizeObserver(([entry]) => { if (entry.contentRect.width > 0 && entry.contentRect.height > 0) setSize({ width: entry.contentRect.width, height: entry.contentRect.height }); }); if (host.current) observer.observe(host.current); return () => observer.disconnect(); }, []);
  const values = samples.flatMap(sample => options.visible.flatMap(key => sample[key] === null ? [] : [sample[key]!]));
  if (options.target !== null) values.push(options.target);
  const minimum = options.zero ? 0 : values.length ? Math.max(0, Math.min(...values) * 0.95) : 0;
  const maximum = values.length ? Math.max(minimum + 1, Math.max(...values) * 1.05) : 1;
  const left = size.width < 420 ? 48 : 58, right = size.width - 16, top = 20, bottom = size.height - 32;
  const startTime = Date.parse(samples[0]?.time ?? '') || 0, endTime = Date.parse(samples.at(-1)?.time ?? '') || startTime;
  const x = (index: number) => endTime === startTime ? (left + right) / 2 : left + (Date.parse(samples[index].time) - startTime) / (endTime - startTime) * (right - left);
  const y = (value: number) => bottom - (value - minimum) / (maximum - minimum) * (bottom - top);
  const current = Math.max(0, Math.min(samples.length - 1, cursor)), sample = samples[current];
  const inspect = (index: number, keyboard = false) => { const next = Math.max(0, Math.min(samples.length - 1, index)); onCursor(next); setActive(true); if (keyboard && samples[next]) setAnnouncement(`${timeLabel(samples[next].time)} UTC. ${options.visible.map(key => `${priceSeries.find(series => series.key === key)!.label}: ${platinum(samples[next][key])}`).join('. ')}`); };
  const nearest = (clientX: number) => {
    const position = clientX - host.current!.getBoundingClientRect().left;
    return samples.reduce((best, _sample, index) => Math.abs(x(index) - position) < Math.abs(x(best) - position) ? index : best, 0);
  };
  const shown = (active || pinned) && Boolean(sample);
  useLayoutEffect(() => {
    if (!shown || !tip.current) return;
    const bounds = tip.current.getBoundingClientRect();
    tip.current.style.left = `${Math.max(8, Math.min(size.width - bounds.width - 8, x(current) + 14))}px`;
    tip.current.style.top = `${Math.max(8, Math.min(size.height - bounds.height - 8, 24))}px`;
  });
  const marker = (key: PriceSeries, index: number, radius = 3) => key === 'closed' ? <circle cx={x(index)} cy={y(samples[index][key]!)} r={radius} /> : key === 'sell' ? <rect x={x(index) - radius} y={y(samples[index][key]!) - radius} width={radius * 2} height={radius * 2} /> : <path d={`M${x(index)},${y(samples[index][key]!) - radius}l${radius},${radius}l-${radius},${radius}l-${radius},-${radius}Z`} />;
  return <div className="market-chart-frame" ref={host} data-swipe-ignore>
    <div className="market-chart-surface" role="group" tabIndex={0} aria-label="Explore WFM price history" aria-describedby={`${id}-help${shown ? ` ${id}-tip` : ''}`}
      onPointerMove={event => { if (event.pointerType !== 'touch' && !pinned && samples.length) inspect(nearest(event.clientX)); }} onPointerLeave={() => { if (!pinned) setActive(false); }}
      onFocus={() => inspect(current, true)} onBlur={() => { if (!pinned) setActive(false); }} onClick={event => { if (samples.length) onPin(event.detail ? nearest(event.clientX) : current); }}
      onKeyDown={event => {
        if (event.altKey || event.ctrlKey || event.metaKey) return;
        if (event.shiftKey && ['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); onPan(event.key === 'ArrowLeft' ? -1 : 1); return; }
        const index = event.key === 'ArrowLeft' ? current - 1 : event.key === 'ArrowRight' ? current + 1 : event.key === 'Home' ? 0 : event.key === 'End' ? samples.length - 1 : undefined;
        if (index !== undefined) { event.preventDefault(); inspect(index, true); }
        else if (['Enter', ' '].includes(event.key)) { event.preventDefault(); if (samples.length) onPin(current); }
        else if (['+', '=', '-'].includes(event.key)) { event.preventDefault(); onZoom(event.key === '-' ? 2 : 0.5); }
      }}>
      <svg viewBox={`0 0 ${size.width} ${size.height}`} width="100%" height="100%" role="img" aria-label={`Price history in platinum, ${samples.length} timestamps`}>
        <g aria-hidden="true"><image href={currencySymbols.platinum} x={left - 22} y={0} width={14} height={14} />
          {[0, 1, 2, 3, 4].map(tick => { const value = minimum + (maximum - minimum) * tick / 4; return <g key={tick}>{options.grid && <line className="market-grid" x1={left} x2={right} y1={y(value)} y2={y(value)} />}<text className="market-axis" x={left - 8} y={y(value) + 4} textAnchor="end">{value.toLocaleString(undefined, { maximumFractionDigits: 1 })}</text></g>; })}
          {[...new Set([0, ...(size.width > 500 ? [Math.floor((samples.length - 1) / 2)] : []), samples.length - 1])].filter(index => samples[index]).map((index, order, array) => <text key={index} className="market-axis" x={x(index)} y={size.height - 8} textAnchor={order === 0 ? 'start' : order === array.length - 1 ? 'end' : 'middle'}>{new Date(samples[index].time).toLocaleString(undefined, interval < 86_400_000 ? { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', hour12: false } : { timeZone: 'UTC', month: 'short', day: 'numeric' })}</text>)}
        </g>
        {priceSeries.filter(series => options.visible.includes(series.key)).map(series => {
          const segments: number[][] = []; let part: number[] = [];
          samples.forEach((sample, index) => {
            if (sample[series.key] === null || index && Date.parse(sample.time) - Date.parse(samples[index - 1].time) > interval * 1.5) { if (part.length) segments.push(part); part = []; }
            if (sample[series.key] !== null) part.push(index);
          });
          if (part.length) segments.push(part);
          return <g key={series.key} className={`market-series series-${series.key}`}>{segments.map((segment, index) => {
            const path = segment.map((sample, position) => `${position ? 'L' : 'M'}${x(sample)},${y(samples[sample][series.key]!)}`).join(' ');
            return <g key={index}>{options.area && <path className="market-area" d={`${path}L${x(segment.at(-1)!)},${bottom}L${x(segment[0])},${bottom}Z`} />}<path className="market-line" d={path} strokeDasharray={series.dash} />{(options.markers || segment.length === 1) && segment.map(index => <g className="market-marker" key={index}>{marker(series.key, index)}</g>)}</g>;
          })}</g>;
        })}
        {options.target !== null && <g aria-hidden="true"><line className="market-target" x1={left} x2={right} y1={y(options.target)} y2={y(options.target)} /><text className="market-axis" x={right - 18} y={Math.max(14, y(options.target) - 6)} textAnchor="end">Target {options.target.toLocaleString(undefined, { maximumFractionDigits: 2 })}</text><image href={currencySymbols.platinum} x={right - 14} y={Math.max(14, y(options.target) - 6) - 12} width={14} height={14} /></g>}
        {!samples.some(sample => options.visible.some(key => sample[key] !== null)) && <text className="market-axis" x={(left + right) / 2} y={(top + bottom) / 2} textAnchor="middle">No measurements in this range</text>}
        {shown && <g aria-hidden="true"><line className="market-crosshair" x1={x(current)} x2={x(current)} y1={top} y2={bottom} />{options.visible.filter(key => sample[key] !== null).map(key => <g key={key} className={`market-marker series-${key}`}>{marker(key, current, 4)}</g>)}</g>}
      </svg>
    </div>
    <div ref={tip} id={`${id}-tip`} className="market-chart-tip" role="tooltip" hidden={!shown}>{sample && <><time dateTime={sample.time}>{timeLabel(sample.time)} UTC</time><dl>{priceSeries.filter(series => options.visible.includes(series.key)).map(series => <div key={series.key}><dt>{series.label}</dt><dd><Currency value={sample[series.key]} currency="platinum" /></dd></div>)}<div><dt>Reported volume</dt><dd>{sample.volume ?? '—'}</dd></div></dl></>}</div>
    <span id={`${id}-help`} className="sr-only">Left and Right inspect samples. Home and End reach endpoints. Enter, Space or click pins a sample. Plus and Minus zoom. Shift with Left or Right pans.</span>
    <span className="sr-only" aria-live="polite">{announcement}</span>
  </div>;
}
