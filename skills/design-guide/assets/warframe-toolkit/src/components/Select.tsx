import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown } from 'lucide-react';

export interface SelectOption { value: string; label: string; content?: ReactNode; disabled?: boolean }

interface SelectProps {
  label: string;
  value: string;
  options: readonly SelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
}

/** App-rendered options, independent of the browser's native select picker. */
export function Select({ label, value, options, onChange, disabled = false, className = '' }: SelectProps) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const search = useRef({ text: '', time: 0 });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const selected = options.findIndex(option => option.value === value);
  const available = options.map((option, index) => option.disabled ? -1 : index).filter(index => index >= 0);
  const inactive = disabled || !available.length;
  const expanded = open && !inactive;
  const activeIndex = available.includes(active) ? active : available[0];
  const currentLabel = options[selected]?.label ?? value;
  const dismiss = () => { search.current = { text: '', time: 0 }; setOpen(false); };
  const show = (index = selected) => {
    search.current = { text: '', time: 0 };
    setActive(available.includes(index) ? index : available[0]);
    setOpen(true);
  };
  const choose = (index: number) => {
    const option = options[index];
    if (option && !option.disabled && option.value !== value) onChange(option.value);
    dismiss();
  };

  useEffect(() => { if (inactive) setOpen(false); }, [inactive]);

  useLayoutEffect(() => {
    const popup = menu.current;
    const button = trigger.current;
    if (!expanded || !popup || !button) return;
    const position = () => {
      const field = button.getBoundingClientRect();
      const viewport = window.visualViewport;
      const top = viewport?.offsetTop ?? 0;
      const left = viewport?.offsetLeft ?? 0;
      const height = viewport?.height ?? innerHeight;
      const width = viewport?.width ?? innerWidth;
      // Size to the content, then constrain the popup to the visible viewport.
      popup.style.minWidth = `${Math.min(field.width, width - 16)}px`;
      popup.style.maxWidth = `${Math.min(480, width - 16)}px`;
      const below = top + height - field.bottom - 8;
      const above = field.top - top - 8;
      const desired = Math.min(320, height / 2, popup.scrollHeight + 2);
      const opensAbove = below < desired && above > below;
      const popupHeight = Math.max(0, Math.min(desired, opensAbove ? above : below));
      popup.style.maxHeight = `${popupHeight}px`;
      popup.style.left = `${Math.max(left + 8, Math.min(field.left, left + width - popup.offsetWidth - 8))}px`;
      popup.style.top = `${opensAbove ? field.top - popupHeight - 4 : field.bottom + 4}px`;
    };
    position();
    const observer = new ResizeObserver(position);
    observer.observe(button);
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
  }, [expanded, options.length]);

  useLayoutEffect(() => {
    const popup = menu.current;
    const option = popup?.children[activeIndex] as HTMLElement | undefined;
    if (!expanded || !popup || !option) return;
    if (option.offsetTop < popup.scrollTop) popup.scrollTop = option.offsetTop - 4;
    else if (option.offsetTop + option.offsetHeight > popup.scrollTop + popup.clientHeight) popup.scrollTop = option.offsetTop + option.offsetHeight - popup.clientHeight + 4;
  }, [expanded, activeIndex]);

  useEffect(() => {
    if (!expanded) return;
    const outside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!trigger.current?.contains(target) && !menu.current?.contains(target)) dismiss();
    };
    document.addEventListener('pointerdown', outside, true);
    return () => document.removeEventListener('pointerdown', outside, true);
  }, [expanded]);

  return <>
    <button ref={trigger} type="button" role="combobox" className={`select-trigger ${className}`} disabled={inactive} aria-label={label} aria-haspopup="listbox" aria-expanded={expanded} aria-controls={expanded ? `${id}-list` : undefined} aria-activedescendant={expanded ? `${id}-option-${activeIndex}` : undefined} title={currentLabel}
      onClick={() => expanded ? dismiss() : show()} onBlur={dismiss}
      onKeyDown={event => {
        if (event.nativeEvent.isComposing || event.ctrlKey || event.metaKey) return;
        const { key } = event;
        const typingSpace = key === ' ' && search.current.text && Date.now() - search.current.time < 700;
        if (key === 'Escape' && expanded) {
          event.preventDefault(); event.stopPropagation(); dismiss();
        } else if (key === 'Tab' && expanded) {
          choose(activeIndex);
        } else if (key === 'Enter' || key === ' ' && !typingSpace) {
          event.preventDefault(); event.stopPropagation();
          if (expanded) choose(activeIndex); else show();
        } else if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageDown', 'PageUp'].includes(key)) {
          event.preventDefault(); event.stopPropagation(); search.current.text = '';
          if (event.altKey && key === 'ArrowUp' && expanded) { choose(activeIndex); return; }
          if (key === 'Home') show(available[0]);
          else if (key === 'End') show(available.at(-1));
          else if (!expanded) show();
          else {
            const step = key === 'PageDown' ? 10 : key === 'PageUp' ? -10 : key === 'ArrowDown' ? 1 : -1;
            setActive(available[Math.max(0, Math.min(available.length - 1, available.indexOf(activeIndex) + step))]);
          }
        } else if (key.length === 1 && !event.altKey) {
          event.preventDefault(); event.stopPropagation();
          const now = Date.now();
          const text = (now - search.current.time < 700 ? search.current.text : '') + key.toLowerCase();
          search.current = { text, time: now };
          const prefix = [...text].every(character => character === text[0]) ? text[0] : text;
          const start = available.indexOf(expanded ? activeIndex : selected);
          if (!expanded) setActive(available.includes(selected) ? selected : available[0]);
          for (let offset = prefix.length === 1 ? 1 : 0; offset < available.length + (prefix.length === 1 ? 1 : 0); offset++) {
            const index = available[(Math.max(0, start) + offset) % available.length];
            if (options[index].label.toLowerCase().startsWith(prefix)) { setActive(index); break; }
          }
          setOpen(true);
        }
      }}><span className="select-value">{options[selected]?.content ?? currentLabel}</span><ChevronDown size={14} aria-hidden="true" /></button>
    {expanded && createPortal(<div ref={menu} id={`${id}-list`} className="select-menu" role="listbox" aria-label={label} onMouseDown={event => event.preventDefault()}>
      {options.map((option, index) => <div key={option.value} id={`${id}-option-${index}`} role="option" aria-selected={index === selected} aria-disabled={option.disabled || undefined} className="select-option" data-active={index === activeIndex || undefined}
        onPointerMove={event => { if (event.pointerType === 'mouse' && !option.disabled && (event.movementX || event.movementY)) setActive(index); }}
        onClick={event => { event.stopPropagation(); if (!option.disabled) { choose(index); trigger.current?.focus({ preventScroll: true }); } }}>
        <span>{option.content ?? option.label}</span><Check size={14} aria-hidden="true" className={index === selected ? '' : 'select-check-hidden'} />
      </div>)}
    </div>, trigger.current?.closest('dialog') ?? document.body)}
  </>;
}
