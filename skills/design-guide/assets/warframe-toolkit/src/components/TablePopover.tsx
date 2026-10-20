import { useEffect, useLayoutEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';

export function TablePopover({ anchor, point, label, role = 'group', className = '', onClose, children }: {
  anchor: HTMLElement;
  point?: { x: number; y: number };
  label: string;
  role?: 'group' | 'menu';
  className?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose); close.current = onClose;
  useLayoutEffect(() => {
    const popup = panel.current!;
    let anchorBounds = anchor.getBoundingClientRect();
    const position = () => {
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft ?? 0, top = viewport?.offsetTop ?? 0;
      const width = viewport?.width ?? innerWidth, height = viewport?.height ?? innerHeight;
      if (anchor.isConnected) anchorBounds = anchor.getBoundingClientRect();
      const bounds = anchorBounds;
      const bottom = point?.y ?? bounds.bottom;
      const above = (point?.y ?? bounds.top) - top - 12;
      const below = top + height - bottom - 12;
      const desired = Math.min(480, height * .6);
      const opensAbove = below < desired && above > below;
      popup.style.maxWidth = `${Math.max(0, width - 24)}px`;
      popup.style.maxHeight = `${Math.max(0, Math.min(desired, opensAbove ? above : below))}px`;
      popup.style.left = `${Math.max(left + 12, Math.min(point?.x ?? bounds.right - popup.offsetWidth, left + width - popup.offsetWidth - 12))}px`;
      popup.style.top = `${Math.max(top + 12, Math.min(opensAbove ? (point?.y ?? bounds.top) - popup.offsetHeight - 4 : bottom + 4, top + height - popup.offsetHeight - 12))}px`;
    };
    position();
    const observer = new ResizeObserver(position);
    observer.observe(anchor); observer.observe(popup);
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    window.visualViewport?.addEventListener('resize', position);
    window.visualViewport?.addEventListener('scroll', position);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', position);
      window.removeEventListener('scroll', position, true);
      window.visualViewport?.removeEventListener('resize', position);
      window.visualViewport?.removeEventListener('scroll', position);
    };
  }, [anchor, point]);

  useEffect(() => {
    panel.current?.querySelector<HTMLElement>('input:not(:disabled), button:not(:disabled)')?.focus({ preventScroll: true });
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      const nested = [...panel.current!.querySelectorAll('[aria-controls]')].some(control => document.getElementById(control.getAttribute('aria-controls')!)?.contains(target));
      if (!panel.current?.contains(target) && !anchor.contains(target) && !nested) close.current();
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [anchor]);

  // Label clicks need a focusable ancestor until the browser focuses the input.
  return createPortal(<div ref={panel} tabIndex={-1} className={`table-popover ${className}`} role={role} aria-label={label} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget) && event.relatedTarget !== anchor) close.current();
  }} onKeyDown={event => {
    const menu = (event.target as HTMLElement).closest<HTMLElement>('[role="menu"]');
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation(); close.current(); anchor.focus({ preventScroll: true });
    } else if (menu && event.currentTarget.contains(menu) && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      const buttons = [...menu.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next]?.focus();
    }
  }}>{children}</div>, document.body);
}
