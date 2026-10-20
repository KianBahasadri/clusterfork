import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent } from 'react';
import { createPortal } from 'react-dom';
import { ArrowRight } from 'lucide-react';
import { Tooltip } from './Tooltip';
import './QuantitySlider.css';

const format = (value: number) => value.toLocaleString();

export function QuantitySlider({ name, value, max, batchSize, disabled, onChange }: { name: string; value: number; max: number; batchSize?: number; disabled: boolean; onChange: (value: number) => void }) {
  const id = useId(), slider = useRef<HTMLInputElement>(null), popup = useRef<HTMLDivElement>(null), editor = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(value), [dragging, setDragging] = useState(false), [editing, setEditing] = useState(false), [text, setText] = useState('');
  const live = useRef(value), editingRef = useRef(false), latest = useRef({ value, max, disabled, onChange });
  latest.current = { value, max, disabled, onChange };
  const drag = useRef<{ id: number; initial: number; value: number; x: number; y: number; units: number; moved: boolean; thumb: boolean } | null>(null);
  const click = useRef<{ time: number; x: number } | null>(null);
  const label = `Completed quantity for ${name}`;
  const shown = editing || dragging;
  // Gathered materials use a nearby 1/2/5 step around one tenth of the total.
  const target = max / 10, magnitude = 10 ** Math.floor(Math.log10(target));
  const increment = batchSize ?? Math.max(1, [1, 2, 5, 10].map(scale => scale * magnitude).reduce((best, step) => Math.abs(step - target) <= Math.abs(best - target) ? step : best));
  const remainingIncrement = Math.min(increment, max - draft);
  useEffect(() => { if (!drag.current && !editingRef.current) { live.current = value; setDraft(value); } }, [value, max]);

  const geometry = () => {
    const bounds = slider.current!.getBoundingClientRect(), width = Math.max(1, bounds.width - 18);
    return { bounds, width, start: bounds.left + 9, thumb: bounds.left + 9 + live.current / max * width };
  };
  const write = (amount: number) => {
    const next = Math.min(latest.current.max, Math.max(0, Math.round(amount)));
    if (next === live.current) return;
    live.current = next; setDraft(next);
    slider.current!.value = String(next);
    slider.current!.dispatchEvent(new Event('input', { bubbles: true }));
  };
  const commit = () => {
    if (latest.current.disabled || live.current === latest.current.value) return;
    slider.current?.dispatchEvent(new Event('change', { bubbles: true }));
    latest.current.onChange(live.current);
  };
  const closeEditor = (save: boolean) => {
    if (!editingRef.current) return;
    if (save && !latest.current.disabled) {
      const entered = editor.current!.value.trim().replaceAll(',', '');
      if (/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(entered) && Number.isFinite(Number(entered))) write(Number(entered));
    }
    editingRef.current = false; setEditing(false);
    slider.current?.focus({ preventScroll: true });
    if (save) commit();
  };
  const openEditor = () => {
    if (disabled || drag.current || editingRef.current) return;
    click.current = null; editingRef.current = true;
    setText(String(live.current)); setEditing(true);
  };
  useLayoutEffect(() => { if (editing) { editor.current?.focus({ preventScroll: true }); editor.current?.select(); } }, [editing]);
  useEffect(() => {
    if (!editing) return;
    const outside = (event: globalThis.PointerEvent) => { if (!popup.current?.contains(event.target as Node)) closeEditor(true); };
    document.addEventListener('pointerdown', outside, true);
    return () => document.removeEventListener('pointerdown', outside, true);
  }, [editing]);
  useEffect(() => { if (disabled) closeEditor(false); }, [disabled]);
  useLayoutEffect(() => {
    if (!shown) return;
    const position = () => {
      if (!popup.current || !slider.current) return;
      const { bounds, thumb } = geometry(), tip = popup.current, viewport = window.visualViewport;
      const left = viewport?.offsetLeft ?? 0, top = viewport?.offsetTop ?? 0, width = viewport?.width ?? innerWidth, height = viewport?.height ?? innerHeight;
      const thumbY = bounds.top + bounds.height / 2, above = thumbY - 17 - tip.offsetHeight;
      tip.style.left = `${Math.max(left + 12, Math.min(left + width - tip.offsetWidth - 12, thumb - tip.offsetWidth / 2))}px`;
      tip.style.top = `${Math.max(top + 12, Math.min(top + height - tip.offsetHeight - 12, above < top + 12 ? thumbY + 17 : above))}px`;
    };
    position();
    const observer = new ResizeObserver(position); observer.observe(slider.current!); observer.observe(popup.current!);
    window.addEventListener('resize', position); window.addEventListener('scroll', position, true);
    window.visualViewport?.addEventListener('resize', position); window.visualViewport?.addEventListener('scroll', position);
    return () => { observer.disconnect(); window.removeEventListener('resize', position); window.removeEventListener('scroll', position, true); window.visualViewport?.removeEventListener('resize', position); window.visualViewport?.removeEventListener('scroll', position); };
  }, [shown, draft, editing, max]);

  const start = (event: PointerEvent<HTMLInputElement>) => {
    if (disabled || event.button !== 0 || drag.current) return;
    event.preventDefault();
    if (editingRef.current) return;
    const track = geometry(), onThumb = Math.abs(event.clientX - track.thumb) <= 12;
    if (onThumb && click.current && event.timeStamp - click.current.time < 500 && Math.abs(event.clientX - click.current.x) < 8) { openEditor(); return; }
    click.current = null;
    const initial = live.current, amount = onThumb ? initial : Math.max(0, Math.min(max, (event.clientX - track.start) / track.width * max));
    drag.current = { id: event.pointerId, initial, value: amount, x: event.clientX, y: event.clientY, units: max / track.width, moved: false, thumb: onThumb };
    event.currentTarget.focus({ preventScroll: true }); event.currentTarget.setPointerCapture(event.pointerId);
    write(amount); setDragging(true);
  };
  const move = (event: PointerEvent<HTMLInputElement>) => {
    const active = drag.current;
    if (!active || active.id !== event.pointerId) return;
    event.preventDefault();
    if (Math.abs(event.clientX - active.x) > 3 || Math.abs(event.clientY - active.y) > 3) active.moved = true;
    active.value = Math.max(0, Math.min(max, active.value + (event.clientX - active.x) * active.units));
    active.x = event.clientX; write(active.value);
  };
  const finish = (event: PointerEvent<HTMLInputElement>) => {
    const active = drag.current;
    if (!active || active.id !== event.pointerId) return;
    if (event.type === 'pointercancel') write(active.initial);
    drag.current = null; setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    click.current = active.thumb && !active.moved && event.type === 'pointerup' ? { time: event.timeStamp, x: event.clientX } : null;
    commit();
  };

  return <div className={`quantity-slider${disabled ? ' is-disabled' : ''}`}>
    <div className="quantity-slider-heading"><label htmlFor={id}>Completed</label><button type="button" className="quantity-slider-value" aria-label={`Set completed quantity for ${name}`} aria-haspopup="dialog" disabled={disabled} onClick={openEditor}><output htmlFor={id}>{format(draft)} / {format(max)}</output></button></div>
    <div className="quantity-slider-row">
      <input ref={slider} id={id} type="range" className="quantity-range" min={0} max={max} step={1} value={draft} disabled={disabled} aria-label={label} aria-valuetext={`${format(draft)} of ${format(max)} completed`} aria-describedby={`${id}-help`} style={{ '--range-progress': `${draft / max * 100}%` } as CSSProperties}
        onChange={() => {}} onInput={event => { live.current = Number(event.currentTarget.value); setDraft(live.current); if (!drag.current && !editingRef.current) commit(); }}
        onPointerDown={start} onPointerMove={move} onPointerUp={finish} onPointerCancel={finish} onLostPointerCapture={finish}
        onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); openEditor(); } }} />
      <Tooltip className="icon-button quantity-increment" label={`Add ${format(remainingIncrement)} completed ${name}`} content={`+${format(remainingIncrement)}`} disabled={disabled || dragging || draft >= max} onClick={() => { if (!disabled && !drag.current && !editingRef.current) write(live.current + increment); }}><ArrowRight size={14} aria-hidden="true" /></Tooltip>
    </div>
    <span id={`${id}-help`} className="sr-only">Double-click the thumb, press Enter, or activate the quantity to type a value.</span>
    {shown && createPortal(<div ref={popup} className={`quantity-popup${editing ? ' is-editing' : ''}`} role={editing ? 'dialog' : undefined} aria-label={editing ? label : undefined} aria-hidden={editing ? undefined : true}>
      <input ref={editor} type="text" inputMode="decimal" autoComplete="off" spellCheck={false} aria-label={`${label} value`} value={editing ? text : String(draft)} readOnly={!editing} tabIndex={editing ? 0 : -1} style={{ width: `${Math.max(4, String(max).length + 1)}ch` }} onChange={event => setText(event.target.value)} onBlur={() => closeEditor(true)} onKeyDown={event => { if (event.key === 'Enter' || event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeEditor(event.key === 'Enter'); } }} />
    </div>, slider.current?.closest('dialog') ?? document.body)}
  </div>;
}
