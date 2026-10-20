export type DragColumn = { key: string; width: number };

// Swap with a neighbor only after crossing its midpoint. A small dead band keeps
// a steady hand near a boundary from repeatedly swapping unequal-width columns.
export function projectColumnOrder(columns: DragColumn[], order: string[], key: string, left: number): string[] {
  const next = [...order];
  const width = (key: string) => columns.find(column => column.key === key)!.width;
  let index = next.indexOf(key);
  if (index < 0) return order;
  let position = next.slice(0, index).reduce((sum, key) => sum + width(key), 0);
  while (index < next.length - 1 && left > position + width(next[index + 1]) / 2 + 6) {
    position += width(next[index + 1]);
    [next[index], next[index + 1]] = [next[index + 1], next[index]];
    index++;
  }
  while (index > 0 && left < position - width(next[index - 1]) / 2 - 6) {
    position -= width(next[index - 1]);
    [next[index], next[index - 1]] = [next[index - 1], next[index]];
    index--;
  }
  return next;
}
