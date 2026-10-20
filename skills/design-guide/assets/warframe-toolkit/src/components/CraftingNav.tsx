import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { Hammer } from 'lucide-react';
import { craftingPlanLabel } from '../lib/crafting';
import type { CraftingItem, CraftingPlan } from '../lib/crafting';
import { CraftingArtwork } from './CraftingArtwork';
import './CraftingNav.css';

export function CraftingNav({ plans, active, selected, onHome, onPlan }: {
  plans: CraftingPlan[]; active: boolean; selected: string; onHome: () => void; onPlan: (id: string) => void;
}) {
  const id = useId(), root = useRef<HTMLLIElement>(null), trigger = useRef<HTMLAnchorElement>(null), menu = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false), [items, setItems] = useState<Map<string, CraftingItem>>(), [error, setError] = useState(false);
  const closing = useRef<number>(undefined);
  const pendingFocus = useRef<'first' | 'last' | null>(null);
  const clearCloseTimer = () => window.clearTimeout(closing.current);
  const dismiss = () => { clearCloseTimer(); pendingFocus.current = null; setOpen(false); };
  const focusItem = (end: 'first' | 'last') => {
    const buttons = menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]');
    const target = (end === 'first' ? buttons?.[0] : buttons?.[buttons.length - 1]) ?? menu.current;
    target?.focus({ preventScroll: true }); target?.scrollIntoView({ block: 'nearest' });
  };

  useEffect(() => () => clearCloseTimer(), []);
  useEffect(() => dismiss(), [active, selected]);
  useEffect(() => {
    if (!open || items || !plans.length) return;
    let current = true;
    setError(false);
    // Keep ingredient data out of the initial collection-page download.
    import('../data/crafting').then(data => { if (current) setItems(data.craftingItems); }, () => { if (current) setError(true); });
    return () => { current = false; };
  }, [open, items, plans.length]);

  useLayoutEffect(() => {
    if (!open) return;
    const panel = menu.current!, anchor = trigger.current!;
    const position = () => {
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft ?? 0, top = viewport?.offsetTop ?? 0;
      const width = viewport?.width ?? document.documentElement.clientWidth, height = viewport?.height ?? innerHeight;
      const bounds = anchor.getBoundingClientRect();
      if (bounds.bottom <= top || bounds.top >= top + height) { dismiss(); return; }
      panel.style.width = `${Math.min(300, width - 24)}px`;
      panel.style.maxHeight = `${Math.max(0, Math.min(360, top + height - bounds.bottom - 16))}px`;
      panel.style.left = `${Math.max(left + 12, Math.min(bounds.left, left + width - panel.offsetWidth - 12))}px`;
      panel.style.top = `${bounds.bottom + 4}px`;
    };
    position();
    const observer = new ResizeObserver(position);
    observer.observe(anchor); observer.observe(panel);
    window.addEventListener('resize', position); window.addEventListener('scroll', position, true);
    window.visualViewport?.addEventListener('resize', position); window.visualViewport?.addEventListener('scroll', position);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', position); window.removeEventListener('scroll', position, true);
      window.visualViewport?.removeEventListener('resize', position); window.visualViewport?.removeEventListener('scroll', position);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !pendingFocus.current || plans.length && !items && !error) return;
    focusItem(pendingFocus.current); pendingFocus.current = null;
  }, [open, items, error, plans.length]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => { if (!root.current?.contains(event.target as Node)) dismiss(); };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault(); event.stopPropagation(); dismiss();
      if (root.current?.contains(document.activeElement)) trigger.current?.focus({ preventScroll: true });
    };
    document.addEventListener('pointerdown', outside); document.addEventListener('focusin', outside);
    document.addEventListener('keydown', escape, true); window.addEventListener('blur', dismiss);
    return () => {
      document.removeEventListener('pointerdown', outside); document.removeEventListener('focusin', outside);
      document.removeEventListener('keydown', escape, true); window.removeEventListener('blur', dismiss);
    };
  }, [open]);

  return <li ref={root} onPointerEnter={event => {
    if (event.pointerType === 'touch') return;
    clearCloseTimer(); setOpen(true);
  }} onPointerLeave={() => {
    clearCloseTimer();
    if (!menu.current?.contains(document.activeElement)) closing.current = window.setTimeout(dismiss, 200);
  }}>
    <a ref={trigger} href="#crafting" aria-current={active ? 'page' : undefined} aria-keyshortcuts="Alt+5" aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={event => { event.preventDefault(); dismiss(); onHome(); }} onKeyDown={event => {
        if (event.altKey || event.ctrlKey || event.metaKey || !['ArrowDown', 'ArrowUp', ' '].includes(event.key)) return;
        event.preventDefault(); clearCloseTimer();
        const end = event.key === 'ArrowUp' ? 'last' : 'first';
        if (open && (items || !plans.length || error)) focusItem(end);
        else { pendingFocus.current = end; setOpen(true); }
      }}><span className="nav-icon" aria-hidden="true" style={{ background: 'none' }}><Hammer size={20} /></span><span>Crafting</span></a>
    {open && <div ref={menu} id={id} role="menu" aria-label="Open crafting plans" className="craft-plan-menu" tabIndex={-1} onKeyDown={event => {
      if (event.key === 'Tab') { closing.current = window.setTimeout(dismiss, 0); return; }
      if (event.altKey || event.ctrlKey || event.metaKey || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')];
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next]?.focus();
    }}>
      {!plans.length ? <p className="craft-plan-menu-message">No open plans.</p> : error ? <p className="craft-plan-menu-message" role="status">Couldn’t load plan names. Open Crafting to view plans.</p> : !items ? <p className="craft-plan-menu-message" role="status">Loading plans…</p> : plans.map((plan, index) => {
        const item = items.get(plan.itemId);
        return <button type="button" role="menuitem" key={plan.id} tabIndex={-1} aria-current={active && selected === plan.id ? 'page' : undefined} onClick={() => {
          dismiss(); trigger.current?.focus({ preventScroll: true }); onPlan(plan.id);
        }}>{item && <CraftingArtwork item={item} />}<span>{craftingPlanLabel(plans, index, item?.name ?? 'Unavailable item')}</span></button>;
      })}
    </div>}
  </li>;
}
