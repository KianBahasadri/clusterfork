# Warframe Toolkit Application Reference

The [Warframe Toolkit reference](../assets/warframe-toolkit/index.html) (`Grakata`) demonstrates how the design-guide's tokens, controls, data tables, charts, dialogs, and popovers scale to a full multi-view React + TypeScript application. Unlike the single-page [Dashboard Reference](../assets/dashboard-reference/index.html), it includes both a directly runnable browser bundle (`index.html` + `bundle.js` + `bundle.css`) and the complete React 19 + TypeScript source under [`assets/warframe-toolkit/src/`](../assets/warframe-toolkit/src/).

Component token values, typography, and base control geometry remain owned by [Foundations](foundations.md), [Controls and Forms](controls-and-forms.md), [Data Display](data-display.md), and [Overlays and Feedback](overlays-and-feedback.md). Use this reference for multi-view application architecture, React state/accessibility wiring, and the five domain workflows below.

## Application Shell and Navigation (`App.tsx`, `CraftingNav.tsx`)

- **Topbar layout:** Place primary destination links on the left (`.collection-nav`) and global utilities on the right (`.topbar-right`: command palette trigger, theme toggle, and account status/actions). Separate tool views (`Drop Tables`, `Crafting`, `Modding Calculator`) from collection inventories (`Weapons`, `Warframes`, `Relics`) with a 1px vertical divider (`.nav-group-start`).
- **Icon rendering:** Render monochrome game/category glyphs with CSS `mask-image` and `background: currentColor` (`.nav-icon`) so active, hover, and light/dark theme states recolor automatically without duplicate asset files. Retain full-color `<img>` rendering only for semantic currency icons (`Platinum`, `Credits`, `Ducats`).
- **Hover + keyboard submenu (`CraftingNav.tsx`):** The `Crafting` nav item acts as both a direct destination (`#crafting`) and a menu trigger (`aria-haspopup="menu"`) for open crafting plans:
  - Open on pointer enter (non-touch) with a 200ms pointer-leave grace timer so diagonal cursor movement into `.craft-plan-menu` does not dismiss the menu.
  - Support `ArrowDown`, `ArrowUp`, and `Space` on the trigger to open and focus the first or last `[role="menuitem"]`, and `ArrowDown` / `ArrowUp` / `Home` / `End` / `Escape` inside the menu.
  - Clamp the floating menu to `window.visualViewport` bounds using `ResizeObserver` and scroll/resize listeners.
- **Global command palette (`Commands.tsx`):** Bind `Ctrl+K` / `⌘+K` / `Ctrl+Space` and `Alt+J` to a modal command launcher filtering both contextual page actions (`Alt+N`, `Alt+I`, `Alt+E`) and top-level destinations (`Alt+1` through `Alt+6`). Suppress `Alt` shortcuts when focus is inside an input, textarea, combobox, contenteditable, or open `<dialog>`.

## 1. Collection Tables and Market Explorer (`CollectionTable.tsx`, `MarketDialog.tsx`)

Demonstrated on `#weapons`, `#warframes`, and `#relics` ([`CollectionTable.tsx`](../assets/warframe-toolkit/src/components/CollectionTable.tsx), [`InventoryTable.tsx`](../assets/warframe-toolkit/src/components/InventoryTable.tsx), [`Relics.tsx`](../assets/warframe-toolkit/src/components/Relics.tsx)):

- **Generic collection contract (`CollectionDefinition<T>`):** Share one table shell across distinct schemas (`EquipmentRow` and `RelicRow`) by parameterizing `columns`, `defaultColumns`, `storageKey`, `model` (`sort`, `value`, `filterValue`, `filterChoices`), `filters`, `applyFilters`, `summary`, and `emptyState`.
- **Column management:**
  - Persist visible column keys and order in `localStorage`. Always keep the primary `name` column pinned first (`locked: true`).
  - Allow drag-and-drop column reordering directly on `<th>` headers (`draggable`, `.drag-before` / `.drag-after` indicators) as well as keyboard reorder buttons inside the `Columns` popover.
  - Provide per-column sort cycling (ascending → descending → unsorted, with `Shift`-click multi-column sort priority badges) and per-column filter popovers (`Shift+F10` or clicking the header filter icon).
