import { useDeferredValue, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Clock3, MapPin, Search, X } from 'lucide-react';
import { craftingItems } from '../data/crafting';
import { buildCraftingTree, checkCraftingNode, chooseCraftingRecipe, craftingAreas, craftingDuration, craftingKinds, craftingPlanLabel, craftingSummary, setCraftingProgress, MAX_CRAFTING_PLANS } from '../lib/crafting';
import type { CraftingItem, CraftingNode, CraftingPlan } from '../lib/crafting';
import { Select } from './Select';
import { useToasts } from './Toasts';
import { CraftingArtwork } from './CraftingArtwork';
import { CraftingDetails } from './CraftingDetails';
import { Currency, CurrencySymbol } from './Currency';
import { QuantitySlider } from './QuantitySlider';
import { Tooltip } from './Tooltip';
import './Crafting.css';

const number = (value: number) => value.toLocaleString();
const craftable = [...craftingItems.values()].filter(item => item.recipes.length).sort((a, b) => a.name.localeCompare(b.name));
const featured = ['Rhino', 'Wisp Prime', 'Boltor', 'Akjagara', 'Forma', 'Fieldron', 'Dragon Nikana', 'Ignis Wraith'];
const itemType = (item: CraftingItem) => item.modular ? 'Modular component' : item.catalogItem?.weaponType || (item.kind === 'Materials' ? 'Material / component' : item.kind);

function ItemPicker({ onPick, disabled }: { onPick: (item: CraftingItem) => void; disabled: boolean }) {
  const [query, setQuery] = useState(''), [kind, setKind] = useState('All'), [limit, setLimit] = useState(24);
  const deferred = useDeferredValue(query);
  const matches = useMemo(() => {
    const terms = deferred.trim().toLowerCase().split(/\s+/);
    return craftable.filter(item => (kind === 'All' || item.kind === kind) && terms.every(term => `${item.name} ${item.recipes.map(recipe => recipe.name).join(' ')}`.toLowerCase().includes(term)));
  }, [deferred, kind]);
  const preview = !deferred.trim() && kind === 'All';
  const visible = preview ? featured.flatMap(name => { const item = craftable.find(item => item.name === name); return item ? [item] : []; }) : matches.slice(0, limit);
  return <div className="craft-picker">
    <div className="craft-picker-tools"><label className="search-field"><Search size={17} /><input type="search" aria-label="Search craftable items" placeholder="Search items or blueprints…" value={query} onChange={event => { setQuery(event.target.value); setLimit(24); }} /></label><Select label="Crafting category" value={kind} options={['All', ...craftingKinds].map(value => ({ value, label: value === 'All' ? 'All items' : value }))} onChange={value => { setKind(value); setLimit(24); }} /></div>
    <div className="craft-picker-heading"><h2>{preview ? 'Choose an item' : 'Results'}</h2><span className="small muted" role="status">{preview ? `${craftable.length.toLocaleString()} craftable items` : `${matches.length.toLocaleString()} items`}</span></div>
    <div className="craft-picker-grid">{visible.map(item => <button type="button" key={item.id} className="craft-pick" aria-label={`Craft ${item.name}`} disabled={disabled} onClick={() => onPick(item)}><CraftingArtwork item={item} /><span className="craft-pick-copy"><strong>{item.name}</strong><span>{itemType(item)}<ChevronRight size={14} /></span></span></button>)}</div>
    {!matches.length && <p className="craft-empty muted">No craftable items match. Try another name or category.</p>}
    {!preview && matches.length > limit && <button type="button" className="button secondary craft-more" onClick={() => setLimit(value => value + 24)}>Show more</button>}
    {preview && <button type="button" className="button quiet craft-more" onClick={() => setKind('Weapons')}>Browse weapons</button>}
  </div>;
}

