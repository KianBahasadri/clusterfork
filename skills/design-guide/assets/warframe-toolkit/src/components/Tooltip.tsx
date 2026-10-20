import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import './Tooltip.css';

export function Tooltip({ children, content, className = '', label, onClick, hasPopup, disabled = false }: { children: ReactNode; content: ReactNode; className?: string; label?: string; onClick?: () => void; hasPopup?: boolean; disabled?: boolean }) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null), bubble = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false), [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false), [open, setOpen] = useState(false);
  const active = !disabled && (hovered || focused);
  useEffect(() => {
    if (!active || dismissed) { setOpen(false); return; }
    const timer = window.setTimeout(() => setOpen(true), 150);
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setDismissed(true); setOpen(false); } };
    window.addEventListener('keydown', escape);
    return () => { clearTimeout(timer); window.removeEventListener('keydown', escape); };
  }, [active, dismissed]);
  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      const anchor = trigger.current!, tip = bubble.current!;
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft ?? 0, top = viewport?.offsetTop ?? 0;
      const width = viewport?.width ?? document.documentElement.clientWidth, height = viewport?.height ?? innerHeight;
      tip.style.maxWidth = `${Math.min(360, width - 24)}px`;
      const bounds = anchor.getBoundingClientRect(), size = tip.getBoundingClientRect();
      const region = anchor.closest('.table-scroll')?.getBoundingClientRect();
      if (region && (bounds.right <= region.left || bounds.left >= region.right || bounds.bottom <= region.top || bounds.top >= region.bottom)) { setDismissed(true); setOpen(false); return; }
      const above = bounds.top - size.height - 8;
      tip.style.left = `${Math.max(left + 12, Math.min(left + width - size.width - 12, bounds.left + (bounds.width - size.width) / 2))}px`;
      tip.style.top = `${Math.max(top + 12, Math.min(top + height - size.height - 12, above < top + 12 ? bounds.bottom + 8 : above))}px`;
    };
    position();
    const observer = new ResizeObserver(position);
    observer.observe(trigger.current!); observer.observe(bubble.current!);
    window.addEventListener('resize', position); window.addEventListener('scroll', position, true);
    window.visualViewport?.addEventListener('resize', position); window.visualViewport?.addEventListener('scroll', position);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', position); window.removeEventListener('scroll', position, true);
      window.visualViewport?.removeEventListener('resize', position); window.visualViewport?.removeEventListener('scroll', position);
    };
  }, [open, content]);
  return <>
    <button ref={trigger} type="button" className={`tooltip-trigger ${className}`} disabled={disabled} aria-label={label} aria-haspopup={hasPopup ? 'dialog' : undefined} aria-describedby={open ? id : undefined} onClick={onClick ? () => { setDismissed(true); setOpen(false); onClick(); } : undefined}
      onPointerEnter={event => { if (event.pointerType !== 'touch') { setHovered(true); setDismissed(false); } }} onPointerLeave={() => setHovered(false)}
      onFocus={() => { setFocused(true); setDismissed(false); }} onBlur={() => setFocused(false)}>
      {children}
    </button>
    {open && createPortal(<div ref={bubble} id={id} role="tooltip" className="tooltip-bubble">{content}</div>, trigger.current?.closest('dialog') ?? document.body)}
  </>;
}
