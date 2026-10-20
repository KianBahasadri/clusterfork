import { useEffect, useRef } from 'react';
import type { DragEvent, MouseEvent, PointerEvent as ReactPointerEvent, RefObject } from 'react';
import { projectColumnOrder } from '../lib/columnDrag';
import type { ColumnDropTarget } from '../lib/tableView';
import { columnSlideDuration, createColumnDragPreview } from './columnDragPreview';

// The real table owns data, focus, and scrolling. Drag previews and target markers
// stay outside React so crossing a column never renders the entire collection.
export function useColumnDrag(container: RefObject<HTMLDivElement | null>, onDrop: (key: string, target: ColumnDropTarget) => void, onStart: () => void) {
  const drop = useRef(onDrop); drop.current = onDrop;
  const start = useRef(onStart); start.current = onStart;
  const cleanup = useRef<(() => void) | null>(null);
  const suppressClick = useRef(false);
  useEffect(() => () => cleanup.current?.(), []);

  const begin = (key: string, event: ReactPointerEvent<HTMLTableCellElement>) => {
    if (event.button !== 0 || !event.isPrimary || event.ctrlKey || event.metaKey) return;
    cleanup.current?.();
    suppressClick.current = false;
    const grid = container.current;
    if (!grid) return;
    const element = event.currentTarget, pointerId = event.pointerId;
    const startX = event.clientX, startY = event.clientY;
    const grabOffset = startX - element.getBoundingClientRect().left;
    const initialScrollTop = grid.scrollTop;
    const pinned = getComputedStyle(grid.querySelector('th')!).left === '0px';
    const headers = new Map([...grid.querySelectorAll<HTMLTableCellElement>('th[data-column-key]')].map(cell => [cell.dataset.columnKey!, cell]));
    const columns = [...headers].map(([key, cell]) => ({ key, width: cell.getBoundingClientRect().width }));
    const original = columns.map(column => column.key);
    const originalIndex = original.indexOf(key);
    let order = original;
    let x = startX, y = startY, active = false, frame = 0, lastFrame = 0, settling = 0;
    let destination: ColumnDropTarget | null = null;
    let overPinnedStart = false;
    let preview: ReturnType<typeof createColumnDragPreview> | null = null;

    const inBounds = () => {
      const bounds = grid.getBoundingClientRect(), header = element.getBoundingClientRect();
      return x >= Math.max(0, bounds.left) && x <= Math.min(innerWidth, bounds.left + grid.clientWidth) && y >= header.top - 32 && y <= header.bottom + 32;
    };
    const paint = () => {
      const left = x - grid.getBoundingClientRect().left - grid.clientLeft - grabOffset;
      if (inBounds()) {
        // The visible pinned column remains a reachable first-position target
        // even when the rest of the table has scrolled underneath it.
        const firstMiddle = columns[0].width / 2;
        const pointerLeft = x - grid.getBoundingClientRect().left;
        if (pointerLeft < firstMiddle - 6) overPinnedStart = true;
        else if (pointerLeft > firstMiddle + 6) overPinnedStart = false;
        order = pinned && grid.scrollLeft > 0 && overPinnedStart ? [key, ...order.filter(column => column !== key)] : projectColumnOrder(columns, order, key, left + grid.scrollLeft);
      } else order = original;
      const index = order.indexOf(key);
      const next: ColumnDropTarget | null = index === originalIndex ? null : { key: original[index], edge: index < originalIndex ? 'before' : 'after' };
      if (next?.key !== destination?.key || next?.edge !== destination?.edge) {
        if (destination) delete headers.get(destination.key)!.dataset.dropEdge;
        destination = next;
        if (next) headers.get(next.key)!.dataset.dropEdge = next.edge;
      }
      preview?.paint(order, grid.scrollLeft, pinned, left);
    };
    const tick = (time: number) => {
      const bounds = grid.getBoundingClientRect();
      const left = Math.max(0, bounds.left), right = Math.min(innerWidth, bounds.left + grid.clientWidth);
      const elapsed = Math.min(32, lastFrame ? time - lastFrame : 16);
      lastFrame = time;
      if (inBounds()) {
        // Only the actual viewport edges scroll; pinned columns are not edges.
        const velocity = x < left + 36 ? -Math.min(1, (left + 36 - x) / 36) : x > right - 36 ? Math.min(1, (x - right + 36) / 36) : 0;
        grid.scrollLeft += velocity * elapsed * .8;
      }
      paint();
      frame = requestAnimationFrame(tick);
    };
    const stopInput = () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', finish);
      document.removeEventListener('pointercancel', cancel);
      document.removeEventListener('keydown', keyDown, true);
      window.removeEventListener('blur', cancel);
      window.removeEventListener('resize', cancel);
      window.removeEventListener('scroll', scrolled, true);
      grid.removeEventListener('wheel', wheel);
      element.removeEventListener('lostpointercapture', lostCapture);
      if (element.hasPointerCapture(pointerId)) element.releasePointerCapture(pointerId);
    };
    const clearMarkers = () => {
      delete grid.dataset.dragging;
      delete element.dataset.dragging;
      if (destination) delete headers.get(destination.key)!.dataset.dropEdge;
    };
    const dispose = () => {
      stopInput(); clearMarkers(); clearTimeout(settling); preview?.remove(); cleanup.current = null;
    };
    const end = (commit: boolean, immediate = false) => {
      stopInput(); clearMarkers();
      if (active) {
        preview?.settle(commit ? order : original, grid.scrollLeft, pinned);
        if (commit && destination) drop.current(key, destination);
      }
      if (!active || immediate || matchMedia('(prefers-reduced-motion: reduce)').matches) dispose();
      else settling = window.setTimeout(dispose, columnSlideDuration + 34);
    };
    const move = (next: PointerEvent) => {
      if (next.pointerId !== pointerId) return;
      x = next.clientX; y = next.clientY;
      if (!active && Math.abs(x - startX) >= 6 && Math.abs(x - startX) > Math.abs(y - startY)) {
        active = true;
        suppressClick.current = true;
        element.setPointerCapture(pointerId);
        start.current();
        grid.dataset.dragging = key;
        element.dataset.dragging = 'true';
        preview = createColumnDragPreview(grid, columns, key);
        preview.paint(original, grid.scrollLeft, pinned, startX - grid.getBoundingClientRect().left - grabOffset);
        frame = requestAnimationFrame(tick);
      }
      if (active) { next.preventDefault(); paint(); }
    };
    const finish = (next: PointerEvent) => {
      if (next.pointerId !== pointerId) return;
      x = next.clientX; y = next.clientY;
      if (active) paint();
      end(active && inBounds());
    };
    const cancel = (event?: Event) => end(false, event?.type === 'resize' || event?.type === 'blur');
    const lostCapture = (next: PointerEvent) => {
      // Ignore the nested button's implicit touch capture transferring to its header.
      if (next.target === element && next.pointerId === pointerId) cancel();
    };
    const keyDown = (next: KeyboardEvent) => {
      if (next.key === 'Escape') { next.preventDefault(); next.stopPropagation(); cancel(); }
      else if (next.key === 'Tab') cancel();
    };
    const scrolled = (event: Event) => {
      if (event.target !== grid || grid.scrollTop !== initialScrollTop) end(false, true);
    };
    const wheel = (event: WheelEvent) => { if (active) event.preventDefault(); };
    cleanup.current = dispose;
    document.addEventListener('pointermove', move, { passive: false });
    document.addEventListener('pointerup', finish);
    document.addEventListener('pointercancel', cancel);
    document.addEventListener('keydown', keyDown, true);
    window.addEventListener('blur', cancel);
    window.addEventListener('resize', cancel);
    window.addEventListener('scroll', scrolled, true);
    grid.addEventListener('wheel', wheel, { passive: false });
    element.addEventListener('lostpointercapture', lostCapture);
  };

  return {
    headerProps: (key: string) => ({
      onPointerDown: (event: ReactPointerEvent<HTMLTableCellElement>) => begin(key, event),
      onDragStart: (event: DragEvent) => event.preventDefault(),
      onClickCapture: (event: MouseEvent) => {
        if (suppressClick.current && event.detail > 0) { event.preventDefault(); event.stopPropagation(); }
      },
    }),
  };
}