function RecipeCost({ node }: { node: CraftingNode }) {
  return node.recipe ? <div className="craft-cost"><span title="Time per build"><Clock3 size={13} />{craftingDuration(node.recipe.seconds)}</span><span title="Build credits remaining">{node.recipe.credits === null ? 'Cost unknown' : <Currency value={node.recipe.credits * node.batches} currency="credits" />}</span>{node.batches > 1 && <span>{number(node.batches)} builds</span>}{node.recipe.quantity > 1 && <span>{number(node.recipe.quantity)} / build</span>}</div> : null;
}

type NodeActions = { onCheck: (key: string, checked: boolean, quantity: number) => void; onProgress: (key: string, amount: number, quantity: number) => void; onChoice: (key: string, choice: string) => void; onDetails: (node: CraftingNode) => void; disabled: boolean };

function SourceButton({ node, onClick }: { node: CraftingNode; onClick: () => void }) {
  const text = node.item.location || 'Source details';
  return <Tooltip className="button quiet craft-source-button" label={`Sources for ${node.item.name}: ${text}`} content={text} hasPopup onClick={onClick}><MapPin size={14} aria-hidden="true" /><span>{text}</span></Tooltip>;
}

function IngredientList({ nodes, actions, parent, nested = false, id, hidden }: { nodes: CraftingNode[]; actions: NodeActions; parent: string; nested?: boolean; id?: string; hidden?: boolean }) {
  const list = useRef<HTMLUListElement>(null);
  const focus = useRef<string | null>(null);
  const completed = nodes.filter(node => node.completed);
  const rememberFocus = (key: string, done: boolean) => { if (nodes.some(node => node.key === key && node.completed !== done)) focus.current = key; };
  const localActions: NodeActions = { ...actions,
    onCheck: (key, checked, quantity) => { rememberFocus(key, checked); actions.onCheck(key, checked, quantity); },
    onProgress: (key, amount, quantity) => { rememberFocus(key, amount === quantity); actions.onProgress(key, amount, quantity); },
  };
  useLayoutEffect(() => {
    if (!focus.current) return;
    const target = focus.current; focus.current = null;
    list.current?.querySelector<HTMLInputElement>(`[data-node-key="${CSS.escape(target)}"] input[type="checkbox"]`)?.focus({ preventScroll: true });
  }, [nodes]);
  return <ul ref={list} id={id} className={nested ? 'craft-children' : 'craft-branches'} hidden={hidden}>
    {nodes.filter(node => !node.completed).map(node => <IngredientNode key={node.key} node={node} actions={localActions} />)}
    {!!completed.length && <li className="craft-completed-group"><ul aria-label={`Completed ingredients for ${parent}`}>
      {completed.map(node => <li key={node.key} data-node-key={node.key} className="craft-completed-item"><label><input type="checkbox" aria-label={`Completed: ${node.item.name}`} checked disabled={actions.disabled} onChange={() => localActions.onCheck(node.key, false, node.quantity)} /><CraftingArtwork item={node.item} /><span>{node.item.name}</span><small>× {number(node.quantity)}</small></label><button type="button" className="icon-button" aria-label={`Sources for ${node.item.name}`} title={`Sources for ${node.item.name}`} onClick={() => actions.onDetails(node)}><MapPin size={14} aria-hidden="true" /></button></li>)}
    </ul></li>}
  </ul>;
}

