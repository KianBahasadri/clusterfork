import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactElement } from 'react';

const fullListLimit = 500;
const batchSize = 192;

/** Small lists render in full. Large lists append near the bottom without fetching data. */
export function DropResults({ children, className }: { children: ReactElement[]; className: string }) {
  const [limit, setLimit] = useState(batchSize);
  const grid = useRef<HTMLDivElement>(null), bottom = useRef<HTMLDivElement>(null);
  const pendingFocus = useRef<number | null>(null);
  const count = children.length <= fullListLimit ? children.length : Math.min(limit, children.length);
  const hasMore = count < children.length;

  useEffect(() => {
    if (!hasMore || !bottom.current) return;
    if (typeof IntersectionObserver === 'undefined') { setLimit(children.length); return; }
    let active = true;
    const observer = new IntersectionObserver(entries => {
      if (active && entries.some(entry => entry.isIntersecting)) {
        observer.disconnect();
        setLimit(current => Math.min(children.length, current + batchSize));
      }
    }, { rootMargin: '1000px 0px' });
    observer.observe(bottom.current);
    return () => { active = false; observer.disconnect(); };
  }, [count, hasMore, children.length]);

  useLayoutEffect(() => {
    if (pendingFocus.current === null) return;
    (grid.current?.children[pendingFocus.current] as HTMLElement | undefined)?.focus();
    pendingFocus.current = null;
  }, [count]);

  return <div className={className} ref={grid} onKeyDown={event => {
    const target = event.target, element = event.currentTarget;
    if (!(target instanceof HTMLButtonElement) || target.parentElement !== element || event.altKey || event.ctrlKey || event.metaKey || !['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const index = Array.from(element.children).indexOf(target);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? children.length - 1 : Math.max(0, Math.min(children.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)));
    if (next < count) (element.children[next] as HTMLElement).focus();
    else { pendingFocus.current = next; setLimit(Math.max(count + batchSize, next + 1)); }
  }}>
    {children.slice(0, count)}
    {hasMore && <div ref={bottom} className="drop-results-bottom" aria-hidden="true" />}
  </div>;
}
