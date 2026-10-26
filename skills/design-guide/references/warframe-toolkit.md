# Warframe Toolkit / Grakata

Use [KianBahasadri/warframe-toolkit](https://github.com/KianBahasadri/warframe-toolkit) as an external example of a React + TypeScript application combining dense data, several related views, and interactive planning tools.

## Where to find it

- **Current source:** Browse the repository's default branch, starting with its [README](https://github.com/KianBahasadri/warframe-toolkit#readme) for current setup, preview instructions, and workflow limitations.
- **Local checkout, if present:** `~/warframe-toolkit/`. Source paths below are relative to that repository, not this skill.
- **Interface source:** [src/](https://github.com/KianBahasadri/warframe-toolkit/tree/main/src), especially `App.tsx`, `styles.css`, `components/`, and `lib/`.
- **Interaction checks:** [tests/](https://github.com/KianBahasadri/warframe-toolkit/tree/main/tests), including browser checks for tables, column controls, and screenshot review.

## What to look for

| Area | Source starting points | Patterns to examine |
| --- | --- | --- |
| Application shell and navigation | `src/App.tsx`; `src/components/CraftingNav.tsx`, `Commands.tsx` | Grouping related destinations, separating global utilities, contextual commands, keyboard shortcuts, submenu focus, and keeping navigation usable across views. |
| Collection tables | `src/components/CollectionTable.tsx`, `InventoryTable.tsx`, `Relics.tsx`, `ColumnPicker.tsx`, `ColumnMenu.tsx`, `TableNotes.tsx` | Reusing one table shell across schemas; column visibility, ordering, sorting, and filtering; keyboard alternatives to dragging; row selection; inline edits; and containing overflow at narrow widths. |
| Market exploration | `src/components/MarketPrice.tsx`, `MarketDialog.tsx`, `MarketChart.tsx`; market helpers in `src/lib/` | Loading data as needed, communicating missing or stale quotes, moving from a cell into a detailed chart, keyboard chart inspection, and keeping chart summaries and underlying data available together. |
| Crafting plans | `src/components/Crafting.tsx`, `CraftingNav.tsx`, `CraftingDetails.tsx`, `QuantitySlider.tsx`; crafting helpers in `src/lib/` | Closable plan tabs, item selection, recursive dependencies, focus when completed items move, quantity scrubbing with typed entry, batch-aware stepping, and summaries that follow progress. |
| Faceted exploration | `src/components/DropTables.tsx`, `DropBrowser.tsx`, `DropTableControls.tsx`, `DropDetails.tsx`; drop helpers in `src/lib/` | Several browse modes over one dataset, query tokens alongside filters, summary-to-detail transitions, and probability explanations that keep assumptions and unavailable values visible. |
| Arsenal and modding workbench | `src/components/ArsenalOverview.tsx`, `ArsenalWeaponStats.tsx`, `Modder.tsx`, `ModderLibrary.tsx`, `ModderVisuals.tsx`; modder helpers in `src/lib/` | Moving from overview to editor without losing work, responsive panel composition, compatible drag targets with picker/keyboard alternatives, slot adjustment controls, and calculation breakdowns beside headline results. |
| Screenshot import and review | `src/components/ScreenshotImport.tsx`, `RelicScreenshotReview.tsx`, `ScreenshotGallery.tsx`; `src/lib/matching.ts`, `relicMatching.ts`, `model.ts` | Paste/drop targeting, image preview, uncertain-match feedback, editable review before committing, explicit handling of conflicting readings, and preserving existing records when evidence is absent. |

For shared React interaction primitives, inspect `src/components/Dialog.tsx`, `Select.tsx`, `TablePopover.tsx`, and `Toasts.tsx` together with their callers. Trace keyboard operation, focus restoration, dismissal, and feedback through the complete interaction.

## Related renderer source

For the layered mod-card appearance, also consult [Reuzehagel/arsenyx](https://github.com/Reuzehagel/arsenyx). Search its current source for `mod-card-frame`, `mod-card`, and `mod-card-config` under `apps/web/`: examine frame layers, artwork cropping, rarity treatment, drain backers, and rank markers. Grakata's [modding source notes](https://github.com/KianBahasadri/warframe-toolkit/blob/main/public/images/modding/SOURCES.md) describe its adaptation and asset provenance; consult the current upstream licenses before reusing implementation or artwork.

## Applying the example

Choose the workflow relevant to the task, inspect its current source and behavior, and adapt the useful interaction pattern to the target product. Read current code and tests for exact timings, dimensions, data contracts, and calculation coverage rather than treating this guide as a frozen implementation specification.

Shared tokens, typography, control geometry, and overlay rules remain in [Foundations](foundations.md), [Controls and Forms](controls-and-forms.md), [Data Display](data-display.md), and [Overlays and Feedback](overlays-and-feedback.md).
