import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, DragEvent } from 'react';
import { catalog } from '../data/catalog';
import { arcanes, calculate, capacity, compatible, equipItem, fmt, frames, modderData, mods, slotDrain, upgradeEffects, upgrades, validateEquipment, weaponSourceStats } from '../lib/modder';
import type { Calculation, CalculationStep, UpgradeKind } from '../lib/modder';
import { arsenalSlots, emptyUpgrade, newArsenal, polarities } from '../lib/modderModel';
import { weaponReferenceSources } from '../lib/arsenalStats';
import type { ArsenalBuild, ArsenalSlot, EquipmentBuild, ModderConditions, Polarity, UpgradeSlot } from '../lib/modderModel';
import { placeUpgrade } from '../lib/modderUpgrades';
import type { UpgradeSource, UpgradeTarget } from '../lib/modderUpgrades';
import { ModderLibrary, targetLabel } from './ModderLibrary';
import type { PickerTarget } from './ModderLibrary';
import { GameIcon, PolarityIcon, rankText, UpgradeFace } from './ModderVisuals';
import { AbilitiesDialog, AbilityIcons, ArsenalOverview } from './ArsenalOverview';
import { ArsenalWeaponStats } from './ArsenalWeaponStats';
import { Select } from './Select';
import './QuantitySlider.css';
import './Modder.css';
import './Arsenal.css';

const items = new Map(catalog.map(item => [item.id, item]));
const upgradeDragType = 'application/x-warframe-upgrade';
const valueText = (step: CalculationStep) => step.value === null ? 'Unavailable' : `${fmt(step.value)}${step.unit ? ` ${step.unit}` : ''}`;
const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const polarityOptions = polarities.map(value => ({ value, label: value ? titleCase(value) : 'No polarity', content: <span className="modder-polarity-option"><PolarityIcon polarity={value} />{value ? titleCase(value) : 'No polarity'}</span> }));
function starter(): ArsenalBuild {
  const build = newArsenal();
  build.name = 'Config A';
  for (const [index, name] of ['Excalibur', 'Braton', 'Lato', 'Skana'].entries()) {
    const item = catalog.find(item => item.name === name);
    if (item) build.equipment[arsenalSlots[index]] = equipItem(item, arsenalSlots[index]);
  }
  return build;
}
function Numeric({ label, value, max, min = 0, step = 1, onChange, disabled = false }: { label: string; value: number; min?: number; max: number; step?: number; onChange: (value: number) => void; disabled?: boolean }) {
  return <label className="modder-number"><span>{label}</span><input type="number" aria-label={label} value={value} min={min} max={max} step={step} disabled={disabled} onChange={event => { const next = event.currentTarget.valueAsNumber; if (Number.isFinite(next) && next >= min && next <= max && (step !== 1 || Number.isInteger(next))) onChange(next); }} /></label>;
}

function ArsenalName({ name, onChange, disabled }: { name: string; onChange: (name: string) => void; disabled: boolean }) {
  const [draft, setDraft] = useState(name);
  useEffect(() => setDraft(name), [name]);
  return <label className="modder-name"><span>Arsenal name</span><input aria-label="Arsenal name" maxLength={100} value={draft} disabled={disabled} onChange={event => { setDraft(event.target.value); if (event.target.value.trim()) onChange(event.target.value); }} onBlur={() => { if (!draft.trim()) setDraft(name); }} /></label>;
}

