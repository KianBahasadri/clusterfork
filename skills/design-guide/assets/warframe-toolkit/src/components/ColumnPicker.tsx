import { useRef, useState } from 'react';
import { Columns3 } from 'lucide-react';
import { TablePopover } from './TablePopover';

interface ColumnOptionsProps<Key extends string> {
  options: readonly { key: Key; title: string; required?: boolean }[];
  selected: readonly Key[];
  onChange: (key: Key, visible: boolean) => void;
  onReset: () => void;
}

export function ColumnOptions<Key extends string>({ options, selected, onChange, onReset }: ColumnOptionsProps<Key>) {
  return <>
    <div className="column-options">{options.map(option => <label key={option.key}>
      <input type="checkbox" checked={selected.includes(option.key)} disabled={option.required} onChange={event => onChange(option.key, event.target.checked)} />
      <span>{option.title}</span>
    </label>)}</div>
    <div className="column-menu-footer"><button type="button" className="button quiet" onClick={onReset}>Reset to default</button></div>
  </>;
}

export function ColumnPicker<Key extends string>(props: ColumnOptionsProps<Key>) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return <div className="column-picker">
    <button ref={trigger} type="button" className={`button secondary columns-button ${open ? 'selected' : ''}`} aria-expanded={open} onClick={() => setOpen(!open)}><Columns3 size={16} aria-hidden="true" />Columns</button>
    {open && <TablePopover anchor={trigger.current!} label="Table columns" className="column-menu" onClose={() => setOpen(false)}>
      <ColumnOptions {...props} />
    </TablePopover>}
  </div>;
}