- **Keyboard row navigation:** Clicking or pressing `ArrowUp` / `ArrowDown` inside `<tbody>` moves `.selected-row` selection across visible rows; pressing `Enter` or `Space` on a selected row (outside interactive cell controls) opens the item's detail/edit dialog.
- **Inline debounced notes (`TableNotes.tsx`):** Keep table note inputs lightweight during typing and commit after a 600ms debounce or on blur, emitting a confirmation toast only after the state commit succeeds.
- **Lazy Warframe Market quotes and history chart (`MarketPrice.tsx`, `MarketDialog.tsx`):**
  - Observe `.market-cell` visibility with `IntersectionObserver` (`rootMargin: '100px'`) so market order requests only fire for visible rows unless sorting or filtering by price triggers `useMarketPrefetch`.
  - Quote the median of up to the five cheapest distinct online sellers (`quoteMarketOrders` in [`market.ts`](../assets/warframe-toolkit/src/lib/market.ts)), flagging thin markets (`sample < 3`) and excluding offline listings.
  - Clicking a price opens [`MarketDialog.tsx`](../assets/warframe-toolkit/src/components/MarketDialog.tsx), which combines a 90-day / 48-hour dual-band SVG price chart (median line, min–max band, volume bars, keyboard scrubbing with `←` / `→` / `Home` / `End`, pin-to-compare with `Enter` / `Space`, and `+` / `-` zoom) with top buy/sell order books.

## 2. Crafting Planner and Batch Quantity Slider (`Crafting.tsx`, `QuantitySlider.tsx`)

Demonstrated on `#crafting` ([`Crafting.tsx`](../assets/warframe-toolkit/src/components/Crafting.tsx), [`QuantitySlider.tsx`](../assets/warframe-toolkit/src/components/QuantitySlider.tsx), [`crafting.ts`](../assets/warframe-toolkit/src/lib/crafting.ts)):

- **Closable plan tabs + item picker:** Display up to 25 open crafting plans in an `aria-label="Crafting plans"` tablist with roving `tabIndex`, `ArrowLeft` / `ArrowRight` / `Home` / `End` navigation, and an adjacent `.craft-plan-close` button per tab. When no plan tab is selected, show the searchable `ItemPicker` grid with featured blueprints and category filtering.
- **Recursive dependency tree (`buildCraftingTree`):**
  - Distinguish buildable sub-blueprints (`.craft-node.is-build`, showing build time, credit cost, and batch count) from leaf materials (`.craft-node.is-material`). Flag equipment consumed as an ingredient with `.craft-consumed` (`Consumes equipment`).
  - Group completed child ingredients into a compact `.craft-completed-group` list at the bottom of each branch, preserving focus (`data-node-key`) when checking or unchecking an ingredient moves it between active and completed lists.
- **Dual-column aggregation sidebar:** Alongside the ingredient tree, compute remaining leaf materials (`.craft-remaining`) and deduplicated farming locations (`.craft-areas`) in real time as nodes are partially or fullyprogressed.
- **Batch-stepping `QuantitySlider` (`QuantitySlider.tsx`):**
  - Combine quick `-` / `+` buttons (which step by `batchSize` when a blueprint produces multiple items per build, e.g. 10× or 20×), a scrubbable range input (`--range-progress`), and a click-to-edit numeric readout (`input.quantity-number`) within a single control.

## 3. Drop Tables Explorer and Odds Calculator (`DropTables.tsx`, `DropDetails.tsx`)

Demonstrated on `#droptables` ([`DropTables.tsx`](../assets/warframe-toolkit/src/components/DropTables.tsx), [`DropBrowser.tsx`](../assets/warframe-toolkit/src/components/DropBrowser.tsx), [`DropDetails.tsx`](../assets/warframe-toolkit/src/components/DropDetails.tsx)):

- **Six browse modes (`DropModePicker`):** Let users pivot the same normalized dataset by `mission`, `item`, `location`, `relic`, `bounty`, or `source` (`Enemy / source`), transitioning from mode picker → grouped summary cards (`DropGroupCard`) → reward cards (`DropRewardCards`) or dense table rows (`DropTableView`).
- **Structured query tokens + faceted filters:** Parse inline search prefixes (`item:`, `reward:`, `source:`, `from:`, `planet:`, `location:`, `mission:`, `type:`, `relic:`, `era:`, `rot:`, `stage:`, `rarity:`, `cat:`, `chance>=`, `chance<=`) alongside multi-select faceted filter popovers (`Categories`, `Locations`, `Mission types`, `Rotations`, `Rarities`, `Relic eras`, and minimum/maximum chance bounds).
- **Honest probability display and odds calculator (`DropDetails.tsx`):**
  - Display both the within-table rate (`tableChance`) and the table roll rate (`sourceChance`). Never infer per-attempt odds when a source reports a roll rate above 100% (`chance === null`, shown as `Multiple rolls`).
  - In the `Odds calculator` section, compute cumulative probability after $N$ independent attempts ($1 - (1 - p)^N$) and required attempts for a target probability ($\lceil \ln(1 - T) / \ln(1 - p) \rceil$), with one-click milestone presets for `50%`, `90%`, `95%`, and `99%`.

