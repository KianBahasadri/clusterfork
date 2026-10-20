import type { DragColumn } from '../lib/columnDrag';

export const columnSlideDuration = 150;

// A fixed, inert preview keeps animation out of the real table's sticky layout
// and scroll measurements. Only visible rows are copied, once per gesture.
export function createColumnDragPreview(grid: HTMLDivElement, columns: DragColumn[], source: string) {
  const bounds = grid.getBoundingClientRect();
  const table = grid.querySelector<HTMLTableElement>('table')!;
  const layer = document.createElement('div');
  layer.className = 'inventory-grid column-drag-layer';
  layer.setAttribute('aria-hidden', 'true');
  layer.inert = true;
  const height = Math.min(grid.clientHeight, table.getBoundingClientRect().bottom - bounds.top - grid.clientTop);
  Object.assign(layer.style, { left: `${bounds.left + grid.clientLeft}px`, top: `${bounds.top + grid.clientTop}px`, width: `${grid.clientWidth}px`, height: `${height}px` });
  const rows = [...table.tBodies[0].rows].filter(row => { const rect = row.getBoundingClientRect(); return rect.bottom > bounds.top && rect.top < bounds.bottom; });
  const header = table.tHead!.rows[0];
  const groups = new Map<string, HTMLDivElement>();
  for (const [index, column] of columns.entries()) {
    const group = document.createElement('div');
    group.className = 'column-drag-preview';
    group.dataset.columnKey = column.key;
    group.style.width = `${column.width}px`;
    group.style.height = `${height}px`;
    if (column.key === source) group.dataset.lifted = 'true';
    const copyRows = (originals: HTMLTableRowElement[], heading: boolean) => {
      if (!originals.length) return;
      const copy = document.createElement('table');
      copy.className = `inventory-table column-drag-${heading ? 'heading' : 'body'}`;
      copy.style.top = `${originals[0].cells[index].getBoundingClientRect().top - bounds.top - grid.clientTop}px`;
      const section = heading ? copy.createTHead() : copy.createTBody();
      for (const original of originals) {
        const cell = original.cells[index].cloneNode(true) as HTMLTableCellElement;
        cell.style.height = `${original.cells[index].getBoundingClientRect().height}px`;
        cell.removeAttribute('data-dragging');
        cell.removeAttribute('data-drop-edge');
        cell.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
        section.insertRow().append(cell);
      }
      group.append(copy);
    };
    copyRows(rows, false);
    copyRows([header], true);
    groups.set(column.key, group);
    layer.append(group);
  }
  document.body.append(layer);
  let initialized = false;
  return {
    paint(order: string[], scrollLeft: number, pinned: boolean, floatingLeft?: number) {
      let left = 0;
      for (const [index, key] of order.entries()) {
        const column = columns.find(column => column.key === key)!;
        const group = groups.get(key)!;
        const position = key === source && floatingLeft !== undefined ? floatingLeft : pinned && index === 0 ? 0 : left - scrollLeft;
        group.dataset.pinned = pinned && index === 0 ? 'true' : 'false';
        group.style.transform = `translate3d(${position}px, 0, 0)`;
        left += column.width;
      }
      if (!initialized) {
        // Establish the starting positions before enabling transitions.
        layer.getBoundingClientRect();
        layer.dataset.ready = 'true';
        initialized = true;
      }
    },
    settle(order: string[], scrollLeft: number, pinned: boolean) {
      layer.dataset.settling = 'true';
      this.paint(order, scrollLeft, pinned);
    },
    remove() { layer.remove(); },
  };
}