function UpgradeControls({ label, value, kind, conditions, onChange, onPick, disabled, onMove, index }: { label: string; value: UpgradeSlot; kind: UpgradeKind; conditions: ModderConditions; onChange: (value: UpgradeSlot) => void; onPick: () => void; disabled: boolean; onMove?: (direction: number) => void; index?: number }) {
  const mod = upgrades.get(value.id), effects = upgradeEffects(value, conditions);
  const arcane = kind === 'arcane';
  return <div className={`modder-upgrade-controls${value.id ? ' is-filled' : ''}${!value.enabled ? ' is-muted' : ''}`} role="region" aria-label={`${label} controls`}>
    <div className="modder-upgrade-top"><span>{label}</span>{value.id && <><span className="modder-drain">{arcane ? `R${value.rank}` : `${kind === 'special' ? '+' : ''}${slotDrain(value, kind === 'special')} capacity`}</span><button type="button" className="icon-button" aria-label={`Remove ${label.toLowerCase()}`} title={`Remove ${label.toLowerCase()}`} disabled={disabled} onClick={() => onChange(emptyUpgrade(value.polarity))}><GameIcon name="close" size={14} /></button></>}</div>
    <button type="button" className="modder-upgrade-pick" aria-label={`Replace ${label.toLowerCase()}`} disabled={disabled} onClick={onPick}>{mod ? mod.name : value.id ? 'Unavailable upgrade' : <><GameIcon name="add" size={16} />{arcane ? 'Add arcane' : kind === 'special' ? `Add ${label.toLowerCase()}` : 'Add mod'}</>}<GameIcon name="down" size={14} /></button>
    {mod && <>
      <div className="modder-effect-text">{rankText(mod, value.rank)}</div>
      <div className="modder-rank"><span>Rank</span><input className="quantity-range" type="range" aria-label={`${label} rank slider`} min={0} max={mod.maxRank} step={1} value={Math.min(value.rank, mod.maxRank)} disabled={disabled} style={{ '--range-progress': `${value.rank / (mod.maxRank || 1) * 100}%` } as CSSProperties} onChange={event => onChange({ ...value, rank: Number(event.target.value) })} /><input type="number" aria-label={`${label} rank`} min={0} max={mod.maxRank} value={value.rank} disabled={disabled} onChange={event => { const rank = event.currentTarget.valueAsNumber; if (Number.isInteger(rank) && rank >= 0 && rank <= mod.maxRank) onChange({ ...value, rank }); }} /><span>/ {mod.maxRank}</span></div>
      {effects.maxStacks > 0 && <Numeric label={`${label} active stacks`} value={value.stacks} max={effects.maxStacks} onChange={stacks => onChange({ ...value, stacks })} disabled={disabled} />}
      <div className="modder-upgrade-bottom"><label><input type="checkbox" checked={value.enabled} aria-label={`Include ${label.toLowerCase()}`} disabled={disabled} onChange={event => onChange({ ...value, enabled: event.target.checked })} />Include</label>{effects.omitted.length > 0 && <span className="modder-coverage">{effects.effects.length ? 'Partial' : 'Reference only'}</span>}{onMove && <span className="modder-move"><button type="button" className="icon-button" aria-label={`Move ${label.toLowerCase()} earlier`} title={`Move ${label.toLowerCase()} earlier`} disabled={disabled || index === 0} onClick={() => onMove(-1)}><GameIcon name="back" size={14} /></button><button type="button" className="icon-button" aria-label={`Move ${label.toLowerCase()} later`} title={`Move ${label.toLowerCase()} later`} disabled={disabled || index === 7} onClick={() => onMove(1)}><GameIcon name="forward" size={14} /></button></span>}</div>
    </>}
    {!arcane && <Select label={`${label} polarity`} value={value.polarity} options={polarityOptions.filter(option => option.value !== 'universal' || kind === 'special')} onChange={polarity => onChange({ ...value, polarity: polarity as Polarity })} disabled={disabled} />}
  </div>;
}