function IngredientNode({ node, actions }: { node: CraftingNode; actions: NodeActions }) {
  const [collapsed, setCollapsed] = useState(false);
  const childrenId = useId();
  const expanded = !collapsed;
  return <li className="craft-branch" data-item={node.item.name} data-node-key={node.key}>
    <div className={`craft-node ${node.recipe ? 'is-build' : 'is-material'}`}>
      <div className="craft-node-main"><label className="craft-check"><input type="checkbox" aria-label={`Completed: ${node.item.name}`} checked={node.completed} disabled={actions.disabled} onChange={event => actions.onCheck(node.key, event.target.checked, node.quantity)} /></label><CraftingArtwork item={node.item} /><div className="craft-node-copy"><span className="craft-node-name">{node.item.name}</span><span className="craft-quantity">{node.acquired ? `${number(node.remaining)} left` : `× ${number(node.quantity)}`}</span></div></div>
      {!node.completed && <RecipeCost node={node} />}
      {node.item.equipment && <span className="craft-consumed">Consumes equipment</span>}
      {node.problem && <p className="small error-text">{node.problem}</p>}
      <QuantitySlider name={node.item.name} value={node.acquired} max={node.quantity} batchSize={node.recipe?.quantity} disabled={actions.disabled} onChange={amount => actions.onProgress(node.key, amount, node.quantity)} />
      <div className="craft-node-actions"><SourceButton node={node} onClick={() => actions.onDetails(node)} />
        {!!node.children.length && <button type="button" className="icon-button craft-expand" aria-label={`${expanded ? 'Collapse' : 'Expand'} ${node.item.name} ingredients`} aria-expanded={expanded} aria-controls={childrenId} title={`${expanded ? 'Collapse' : 'Expand'} ${node.item.name} ingredients`} onClick={() => setCollapsed(value => !value)}>{expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button>}
      </div>
    </div>
    {!!node.children.length && <IngredientList id={childrenId} nodes={node.children} actions={actions} parent={node.item.name} nested hidden={!expanded} />}
  </li>;
}

export function Crafting({ plans, selected, onSelect, onSave, canSave, onSignIn, disabled }: { plans: CraftingPlan[]; selected: string; onSelect: (id: string) => void; onSave: (plans: CraftingPlan[]) => boolean; canSave: boolean; onSignIn: () => void; disabled: boolean }) {
  const [details, setDetails] = useState<{ item: CraftingItem; node?: CraftingNode }>();
  const [quantity, setQuantity] = useState('1');
  const [expandedProgressPlan, setExpandedProgressPlan] = useState('');
  const tabsId = useId(), page = useRef<HTMLElement>(null);
  const focusPlan = useRef(false);
  const notify = useToasts();
  const plan = plans.find(plan => plan.id === selected);
  const root = useMemo(() => plan ? buildCraftingTree(craftingItems, plan) : null, [plan]);
  const summary = useMemo(() => root ? craftingSummary(root) : null, [root]);
  const areas = useMemo(() => craftingAreas(summary?.materials ?? []), [summary]);
  useEffect(() => { setQuantity(String(plan?.quantity ?? 1)); }, [plan?.id, plan?.quantity]);
  useEffect(() => {
    if (!focusPlan.current) return;
    focusPlan.current = false;
    const target = page.current?.querySelector<HTMLElement>(plan ? '[role="tab"][aria-selected="true"]' : '.craft-picker input[type="search"]');
    target?.focus({ preventScroll: true }); target?.scrollIntoView({ block: 'nearest' });
  }, [plan?.id]);
  const savePlan = (next: CraftingPlan) => onSave(plans.map(entry => entry.id === next.id ? next : entry));
  const addPlan = (item: CraftingItem) => {
    if (plans.length >= MAX_CRAFTING_PLANS) { notify(`Remove a plan before adding more (${MAX_CRAFTING_PLANS} maximum).`, 'caution'); return; }
    const next: CraftingPlan = { id: crypto.randomUUID(), itemId: item.id, quantity: 1, completed: [], choices: {} };
    if (!onSave([...plans, next])) return;
    focusPlan.current = true; onSelect(next.id);
  };
  const updateQuantity = (value = quantity, final = true) => {
    if (!plan) return;
    const next = Number(value);
    if (!Number.isSafeInteger(next) || next < 1 || next > 100) { if (final) { setQuantity(String(plan.quantity)); notify('Choose a quantity from 1 to 100.', 'caution'); } return; }
    if (next !== plan.quantity && savePlan({ ...plan, quantity: next, completed: [], progress: {} })) notify('Quantity updated. Progress reset for this plan.', 'neutral');
  };
  const closePlan = (id: string) => {
    const index = plans.findIndex(entry => entry.id === id);
    const remaining = plans.filter(entry => entry.id !== id);
    if (!onSave(remaining)) return;
    if (plan?.id === id) {
      focusPlan.current = true;
      onSelect(remaining[Math.min(index, remaining.length - 1)]?.id ?? '');
    } else {
      requestAnimationFrame(() => (page.current?.querySelector<HTMLButtonElement>('[role="tab"][aria-selected="true"]') ?? page.current?.querySelector<HTMLButtonElement>('[role="tab"]'))?.focus({ preventScroll: true }));
    }
  };
  const actions: NodeActions = {
    onCheck: (key, checked, quantity) => { if (plan) savePlan(checkCraftingNode(plan, key, checked, quantity)); },
    onProgress: (key, amount, quantity) => { if (plan) savePlan(setCraftingProgress(plan, key, amount, quantity)); },
    onChoice: (key, choice) => { if (plan) savePlan(chooseCraftingRecipe(plan, key, choice)); },
    onDetails: node => setDetails({ item: node.item, node }), disabled,
  };
  return <section ref={page} className="crafting-page" aria-label="Crafting">
    {!!plans.length && <div className="craft-plan-toolbar">
        <div className="craft-plan-tabs" role="tablist" aria-label="Crafting plans" onKeyDown={event => {
          if (event.altKey || event.ctrlKey || event.metaKey) return;
          const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
          const index = buttons.indexOf(event.target as HTMLButtonElement);
          if (index < 0) return;
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? plans.length - 1 : event.key === 'ArrowRight' ? (index + 1) % plans.length : event.key === 'ArrowLeft' ? (index - 1 + plans.length) % plans.length : -1;
          if (next < 0) return;
          event.preventDefault(); onSelect(plans[next].id); buttons[next].focus({ preventScroll: true }); buttons[next].scrollIntoView({ block: 'nearest' });
        }}>
          {plans.map((entry, index) => {
            const item = craftingItems.get(entry.itemId), active = entry.id === plan?.id;
            return <div className="craft-plan-tab" key={entry.id} role="presentation"><button type="button" role="tab" id={`${tabsId}-tab-${index}`} aria-controls={`${tabsId}-panel-${index}`} aria-selected={active} tabIndex={active || !plan && index === 0 ? 0 : -1} onClick={() => onSelect(entry.id)}>
              {item && <CraftingArtwork item={item} />}<span>{craftingPlanLabel(plans, index, item?.name ?? 'Unavailable item')}</span>
            </button><button type="button" className="craft-plan-close" aria-label={`Close ${craftingPlanLabel(plans, index, item?.name ?? 'Unavailable item')} plan`} title="Close plan" disabled={disabled} onClick={() => closePlan(entry.id)}><X size={13} aria-hidden="true" /></button></div>;
          })}
        </div>
      </div>}
      {!plan && <><ItemPicker onPick={addPlan} disabled={disabled || plans.length >= MAX_CRAFTING_PLANS} />{plans.length >= MAX_CRAFTING_PLANS && <p className="small muted">Remove a plan to add another ({MAX_CRAFTING_PLANS} maximum).</p>}</>}
      {plans.map((entry, index) => <div key={entry.id} role="tabpanel" id={`${tabsId}-panel-${index}`} aria-labelledby={`${tabsId}-tab-${index}`} hidden={entry.id !== plan?.id}>
      {entry.id === plan?.id && <>
      {!root || !summary ? <p className="callout caution">This item is no longer in the catalog. Your plan has been kept.</p> : <>
        <div className={`craft-root${root.completed ? ' is-complete' : ''}`}><CraftingArtwork item={root.item} /><div className="craft-root-copy"><span className="craft-eyebrow">{itemType(root.item)}</span><h2>{root.item.name}</h2>{!root.completed && <RecipeCost node={root} />}{root.problem && <p className="small error-text">{root.problem}</p>}{!root.completed && <SourceButton node={root} onClick={() => actions.onDetails(root)} />}</div>
          <div className="craft-root-controls">{(root.quantity > 1 || root.completed || expandedProgressPlan === plan.id || Object.hasOwn(plan.progress ?? {}, root.key)) && <QuantitySlider name={root.item.name} value={root.acquired} max={root.quantity} batchSize={root.recipe?.quantity} disabled={disabled} onChange={amount => { setExpandedProgressPlan(plan.id); actions.onProgress(root.key, amount, root.quantity); }} />}<label className="craft-amount">Quantity<input type="number" aria-label="Craft quantity" min={1} max={100} value={quantity} disabled={disabled} onChange={event => { setQuantity(event.target.value); updateQuantity(event.target.value, false); }} onBlur={() => updateQuantity()} onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }} title="Changing quantity resets this plan’s progress." /></label>{(root.item.recipes.length > 1 || root.problem && !root.recipe) && <Select label="Recipe" value={root.recipe?.id || 'Recipe unavailable'} options={root.item.recipes.map((recipe, index) => ({ value: recipe.id, label: `Recipe ${index + 1}${recipe.quantity > 1 ? ` · ${number(recipe.quantity)} / build` : ''} · ${recipe.credits === null ? 'Unknown cost' : `${number(recipe.credits)} credits`}`, content: <>Recipe {index + 1}{recipe.quantity > 1 && <> · {number(recipe.quantity)} / build</>} · {recipe.credits === null ? 'Unknown cost' : <Currency value={recipe.credits} currency="credits" />}</> }))} onChange={value => actions.onChoice(root.key, value)} disabled={disabled} />}</div>
        </div>
        <div className="craft-progress"><span><strong>{summary.completed} / {summary.total}</strong> steps complete</span><progress max={summary.total} value={summary.completed} aria-label="Crafting progress" /><span><CurrencySymbol currency="credits" decorative /><strong>{number(summary.credits)}{summary.unknownCredits ? '+' : ''}</strong> build credits left</span></div>
        <div className="craft-workspace"><section className="craft-tree" aria-label="Ingredient tree"><div className="craft-section-heading"><h3>Ingredients</h3><span className="small muted">{root.completed ? 'Complete' : `${root.children.length} branches`}</span></div>
          {root.completed ? <div className="craft-completed"><Check size={24} /><p>{root.item.name} completed.</p><span className="small muted">Set completed quantity to 0 to return to ingredients.</span></div> : <IngredientList nodes={root.children} actions={actions} parent={root.item.name} />}
        </section><div className="craft-sidebar">
          <aside className="craft-remaining" aria-label="Remaining materials"><div className="craft-section-heading"><h3>Remaining materials</h3><span className="count">{summary.materials.length}</span></div>{summary.materials.length ? <ul>{summary.materials.map(({ item, quantity }) => <li key={item.id}><button type="button" aria-label={`Sources for remaining ${item.name}`} onClick={() => setDetails({ item })}><CraftingArtwork item={item} /><span>{item.name}</span><strong>{number(quantity)}</strong></button></li>)}</ul> : <p className="small muted">{root.completed ? 'Nothing left to gather.' : 'Materials gathered. Finish the remaining builds.'}</p>}</aside>
          <aside className="craft-areas" aria-label="Areas required to visit"><div className="craft-section-heading"><h3>Areas required to visit</h3></div>{areas.length ? <><p className="craft-area-hint">Listed locations are alternatives.</p><ul>{areas.map(area => <li key={area.location}><div className="craft-area-location"><MapPin size={14} aria-hidden="true" /><span>{area.location || 'Location not listed'}</span></div><div className="craft-area-materials">{area.items.map(item => <button type="button" key={item.id} aria-label={`Area sources for ${item.name}`} onClick={() => setDetails({ item })}>{item.name}</button>)}</div></li>)}</ul></> : <p className="small muted">No areas left to visit.</p>}</aside>
        </div></div>
      </>}
      </>}
      </div>)}
    {!canSave && <button type="button" className="button quiet craft-sign-in" onClick={onSignIn}>Sign in to save plans</button>}
    {details && <CraftingDetails key={details.item.id} item={details.item} node={details.node} onRecipe={details.node && !disabled ? choice => { actions.onChoice(details.node!.key, choice); setDetails(undefined); } : undefined} onClose={() => setDetails(undefined)} />}
  </section>;
}
