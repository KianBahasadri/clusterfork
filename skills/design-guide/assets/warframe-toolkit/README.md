# Grakata (Warframe Toolkit) — Application Reference

This directory contains a self-contained React + TypeScript application snapshot of **Grakata** (`warframe-toolkit`), paired with a prebuilt browser bundle (`index.html` + `bundle.js` + `bundle.css`) so it can be opened directly via `file://` without a local server or build step.

For the architectural, layout, and component rules demonstrated by this reference, read [`../../references/warframe-toolkit.md`](../../references/warframe-toolkit.md).

## Running & Inspecting

- **Direct browser viewing (no server required):** Open [`index.html`](index.html) directly in a browser. The prebuilt `bundle.js` and `bundle.css` include offline fixture fallbacks for account session state, drop tables, Warframe Market orders/history, and screenshot OCR review.
- **Inspecting React + TypeScript source:** Read `src/App.tsx`, `src/styles.css`, `src/components/`, `src/lib/`, and the closed sample fixtures in `src/data/`.
- **Optional Vite dev server:** Run `npm install && npm run dev` if you want hot module replacement while editing `src/`.

## Workflows Included

1. **Collection Tables (`#weapons`, `#warframes`, `#relics`):**
   - [`src/components/CollectionTable.tsx`](src/components/CollectionTable.tsx), [`src/components/InventoryTable.tsx`](src/components/InventoryTable.tsx), [`src/components/Relics.tsx`](src/components/Relics.tsx), [`src/components/TableNotes.tsx`](src/components/TableNotes.tsx)
   - Multi-column sorting (`Shift`-click priority), drag-and-drop column reordering, column visibility picker, header filter popovers, inline debounced notes, CSV export, equipment details (`EquipmentDetails.tsx` / `RelicDetails.tsx`), and Warframe Market price tooltips + 90-day history chart dialog (`MarketPrice.tsx` / `MarketDialog.tsx`).
2. **Crafting Planner (`#crafting`):**
   - [`src/components/CraftingNav.tsx`](src/components/CraftingNav.tsx), [`src/components/Crafting.tsx`](src/components/Crafting.tsx), [`src/components/CraftingDetails.tsx`](src/components/CraftingDetails.tsx), [`src/components/QuantitySlider.tsx`](src/components/QuantitySlider.tsx)
   - Topbar hover/keyboard plan menu, closable plan tabs (`Boltor`, `Rhino`), searchable item picker, recursive dependency tree with batch-stepping `QuantitySlider`, aggregated remaining materials, and required farming locations.
3. **Drop Tables & Odds Calculator (`#droptables`):**
   - [`src/components/DropTables.tsx`](src/components/DropTables.tsx), [`src/components/DropBrowser.tsx`](src/components/DropBrowser.tsx), [`src/components/DropDetails.tsx`](src/components/DropDetails.tsx), [`src/lib/dropExplorer.ts`](src/lib/dropExplorer.ts)
   - Six browse modes (`mission`, `item`, `location`, `relic`, `bounty`, `source`), structured query tokens (`item:`, `source:`, `planet:`, `mission:`, `rot:`, `rarity:`, `chance>=`), faceted filter popovers, saved views, and a binomial expected-attempts calculator with 50% / 90% / 95% / 99% milestone buttons.
4. **Modding Calculator / Arsenal Workbench (`#modder`):**
   - [`src/components/ArsenalOverview.tsx`](src/components/ArsenalOverview.tsx), [`src/components/ArsenalWeaponStats.tsx`](src/components/ArsenalWeaponStats.tsx), [`src/components/Modder.tsx`](src/components/Modder.tsx), [`src/components/ModderLibrary.tsx`](src/components/ModderLibrary.tsx), [`src/components/ModderVisuals.tsx`](src/components/ModderVisuals.tsx), [`src/lib/modder.ts`](src/lib/modder.ts)
   - Two-stage workflow: 3-column Arsenal equipment overview (`Config A` & `Config B`) and the full Upgrades workbench with an 8-slot mod grid, Aura/Stance, Exilus, and 2 Arcane slots, polarity drain matching, drag-and-drop placement, rank sliders, target/buff conditions, and a live expandable formula breakdown sidebar with build-comparison deltas.
5. **Screenshot OCR Import & Review (`#import`):**
   - [`src/components/ScreenshotImport.tsx`](src/components/ScreenshotImport.tsx), [`src/components/RelicScreenshotReview.tsx`](src/components/RelicScreenshotReview.tsx), [`src/components/ScreenshotHelp.tsx`](src/components/ScreenshotHelp.tsx), [`src/components/ScreenshotGallery.tsx`](src/components/ScreenshotGallery.tsx)
   - Dual-source paste stage (`Inventory` vs `Codex`) that tracks pointer hemisphere while keeping the hit boundary stationary, plus `Relics` mode, lightbox preview gallery, and an affirmative-merge review table (clickable via **Load sample** in offline mode).
