import { useEffect, useId, useRef } from 'react';
import type { KeyboardEventHandler, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';

let openDialogs = 0, previousOverflow = '';

export function Dialog({ title, onClose, children, className = '', onKeyDown, initialFocus }: { title: string; onClose: () => void; children: ReactNode; className?: string; onKeyDown?: KeyboardEventHandler<HTMLDialogElement>; initialFocus?: string }) {
  const titleId = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const element = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    element?.showModal();
    if (initialFocus) element?.querySelector<HTMLElement>(initialFocus)?.focus({ preventScroll: true });
    if (openDialogs === 0) previousOverflow = document.body.style.overflow;
    openDialogs++;
    document.body.style.overflow = 'hidden';
    return () => { element?.close(); if (--openDialogs === 0) document.body.style.overflow = previousOverflow; if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, []);
  return createPortal(<dialog ref={ref} className={className} aria-labelledby={titleId} onKeyDown={event => {
    onKeyDown?.(event);
    if (event.defaultPrevented || event.key !== 'Tab') return;
    const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('button, input, select, textarea, a[href], [tabindex]')].filter(node => node.tabIndex >= 0 && !node.matches(':disabled') && node.getClientRects().length > 0 && !node.closest('[hidden], [inert]'));
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }} onCancel={event => { event.preventDefault(); event.stopPropagation(); close.current(); }} onClick={event => { event.stopPropagation(); if (event.target === event.currentTarget) close.current(); }}>
    <div className="dialog-inner">
      <div className="dialog-header"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" onClick={onClose} aria-label="Close" title="Close"><X size={18} /></button></div>
      {children}
    </div>
  </dialog>, document.body);
}
