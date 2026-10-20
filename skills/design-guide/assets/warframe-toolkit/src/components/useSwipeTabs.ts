import { useEffect, useRef } from 'react';

/** Touch previews are inert until the same selection path used by clicks/keys commits. */
export function useSwipeTabs(active: number, onSelect: (index: number) => void) {
  const group = useRef<HTMLElement>(null), viewport = useRef<HTMLDivElement>(null);
  const latest = useRef({ active, onSelect }); latest.current = { active, onSelect };
  const reset = useRef<() => void>(() => {});
  useEffect(() => {
    const root = group.current!, frame = viewport.current!;
    const tabs = [...root.querySelectorAll<HTMLElement>('[role=tab]')];
    const panels = tabs.map(tab => document.getElementById(tab.getAttribute('aria-controls')!)!);
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let gesture: { id: number; x: number; y: number; travel: number; horizontal: boolean } | null = null;
    let preview = -1, offset = 0, suppressClick = false, timer = 0;
    let settlement: { index: number; focus: boolean } | undefined;
    const clean = (selected = latest.current.active) => {
      clearTimeout(timer); settlement = undefined; preview = -1; offset = 0;
      frame.classList.remove('is-sliding', 'is-settling'); frame.style.height = '';
      panels.forEach((panel, index) => {
        panel.style.transform = ''; panel.classList.remove('is-swipe-preview');
        panel.inert = false; panel.removeAttribute('aria-hidden'); panel.hidden = index !== selected;
      });
    };
    reset.current = () => { gesture = null; clean(); };
    const finish = () => {
      if (!settlement) return;
      const { index, focus } = settlement; clean(index); latest.current.onSelect(index);
      if (focus) tabs[index].focus({ preventScroll: true });
    };
    const translate = (index: number, distance: number) => { panels[index].style.transform = `translate3d(${distance}px,0,0)`; };
    const drag = (distance: number, travel: number) => {
      const selected = latest.current.active;
      if (!frame.classList.contains('is-sliding')) { frame.style.height = `${panels[selected].offsetHeight}px`; frame.classList.add('is-sliding'); }
      const adjacent = selected + (distance < 0 ? 1 : -1), next = adjacent < 0 || adjacent >= panels.length ? -1 : adjacent;
      if (preview !== next) {
        if (preview >= 0) { const panel = panels[preview]; panel.hidden = true; panel.inert = false; panel.removeAttribute('aria-hidden'); panel.classList.remove('is-swipe-preview'); panel.style.transform = ''; }
        preview = next;
        if (preview >= 0) { const panel = panels[preview]; panel.inert = true; panel.setAttribute('aria-hidden', 'true'); panel.hidden = false; panel.classList.add('is-swipe-preview'); }
      }
      offset = preview < 0 ? Math.sign(distance) * Math.min(64, Math.abs(distance) * 0.25) : Math.max(-travel, Math.min(travel, distance));
      translate(selected, offset);
      if (preview >= 0) translate(preview, offset + (preview - selected) * travel);
    };
    const settle = (index: number, focus: boolean, travel: number) => {
      settlement = { index, focus };
      const destination = (latest.current.active - index) * travel;
      if (reduced.matches || Math.abs(offset - destination) < 0.5) { finish(); return; }
      frame.getBoundingClientRect(); frame.classList.add('is-settling'); frame.style.height = `${panels[index].offsetHeight}px`;
      translate(latest.current.active, destination);
      if (preview >= 0) translate(preview, destination + (preview - latest.current.active) * travel);
      timer = window.setTimeout(finish, 260);
    };
    const cancel = () => { const previous = gesture; gesture = null; if (previous?.horizontal) settle(latest.current.active, false, previous.travel); };
    const ownsGesture = (target: Element) => {
      if (target.closest('a, input, textarea, select, label, [contenteditable]:not([contenteditable=false]), [data-swipe-ignore]')) return true;
      if (target.closest('button') && !target.closest('[role=tab]')) return true;
      for (let node: Element | null = target; node && node !== root; node = node.parentElement) {
        if (node.scrollWidth > node.clientWidth && /auto|scroll/.test(getComputedStyle(node).overflowX)) return true;
      }
      return false;
    };
    const start = (event: TouchEvent) => {
      finish(); cancel(); suppressClick = false;
      if (event.touches.length !== 1 || ownsGesture(event.target as Element) || !getSelection()?.isCollapsed) return;
      const touch = event.touches[0]; gesture = { id: touch.identifier, x: touch.clientX, y: touch.clientY, travel: frame.clientWidth + 16, horizontal: false };
    };
    const move = (event: TouchEvent) => {
      if (!gesture) return;
      if (event.touches.length !== 1 || !event.cancelable || !getSelection()?.isCollapsed) { cancel(); return; }
      const touch = event.touches[0], x = Math.abs(touch.clientX - gesture.x), y = Math.abs(touch.clientY - gesture.y);
      if (!gesture.horizontal) {
        if (Math.max(x, y) < 10) return;
        if (y >= x) { gesture = null; return; }
        if (x < y * 1.5) return;
        gesture.horizontal = true;
      }
      event.preventDefault(); suppressClick = true; drag(touch.clientX - gesture.x, gesture.travel);
    };
    const end = (event: TouchEvent) => {
      const completed = gesture; gesture = null;
      if (!completed?.horizontal) return;
      const touch = [...event.changedTouches].find(touch => touch.identifier === completed.id);
      let next = latest.current.active;
      if (touch && !event.touches.length && getSelection()?.isCollapsed) {
        const x = touch.clientX - completed.x, y = touch.clientY - completed.y; drag(x, completed.travel);
        if (Math.abs(x) >= 48 && Math.abs(x) >= Math.abs(y) * 1.5) next = Math.max(0, Math.min(panels.length - 1, next + (x < 0 ? 1 : -1)));
      }
      settle(next, next !== latest.current.active && root.contains(document.activeElement), completed.travel);
    };
    const multitouch = (event: TouchEvent) => { if (event.touches.length > 1) cancel(); };
    const click = (event: MouseEvent) => { if (suppressClick && event.detail) { event.preventDefault(); event.stopPropagation(); } else { gesture = null; clean(); } suppressClick = false; };
    const interrupt = () => { gesture = null; if (settlement) finish(); else clean(); };
    const pointer = (event: PointerEvent) => { if (event.pointerType !== 'touch') { interrupt(); suppressClick = false; } };
    const transition = (event: TransitionEvent) => { if (event.target === panels[latest.current.active] && event.propertyName === 'transform') finish(); };
    root.addEventListener('touchstart', start, { passive: true }); root.addEventListener('touchmove', move, { passive: false });
    root.addEventListener('touchend', end, { passive: true }); root.addEventListener('touchcancel', cancel, { passive: true });
    root.addEventListener('click', click, true); root.addEventListener('pointerdown', pointer); root.addEventListener('keydown', interrupt, true);
    window.addEventListener('touchstart', multitouch, { passive: true }); window.addEventListener('resize', interrupt);
    frame.addEventListener('transitionend', transition); reduced.addEventListener('change', finish);
    return () => {
      clean(); reset.current = () => {};
      root.removeEventListener('touchstart', start); root.removeEventListener('touchmove', move); root.removeEventListener('touchend', end); root.removeEventListener('touchcancel', cancel);
      root.removeEventListener('click', click, true); root.removeEventListener('pointerdown', pointer); root.removeEventListener('keydown', interrupt, true);
      window.removeEventListener('touchstart', multitouch); window.removeEventListener('resize', interrupt);
      frame.removeEventListener('transitionend', transition); reduced.removeEventListener('change', finish);
    };
  }, []);
  useEffect(() => reset.current(), [active]);
  return { group, viewport };
}