function Results({ result, compared, compareName }: { result: Calculation; compared?: Calculation; compareName?: string }) {
  const body = useRef<HTMLDivElement>(null);
  const [allOpen, setAllOpen] = useState(false);
  const delta = (step: CalculationStep) => {
    const old = compared?.steps.find(previous => previous.label === step.label);
    return old?.value !== null && old?.value !== undefined && step.value !== null ? step.value - old.value : null;
  };
  return <aside id="modder-results" className="modder-results" aria-label="Calculated results" tabIndex={-1}>
    <div className="modder-section-title"><h2>Calculations</h2><span className="badge modder-modeled">Modeled</span></div>
    {compareName && <p className="small muted">Changes from {compareName}</p>}
    <div className="modder-metrics" role="status" aria-label="Build results">{result.headline.map(step => <div key={step.label}><span>{step.label}</span><strong>{valueText(step)}</strong></div>)}</div>
    {!!result.damage.length && <div className="modder-damage" aria-label="Damage distribution"><h3>Damage / projectile</h3>{result.damage.map(part => <div key={part.type}><span>{titleCase(part.type)}</span><meter min={0} max={result.damage.reduce((sum, value) => sum + value.value, 0)} value={part.value} aria-label={`${titleCase(part.type)} damage`} /><strong>{fmt(part.value)}</strong></div>)}</div>}
    <div className="modder-breakdown-heading"><h3 className="modder-breakdown-title">How it’s calculated</h3><button type="button" className="button quiet" onClick={() => { body.current?.querySelectorAll('details').forEach(details => { details.open = !allOpen; }); setAllOpen(!allOpen); }}>{allOpen ? 'Collapse all' : 'Expand all'}</button></div>
    <div ref={body} className="modder-calculations">{result.steps.map((step, index) => <details key={step.label} open={index === 0 ? true : undefined}>
      <summary><span>{step.label}</span><strong data-result={step.label}>{valueText(step)}</strong>{delta(step) !== null && <small className="modder-delta">{delta(step)! >= 0 ? '+' : ''}{fmt(delta(step)!)}{step.unit === '%' ? ' pp' : step.unit ? ` ${step.unit}` : ''}</small>}<GameIcon name="down" size={14} /></summary>
      <div className="modder-equation"><code>{step.formula}</code><p>{step.explanation}</p>{!!step.sources.length && <ul>{step.sources.map((source, i) => <li key={i}>{source}</li>)}</ul>}</div>
    </details>)}</div>
    {!!result.warnings.length && <details className="modder-limits"><summary>Calculation limits <span className="count">{result.warnings.length}</span></summary><ul>{result.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul></details>}
    <p className="modder-results-note">Direct-hit model. Status damage, enemy special mechanics, and unmodeled effects are excluded.</p>
  </aside>;
}

function Conditions({ value, onChange, disabled, slot }: { value: ModderConditions; onChange: (next: ModderConditions) => void; disabled: boolean; slot: ArsenalSlot }) {
  const set = <K extends keyof ModderConditions>(key: K, next: ModderConditions[K]) => onChange({ ...value, [key]: next });
  return <details className="modder-settings"><summary><GameIcon name="stats" size={16} />Conditions <span>Target, buffs, combo</span><GameIcon name="down" size={16} /></summary><div className="modder-setting-body">
    <div className="modder-fields"><Numeric label="Target armor" value={value.armor} max={100000} onChange={next => set('armor', next)} disabled={disabled} /><Numeric label="Total armor strip %" value={value.strip} max={100} step={.1} onChange={next => set('strip', next)} disabled={disabled} /><div className="modder-field"><span>Target faction</span><Select label="Target faction" value={value.faction} options={['None', 'Grineer', 'Corpus', 'Infested', 'Corrupted', 'Sentient', 'Murmur'].map(value => ({ value, label: value === 'Corrupted' ? 'Orokin (Corrupted)' : value }))} onChange={next => set('faction', next)} disabled={disabled} /></div><Numeric label="Melee combo multiplier" value={value.combo} min={1} max={12} onChange={next => set('combo', next)} disabled={disabled} /></div>
    <div className="modder-buff"><label><input type="checkbox" checked={value.roar} disabled={disabled} onChange={event => set('roar', event.target.checked)} />Roar active</label><Numeric label="Roar base bonus %" value={value.roarBase} max={500} step={.1} onChange={next => set('roarBase', next)} disabled={disabled} /><span className="small muted">Scaled by this Warframe’s strength.</span></div>
    {slot === 'Warframe' && <div className="modder-fields"><Numeric label="Example base cast cost" value={value.abilityCost} max={10000} onChange={next => set('abilityCost', next)} disabled={disabled} /><Numeric label="Example base drain / s" value={value.abilityDrain} max={10000} step={.1} onChange={next => set('abilityDrain', next)} disabled={disabled} /></div>}
  </div></details>;
}

export function Modder({ builds, onSave, canSave, disabled, onSignIn }: { builds: ArsenalBuild[]; onSave: (builds: ArsenalBuild[]) => boolean; canSave: boolean; disabled: boolean; onSignIn: () => void }) {
  const [view, setView] = useState<'arsenal' | 'upgrades'>('arsenal'), [abilitiesOpen, setAbilitiesOpen] = useState(false);
  const [draft] = useState(starter), [selected, setSelected] = useState(builds[0]?.id ?? ''), [slot, setSlot] = useState<ArsenalSlot>('Warframe');
  const [picker, setPicker] = useState<PickerTarget | null>(null), [compareId, setCompareId] = useState('');
  const [active, setActive] = useState<UpgradeTarget>({ kind: 'mod', index: 0 });
  const [drag, setDrag] = useState<{ source: UpgradeSource; context: string } | null>(null), [hovered, setHovered] = useState('');
  const build = builds.find(build => build.id === selected) ?? builds[0] ?? draft;
  const equipment = build.equipment[slot], item = items.get(equipment.id), cap = capacity(equipment);
  const dragContext = `${build.id}:${slot}:${equipment.id}`;
  const dragged = !disabled && drag?.context === dragContext ? drag.source : null;
  const endDrag = () => { setDrag(null); setHovered(''); };
  const beginDrag = (event: DragEvent<HTMLElement>, source: UpgradeSource) => {
    if (disabled || !upgrades.has(source.id)) { event.preventDefault(); return; }
    event.dataTransfer.setData(upgradeDragType, source.id);
    event.dataTransfer.effectAllowed = source.from ? 'move' : 'copy';
    setDrag({ source, context: dragContext }); setHovered('');
  };
  const compareBuild = builds.find(build => build.id === compareId && build.id !== selected);
  const result = useMemo(() => calculate(build, slot, items), [build, slot]);
  const compared = useMemo(() => compareBuild ? calculate(compareBuild, slot, items) : undefined, [compareBuild, slot]);
  const problems = validateEquipment(equipment, item);
  const save = (next: ArsenalBuild) => { if (onSave(builds.some(build => build.id === next.id) ? builds.map(build => build.id === next.id ? next : build) : [...builds, next])) setSelected(next.id); };
  const updateEquipment = (next: EquipmentBuild) => save({ ...build, equipment: { ...build.equipment, [slot]: next } });
  const editUpgrade = (kind: UpgradeKind, index: number, value: UpgradeSlot) => updateEquipment(kind === 'mod' ? { ...equipment, mods: equipment.mods.map((mod, i) => i === index ? value : mod) } : kind === 'arcane' ? { ...equipment, arcanes: equipment.arcanes.map((arcane, i) => i === index ? value : arcane) } : { ...equipment, [kind]: value });
  const choose = (id: string, target: PickerTarget = picker ?? active) => {
    if (disabled) return;
    if (target.kind === 'equipment') {
      const nextItem = items.get(id)!;
      const next = equipItem(nextItem, slot);
      const retain = (old: UpgradeSlot, fresh: UpgradeSlot, kind: UpgradeKind) => { const mod = upgrades.get(old.id); return mod && compatible(mod, nextItem, kind) ? { ...old, polarity: fresh.polarity } : fresh; };
      next.mods = next.mods.map((fresh, index) => retain(equipment.mods[index], fresh, 'mod'));
      next.exilus = retain(equipment.exilus, next.exilus, 'exilus'); next.special = retain(equipment.special, next.special, 'special');
      next.arcanes = next.arcanes.map((fresh, index) => retain(equipment.arcanes[index], fresh, 'arcane'));
      updateEquipment(next);
    } else {
      const next = placeUpgrade(equipment, item, { id }, target as UpgradeTarget);
      if (!next) return;
      updateEquipment(next);
    }
    setPicker(null);
  };
  const moveMod = (index: number, direction: number) => {
    if (disabled) return;
    const other = index + direction;
    const next = placeUpgrade(equipment, item, { id: equipment.mods[index].id, from: { kind: 'mod', index } }, { kind: 'mod', index: other });
    if (!next) return;
    updateEquipment(next);
    setActive({ kind: 'mod', index: other });
  };
  const selectedUpgrade = active.kind === 'mod' ? equipment.mods[active.index] : active.kind === 'arcane' ? equipment.arcanes[active.index] : equipment[active.kind];
  const editor = (kind: UpgradeKind, index: number, label: string, value: UpgradeSlot) => {
    const mod = upgrades.get(value.id), current = active.kind === kind && active.index === index;
    const target = { kind, index }, key = `${kind}-${index}`;
    const placement = dragged && placeUpgrade(equipment, item, dragged, target);
    const dragging = dragged?.from?.kind === kind && dragged.from.index === index;
    const match = mod && value.polarity ? value.polarity === mod.polarity || value.polarity === 'universal' ? 'is-match' : 'is-mismatch' : '';
    return <div key={key} className={`modder-upgrade${current ? ' is-selected' : ''}${!value.enabled ? ' is-muted' : ''}${dragging ? ' is-drag-source' : ''}${placement ? ' is-drop-allowed' : ''}${placement && hovered === key ? ' is-drop-target' : ''}`}
      onDragOver={event => {
        if (!dragged || !event.dataTransfer.types.includes(upgradeDragType)) return;
        setHovered(key);
        event.dataTransfer.dropEffect = placement ? dragged.from ? 'move' : 'copy' : 'none';
        if (placement) event.preventDefault();
      }}
      onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setHovered(''); }}
      onDrop={event => {
        if (!dragged || !event.dataTransfer.types.includes(upgradeDragType)) return;
        event.preventDefault();
        if (placement) { updateEquipment(placement); setActive(target); }
        endDrag();
      }}
      onContextMenu={event => {
        event.preventDefault();
        if (!disabled && value.id) { editUpgrade(kind, index, emptyUpgrade(value.polarity)); setActive(target); endDrag(); }
      }}>
      <button type="button" className="modder-slot-button" aria-label={`${label}: ${mod?.name ?? (value.id ? 'Unavailable upgrade' : 'empty')}`} title={value.id ? 'Drag to move · Right-click to remove' : undefined} disabled={disabled} draggable={!disabled && Boolean(mod)} onDragStart={event => beginDrag(event, { id: value.id, from: target })} onDragEnd={endDrag} onClick={() => { setActive(target); setPicker(target); }}>
        <UpgradeFace mod={mod} label={label} rank={value.rank} drain={slotDrain(value, kind === 'special')} polarity={mod?.polarity || value.polarity} arcane={kind === 'arcane'} match={match} unavailable={Boolean(value.id)} />
      </button>
      <button type="button" className="modder-slot-adjust" aria-label={`Adjust ${label.toLowerCase()}`} title={`Adjust ${label.toLowerCase()}`} aria-pressed={current} onClick={() => { setActive({ kind, index }); requestAnimationFrame(() => document.querySelector('.modder-upgrade-controls')?.scrollIntoView({ block: 'nearest' })); }}>
        <span>{value.id ? `Rank ${value.rank}` : 'Select slot'}</span>{!value.enabled && <span>Excluded</span>}<PolarityIcon polarity={value.polarity} /><GameIcon name="edit" size={16} />
      </button>
    </div>;
  };
  const changeSlot = (next: ArsenalSlot) => { setSlot(next); setActive({ kind: 'mod', index: 0 }); setPicker(null); };
  const statGroups = [['Health', 'Shields', 'Armor', 'Energy'], ['Ability duration', 'Ability efficiency', 'Ability range', 'Ability strength']];
  const baseKeys = slot === 'Warframe' ? ['health', 'shield', 'armor', 'energy'] : ['criticalChance', 'criticalMultiplier', 'procChance', 'fireRate', 'multishot', 'magazineSize', 'reloadTime'];
  const baseLabels: Record<string, string> = { health: 'Health', shield: 'Shields', armor: 'Armor', energy: 'Energy', criticalChance: 'Critical chance (0–1)', criticalMultiplier: 'Critical multiplier', procChance: 'Status chance (0–1)', fireRate: 'Fire / attack rate', multishot: 'Base projectile count', magazineSize: 'Magazine', reloadTime: 'Reload seconds' };
  const jumpTo = (id: string) => { const element = document.getElementById(id); if (element instanceof HTMLDetailsElement) element.open = true; element?.scrollIntoView({ block: 'start' }); element?.focus({ preventScroll: true }); };
  const navigate = (next: 'arsenal' | 'upgrades') => { setView(next); setPicker(null); setAbilitiesOpen(false); endDrag(); requestAnimationFrame(() => { document.querySelector<HTMLElement>('.modder-title h1')?.focus({ preventScroll: true }); window.scrollTo({ top: 0 }); }); };
  const stats = slot !== 'Warframe' ? <ArsenalWeaponStats build={build} slot={slot} items={items} result={result} /> : <>{statGroups.map((group, index) => <div className="modder-stat-group" key={index}>{index === 1 && <h2>Ability</h2>}<dl>{group.map(label => { const step = result.steps.find(step => step.label === label); return step && <div key={label}><dt>{label.replace('Ability ', '')}</dt><dd data-arsenal-stat={label}>{valueText(step)}</dd></div>; })}</dl></div>)}<span className="modder-stats-note">{Object.keys(equipment.overrides).length ? 'Custom base stats' : 'Rank 30 base stats'}</span></>;
  const name = <ArsenalName key={build.id} name={build.name} disabled={disabled} onChange={name => save({ ...build, name })} />;
  const buildActions = <div className="modder-build-actions">
      {builds.length > 1 && <Select label="Arsenal build" value={build.id} options={builds.map(build => ({ value: build.id, label: build.name }))} onChange={id => { setSelected(id); setCompareId(''); }} />}
      <button type="button" className="button secondary" disabled={disabled || builds.length >= 100} onClick={() => { const next = starter(); next.name = `Arsenal ${builds.length + 1}`; save(next); }}><GameIcon name="add" size={16} />New arsenal</button>
      <button type="button" className="button secondary" disabled={disabled || builds.length >= 100} onClick={() => { const next = structuredClone(build); next.id = crypto.randomUUID(); next.name = `${build.name.slice(0, 93)} (copy)`; if (!builds.some(saved => saved.id === build.id)) { if (onSave([build, next])) { setSelected(next.id); setCompareId(build.id); } } else { save(next); setCompareId(build.id); } }}><GameIcon name="copy" size={16} />Duplicate</button>
    </div>;
  return <section className={`modder-page modder-theme is-${view}`} aria-label="Modding Calculator" onKeyDown={event => {
    if (event.key === 'Escape' && view === 'upgrades' && !picker && !abilitiesOpen && !event.defaultPrevented && !document.querySelector('[role="listbox"]') && !(event.target as HTMLElement).closest('input, textarea, select')) {
      event.preventDefault(); if (drag) endDrag(); else navigate('arsenal');
    }
  }}>
    <div className="modder-toolbar"><div className="modder-title"><h1 tabIndex={-1}>{view === 'arsenal' ? 'Arsenal' : <><span>Upgrades /</span> {item?.name ?? slot}</>}</h1></div><span className="modder-session-note">{canSave ? 'Account arsenal' : 'Temporary arsenal'}</span>{view === 'upgrades' && <button type="button" className="modder-game-button modder-header-back" aria-label="Return to Arsenal" onClick={() => navigate('arsenal')}><GameIcon name="back" size={18} />Arsenal</button>}</div>
    <div className="modder-layout">
    {view === 'arsenal' ? <ArsenalOverview build={build} slot={slot} item={item} name={name} stats={stats} actions={<>{!canSave && <button type="button" className="modder-game-button" onClick={onSignIn}>Sign in to save</button>}{buildActions}</>} disabled={disabled} onSlot={changeSlot} onSwap={() => setPicker({ kind: 'equipment', index: 0 })} onUpgrade={() => navigate('upgrades')} onAbilities={() => setAbilitiesOpen(true)} /> :
    <div className="modder-workbench">
    <div className="modder-workspace">
      <aside className={`modder-arsenal-panel${slot !== 'Warframe' ? ' is-weapon' : ''}`} aria-label="Arsenal and stats">
        <div className="modder-stat-panel">
          <div className="modder-capacity"><div><span>Capacity</span><strong className={cap.used > cap.total ? 'error-text' : ''}>{cap.total - cap.used} / {cap.total}</strong></div><progress aria-label="Mod capacity remaining" value={Math.max(0, cap.total - cap.used)} max={Math.max(1, cap.total)} /><span>{cap.used} used · {cap.total - cap.used < 0 ? `${cap.used - cap.total} over` : `${cap.total - cap.used} remaining`}</span></div>
          {stats}
          <button type="button" className="modder-rank-bonuses" onClick={() => jumpTo('modder-base-stats')}><GameIcon name="stats" size={22} />{slot === 'Warframe' ? 'Rank bonuses' : 'Base stats & attack'}</button>
        </div>
        <AbilityIcons item={item} onOpen={() => setAbilitiesOpen(true)} />
      </aside>
      <div className="modder-editor">
        <div className="modder-config-bar"><div className="modder-configs" aria-label="Arsenal configurations">{name}{builds.filter(entry => entry.id !== build.id).slice(0, 2).map(entry => <button type="button" key={entry.id} className="modder-config-tab" onClick={() => { setSelected(entry.id); setCompareId(''); }}>{entry.name}</button>)}<button type="button" className="modder-config-add" aria-label="Add configuration" title="Duplicate as a new configuration" disabled={disabled || builds.length >= 100} onClick={() => { const next = structuredClone(build); next.id = crypto.randomUUID(); next.name = `Config ${String.fromCharCode(65 + Math.min(builds.length || 1, 25))}`; if (!builds.some(entry => entry.id === build.id)) { if (onSave([build, next])) setSelected(next.id); } else save(next); }}><GameIcon name="add" size={22} /></button></div><button type="button" className="modder-config-edit" aria-label="Rename arsenal" title="Rename arsenal" onClick={() => document.querySelector<HTMLInputElement>('.modder-name input')?.select()}><GameIcon name="edit" size={26} /></button></div>
        {item && <>
          <div className="modder-upgrade-stage">
            <div className={`modder-slot-layout${slot === 'Primary' || slot === 'Secondary' ? ' is-firearm' : ''}`}>
              <div className="modder-slot-grid">
                {(slot === 'Warframe' || slot === 'Melee') && <div className="modder-extra-grid">{editor('special', 0, slot === 'Warframe' ? 'Aura' : 'Stance', equipment.special)}{editor('exilus', 0, 'Exilus', equipment.exilus)}</div>}
                <div className="modder-mod-grid" role="group" aria-label="Eight mod slots">{equipment.mods.map((value, index) => editor('mod', index, `Mod ${index + 1}`, value))}</div>
              </div>
              <div className="modder-arcane-grid" role="group" aria-label="Arcane slots">{equipment.arcanes.map((value, index) => editor('arcane', index, `Arcane ${index + 1}`, value))}{(slot === 'Primary' || slot === 'Secondary') && editor('exilus', 0, 'Exilus', equipment.exilus)}</div>
            </div>
          </div>
          {slot !== 'Warframe' && <p className="modder-order-note">Elements combine in slot order, 1–8.</p>}
          {!!problems.length && <div className="callout caution"><strong>Build needs attention:</strong> {problems.join(' ')}</div>}
        </>}
      </div>
    </div>
    {item && <ModderLibrary key={`${slot}-${active.kind}`} inline target={active} item={item} slot={slot} equipment={equipment} conditions={build.conditions} disabled={disabled} onChoose={id => choose(id, active)} onDragUpgrade={(event, id) => beginDrag(event, { id })} onDragEnd={endDrag} />}
    <div className="modder-game-footer">
      <button type="button" className="modder-game-button" onClick={() => jumpTo('modder-results')}><GameIcon name="stats" size={22} />Calculations</button>
      <button type="button" className="modder-game-button" onClick={() => jumpTo('modder-actions')}>Actions</button>
      <button type="button" className="modder-game-button" disabled={disabled || ![...equipment.mods, equipment.special, equipment.exilus, ...equipment.arcanes].some(value => value.id)} onClick={() => updateEquipment({ ...equipment, mods: equipment.mods.map(value => emptyUpgrade(value.polarity)), special: emptyUpgrade(equipment.special.polarity), exilus: emptyUpgrade(equipment.exilus.polarity), arcanes: equipment.arcanes.map(value => emptyUpgrade(value.polarity)) })}>Remove all</button>
      <button type="button" className="modder-game-button" onClick={() => navigate('arsenal')}>Back</button>
    </div>
    {item && <div className="modder-inspector">
      <UpgradeControls label={targetLabel(active, slot)} kind={active.kind} index={active.index} value={selectedUpgrade} conditions={build.conditions} disabled={disabled} onChange={value => editUpgrade(active.kind, active.index, value)} onPick={() => setPicker(active)} onMove={active.kind === 'mod' ? direction => moveMod(active.index, direction) : undefined} />
    </div>}
    <div className="modder-settings-column">
      <div id="modder-actions" className="modder-build-settings" tabIndex={-1}><h2>Arsenal actions</h2>{buildActions}
        <div className="modder-capacity-options"><GameIcon name="reactor" size={42} /><label><input type="checkbox" checked={equipment.doubled} disabled={disabled} onChange={event => updateEquipment({ ...equipment, doubled: event.target.checked })} />{slot === 'Warframe' ? 'Orokin Reactor' : 'Orokin Catalyst'}</label><Numeric label="Base capacity" value={equipment.capacity} max={40} onChange={capacity => updateEquipment({ ...equipment, capacity })} disabled={disabled} />{cap.bonus > 0 && <span className="small muted">+{cap.bonus} from {slot === 'Warframe' ? 'aura' : 'stance'}</span>}</div>
        {builds.length > 1 && <Select label="Compare with arsenal" value={compareBuild?.id ?? ''} options={[{ value: '', label: 'No comparison' }, ...builds.filter(entry => entry.id !== build.id).map(entry => ({ value: entry.id, label: `Compare: ${entry.name}` }))]} onChange={setCompareId} />}
        {builds.some(entry => entry.id === build.id) && <button type="button" className="button quiet" disabled={disabled} onClick={() => { const remaining = builds.filter(entry => entry.id !== build.id); if (onSave(remaining)) { setSelected(remaining[0]?.id ?? ''); setCompareId(''); } }}>Delete arsenal</button>}
      </div>
      {item && <>
        <Conditions value={build.conditions} onChange={conditions => save({ ...build, conditions })} disabled={disabled} slot={slot} />
        <details id="modder-base-stats" className="modder-settings" tabIndex={-1}><summary>Base stats & attack <span>{Object.keys(equipment.overrides).length ? 'Custom values' : slot === 'Warframe' ? 'Rank 30' : 'Catalog values'}</span><GameIcon name="down" size={16} /></summary><div className="modder-setting-body">
          {slot !== 'Warframe' && <Select label="Attack component" value={String(equipment.attack)} options={[{ value: '-1', label: 'Base attack (catalog)' }, ...(item.attacks ?? []).map((attack, index) => ({ value: String(index), label: attack.name }))]} onChange={attack => updateEquipment({ ...equipment, attack: Number(attack), overrides: {} })} disabled={disabled} />}
          <p className="small muted">{slot === 'Warframe' ? 'Unmodded stats at rank 30. Enter missing values from your Arsenal.' : 'Overrides are unmodded values. Critical and status chances use fractions: 0.25 = 25%.'}</p>
          <div className="modder-fields">{baseKeys.map(key => { const base = slot === 'Warframe' ? (frames.get(item.id)?.ranked as Record<string, number> | null)?.[key] : weaponSourceStats(item, equipment.attack)[key]; return <label className="modder-number" key={key}><span>{baseLabels[key]}</span><input type="number" aria-label={`Unmodded ${baseLabels[key]}`} min={0} max={100000} step="any" placeholder={typeof base === 'number' ? String(base) : 'Unknown'} value={equipment.overrides[key] ?? ''} disabled={disabled} onChange={event => { const overrides = { ...equipment.overrides }; if (!event.currentTarget.value) delete overrides[key]; else { const value = event.currentTarget.valueAsNumber; if (!Number.isFinite(value) || value < 0 || value > 100000) return; overrides[key] = value; } updateEquipment({ ...equipment, overrides }); }} /></label>; })}</div>
          <button type="button" className="button quiet" disabled={disabled || !Object.keys(equipment.overrides).length} onClick={() => updateEquipment({ ...equipment, overrides: {} })}>Restore source stats</button>
        </div></details>
      </>}
      <details className="modder-settings"><summary>Build notes<GameIcon name="down" size={16} /></summary><textarea aria-label="Build notes" rows={4} maxLength={10000} value={build.notes} disabled={disabled} onChange={event => save({ ...build, notes: event.target.value })} placeholder="Rotation, testing assumptions, or equipment to try…" /></details>
      <details className="modder-settings modder-sources"><summary>Sources & model coverage<GameIcon name="down" size={16} /></summary><div className="modder-setting-body">
        <p>{mods.length} mods and {arcanes.length} arcanes from the <a href={modderData.source} target="_blank" rel="noreferrer">equipment data snapshot</a>. Effects marked Partial or Reference only are listed under calculation limits. Rank values in descriptions are rounded; linear modifiers use the maximum value scaled by rank.</p>
        <p>Warframe rank bonuses follow <a href={modderData.rankSource} target="_blank" rel="noreferrer">Update 34</a> where available, added to this snapshot’s unranked stats. Verify later balance changes with the editable base stats.</p>
        <p>Weapon reference attributes from WARFRAME Wiki contributors: {weaponReferenceSources.map((source, index) => <span key={source.url}>{index > 0 && ' · '}<a href={source.url} target="_blank" rel="noreferrer">{['Primary', 'Secondary', 'Melee'][index]}</a></span>)} (<a href="https://creativecommons.org/licenses/by-nc-sa/3.0/" target="_blank" rel="noreferrer">CC BY-NC-SA 3.0</a>). Dotted values are base references; their modifiers are not simulated.</p>
        <p><a href="https://support.warframe.com/hc/en-us/articles/200500194-Mod-Guide-Use-Fusion-Transmutation-Sale" target="_blank" rel="noreferrer">Mod capacity and polarity</a> · <a href="https://wiki.warframe.com/w/Damage/Calculation" target="_blank" rel="noreferrer">Damage mechanics</a></p>
        <p>Companions, Exalted equipment, Rivens, shards, set bonuses, ability-specific simulations, and Incarnon evolutions are outside this model. Alternate attacks calculate one component at a time.</p>
      </div></details>
      {!canSave && <button type="button" className="button quiet" onClick={onSignIn}>Sign in to save arsenals</button>}
    </div>
    </div>}
    <Results key={slot} result={result} compared={compared} compareName={compareBuild?.name} />
    </div>
    {!!result.headline.length && <div className="modder-mobile-results"><span><small>{result.headline[0].label}</small><strong>{valueText(result.headline[0])}</strong></span><span><small>{result.headline[1].label}</small><strong>{valueText(result.headline[1])}</strong></span><button type="button" className="button secondary" onClick={() => { const results = document.getElementById('modder-results'); results?.scrollIntoView({ block: 'start' }); results?.focus({ preventScroll: true }); }}>Results<GameIcon name="forward" size={14} /></button></div>}
    {picker && <ModderLibrary key={`${slot}-${picker.kind}-${picker.index}`} target={picker} item={item} slot={slot} equipment={equipment} conditions={build.conditions} disabled={disabled} onChoose={id => choose(id)} onClose={() => setPicker(null)} />}
    {abilitiesOpen && item && <AbilitiesDialog item={item} onClose={() => setAbilitiesOpen(false)} />}
  </section>;
}