## 4. Modding Calculator and Arsenal Workbench (`ArsenalOverview.tsx`, `Modder.tsx`)

Demonstrated on `#modder` ([`ArsenalOverview.tsx`](../assets/warframe-toolkit/src/components/ArsenalOverview.tsx), [`Modder.tsx`](../assets/warframe-toolkit/src/components/Modder.tsx), [`ModderVisuals.tsx`](../assets/warframe-toolkit/src/components/ModderVisuals.tsx), [`ModderLibrary.tsx`](../assets/warframe-toolkit/src/components/ModderLibrary.tsx)):

- **Two-stage master–detail flow:**
  1. **Arsenal Overview (`view === 'arsenal'`):** Three-column layout with the equipment slot switcher (`Warframe`, `Primary`, `Secondary`, `Melee`) and loadout list on the left, full-height portrait preview in the center, and ability icons + grouped stat readouts (`ArsenalWeaponStats` with wiki reference dots) on the right.
  2. **Upgrades Workbench (`view === 'upgrades'`):** Left capacity/stats panel (`.modder-arsenal-panel`), center 8-slot mod grid + Aura/Stance + Exilus + 2 Arcane slots (`.modder-editor`), inline searchable mod/arcane picker (`ModderLibrary`), slot inspector (`UpgradeControls`), and build/condition settings (`Conditions`, `Base stats & attack`).
- **Mod slot interactions (`Modder.tsx`):**
  - Support HTML5 drag-and-drop (`application/x-warframe-upgrade`) both from the inline `ModderLibrary` into slots (`copy`) and between equipped slots (`move`/swap), highlighting valid drop targets (`.is-drop-allowed`, `.is-drop-target`) via `placeUpgrade` validation (checking compatibility, mutual exclusion such as `Serration` vs `Amalgam Serration`, and slot kind).
  - Support right-click (`onContextMenu`) to clear an equipped slot, click on the card face to replace via picker, and click on the lower `.modder-slot-adjust` bar to inspect/adjust rank, active stacks, polarity (`madurai`, `vazarin`, `naramon`, `zenurik`, `umbra`, `universal`), and inclusion toggle.
- **Transparent calculation breakdown (`Results` in `Modder.tsx`):**
  - Render headline metrics, per-projectile damage distribution `<meter>` bars, and an expandable `<details>` list (`How it's calculated`) showing the exact formula (`<code>`), prose explanation, contributing mod sources, and live +/- delta badges (`.modder-delta`) when comparing against another saved Arsenal configuration.

## 5. Screenshot OCR Import and Affirmative Merge (`ScreenshotImport.tsx`)

Demonstrated on `#import` ([`ScreenshotImport.tsx`](../assets/warframe-toolkit/src/components/ScreenshotImport.tsx), [`RelicScreenshotReview.tsx`](../assets/warframe-toolkit/src/components/RelicScreenshotReview.tsx), [`matching.ts`](../assets/warframe-toolkit/src/lib/matching.ts)):

- **Stationary-divider dual paste stage:** In `Equipment` mode, display `Inventory` and `Codex` drop zones side by side. Track pointer position across the stage's midpoint (`followPointer`) to set the active `Ctrl+V` paste target (`.is-paste-target`) while keeping the hit boundary stationary even as panels animate. When a batch starts in one source, transition the unused source out (`.is-exiting`) so the active drop zone and review table share the full workspace.
- **Review before commit (`review-table`):** Never mutate the user's inventory directly from raw OCR output. Present matched rows with an inline catalog `<Select>` override, raw OCR text readout (`Read: ...`), a `Check match` caution badge when fuzzy or low-confidence (`!exact || confidence < 65`), and editable `Owned`, `Mastered`, and `Rank` fields.
- **Affirmative merge semantics (`mergeObservations` in `model.ts`):** Merging screenshot detections only sets `owned` or `mastered` to `true` and updates `rank` when a rank was read; an absent checkmark in a screenshot never clears previously recorded ownership or mastery.
