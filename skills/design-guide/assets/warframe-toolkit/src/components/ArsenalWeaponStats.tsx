import { useId, useMemo, useState } from 'react';
import { arsenalStatText, arsenalWeaponStats, damageComponents } from '../lib/arsenalStats';
import type { ArsenalStat } from '../lib/arsenalStats';
import type { Calculation } from '../lib/modder';
import type { ArsenalBuild, ArsenalSlot } from '../lib/modderModel';
import type { CatalogItem } from '../lib/model';
import { DamageIcon } from './ModderVisuals';

function StatRow({ row, prefix }: { row: ArsenalStat; prefix: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const label = <>{row.damageType && <DamageIcon type={row.damageType} />}{row.label}{row.damageType && damageComponents[row.damageType] && <span className="modder-damage-components" aria-hidden="true">(<DamageIcon type={damageComponents[row.damageType][0]} />+<DamageIcon type={damageComponents[row.damageType][1]} />)</span>}</>;
  return <>
    <div className={`modder-stat-row${row.base ? ' is-base' : ''}`} title={row.base ? `Base reference value; modifiers to this attribute are not simulated.${row.title ? ` ${row.title}` : ''}` : row.title}>
      <dt>{row.details?.length ? <button type="button" aria-label={`${row.label} details`} aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>{label}<span aria-hidden="true">◇</span></button> : label}</dt>
      <dd data-arsenal-stat={`${prefix}${row.key}`}>{row.disposition ? <span className="modder-riven-disposition" role="img" aria-label={`${row.disposition} of 5`}>{Array.from({ length: 5 }, (_, index) => <i key={index} className={index < row.disposition! ? 'is-filled' : ''} />)}</span> : arsenalStatText(row)}</dd>
    </div>
    {!!row.details?.length && <div id={id} className="modder-stat-subrows" hidden={!open}><dt className="sr-only">{row.label} details</dt><dd><dl>{row.details.map(detail => <StatRow key={detail.key} row={detail} prefix={prefix} />)}</dl></dd></div>}
  </>;
}

export function ArsenalWeaponStats({ build, slot, items, result }: { build: ArsenalBuild; slot: ArsenalSlot; items: Map<string, CatalogItem>; result: Calculation }) {
  const groups = useMemo(() => arsenalWeaponStats(build, slot, items, result), [build, slot, items, result]);
  return <div className="modder-weapon-stats" role="region" aria-label="Weapon attributes" tabIndex={0}>
    {groups.map((group, index) => <div className="modder-stat-group" key={`${index}-${group.title}`}>
      {group.title && <h2>{group.title}{group.note && <small>{group.note}</small>}</h2>}
      <dl>{group.rows.map(row => <StatRow key={row.key} row={row} prefix={index < 2 ? '' : `${group.title}: `} />)}</dl>
    </div>)}
    <span className="modder-stats-note">Damage per projectile. Dotted values: base stats.</span>
  </div>;
}
