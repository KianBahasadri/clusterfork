# Arsenal interface references and symbols

The upgrade layout follows the user's English 1920 × 1200 Arsenal / Nekros screenshots, supplemented by the Arsenal screenshot in Digital Extremes' [Mods Guide](https://www.warframe.com/en/news/mods-guide) and the [Mod Guide](https://support.warframe.com/hc/en-us/articles/200500194-Mod-Guide-Use-Fusion-Transmutation-Sale). References checked October 13, 2026. The supplied screenshots are visual references only and are not distributed with the application.

Additional reference captures used for the Arsenal-first revision:

- [Digital Extremes' Mods Guide: Mag](https://www-static.warframe.com/uploads/f6c57fbeacaab674fa7fe69fce2799d3.jpg).
- [Digital Extremes' Update 36: Excalibur](https://www-static.warframe.com/uploads/85de425bb808c11fc3a1ab1550323205.jpg), from the [official update notes](https://forums.warframe.com/topic/1400700-actualizaci%C3%B3n-360-sombras-de-jade/).
- [Dante Arsenal screenshot](https://i.imgur.com/wu2oyno.png) from [this Warframe forum report](https://forums.warframe.com/topic/1450677-new-item-icon-ie-the-exclamation-mark-does-not-go-away-after-equipment-has-been-equipped/): equipment rows, selected outline, floating Swap/Upgrade actions.
- [Argonak Upgrade screenshot](https://i.imgur.com/sc7DSDV.png) from [this Warframe forum discussion](https://forums.warframe.com/topic/1425113-could-the-modify-parazon-button-not-teleport-you-to-the-arsenal-and-instead-behave-like-the-upgrade-buttons-on-the-navigation-screen/): firearm Exilus placement and bottom actions.
- [Excalibur Umbra upgrade capture](https://images.steamusercontent.com/ugc/784126725482876938/CC50DBF9C083C2E140A5B02D78707F6DDD14D413/) from [this player guide](https://steamcommunity.com/sharedfiles/filedetails/?id=2001116161): two-row tray and mod ornaments.
- The user's Atomos upgrade screenshot (October 14, 2026): weapon attribute rows, elemental icons/recipes, Riven disposition, metallic arcane surround/rank diamonds, and a full-size firearm Exilus beneath the arcane. Its exact damage values and Incarnon bonuses are not copied into the calculator.
- Digital Extremes' [Update 35.5 notes, “Arsenal Stat Changes”](https://www.warframe.com/en/patch-notes/pc/35-5-0): utility and damage row order, expandable magazine/accuracy details, conditional noise/punch-through rows, and additional attack sections.

The layout uses gold controls on charcoal/plum surfaces, an upper Aura/Exilus row, eight slots in two rows, arcanes at the right, a left capacity/stats panel, and a two-row horizontal mod tray on desktop. The separate Arsenal entry shows equipment rows, Swap/Upgrade actions, a static equipment render, descriptions, and stats. The Upgrade screen has no equipment backdrop. The interface retains the calculator's existing model and whole-arsenal saves; it does not claim in-game configuration synchronization, equipment ownership/rank, or a live 3D game renderer.

## Open-source renderer

The compact mod renderer in `src/components/ModderVisuals.tsx` and the matching styles in `src/components/Modder.css` adapt [Arsenyx](https://github.com/Reuzehagel/arsenyx) at revision [`de051e179996b9b39459e86f55036ea7361319c0`](https://github.com/Reuzehagel/arsenyx/tree/de051e179996b9b39459e86f55036ea7361319c0). Copyright (c) 2026 Reuzehagel, used under the [MIT license](../../licenses/arsenyx-MIT.txt).

Adapted upstream files:

- `apps/web/src/components/build-editor/mod-card-frame.tsx`: separate top/bottom frame layers, drain backer, and rank markers.
- `apps/web/src/components/build-editor/mod-card.tsx`: compact artwork crop, tint, and name placement.
- `apps/web/src/lib/mod-card-config.ts`: rarity colors and frame/backer selection, including Amalgam and Galvanized variants.

The original 184 × 64 compact frame with a 32px lower rim is laid out as a responsive 184 × 96 face. The adaptation uses this project's CSS and existing calculation data, without Arsenyx's Tailwind, view transitions, backend, or build schema.

The 24 WebP files in `frames/{common,uncommon,rare,legendary,amalgam,galvanized}/` are unmodified copies of the corresponding `apps/web/public/mod-components/` files at that revision: each rarity's `FrameTop`, `FrameBottom`, `TopRightBacker`, and `RankCompleteLine`. Amalgam and Galvanized use Silver drain backers. Set crests in `../arsenal/*Header.png` are unmodified copies of `apps/web/public/mod-set-icons/` at the same revision. These are Warframe game textures © Digital Extremes; the MIT license covers the upstream implementation and does not transfer ownership of game artwork. They are served locally.

## Other artwork and type

Unmodified polarity textures © Digital Extremes, downloaded from [the game asset index](https://browse.wf/Lotus/Interface/). Each filename below links to its original image.

| Local file | Polarity |
| --- | --- |
| [PolarityTriangle.png](https://browse.wf/Lotus/Interface/PolarityTriangle.png) | Madurai |
| [PolarityPoint.png](https://browse.wf/Lotus/Interface/PolarityPoint.png) | Vazarin |
| [PolarityCircle.png](https://browse.wf/Lotus/Interface/PolarityCircle.png) | Naramon |
| [PolarityMark.png](https://browse.wf/Lotus/Interface/PolarityMark.png) | Zenurik |
| [PolarityWard.png](https://browse.wf/Lotus/Interface/PolarityWard.png) | Unairu |
| [PolarityPrecept.png](https://browse.wf/Lotus/Interface/PolarityPrecept.png) | Penjaga |
| [PolarityUmbra.png](https://browse.wf/Lotus/Interface/PolarityUmbra.png) | Umbra |
| [PolarityUniversal.png](https://browse.wf/Lotus/Interface/PolarityUniversal.png) | Universal |
| [PolarityAura.png](https://browse.wf/Lotus/Interface/PolarityAura.png) | Aura slot |

Mod and arcane illustrations in `../mods/` are unmodified Warframe artwork © Digital Extremes. `npm run catalog:modder` obtains their rarity and artwork from the WFCD revision pinned in [modder.json](../../../src/data/modder.json), downloads each distinct source image from that revision's `data/img/`, and names it by the first 16 hex characters of its filename's SHA-256 hash. All images are served locally; no image service is contacted from the browser.

The title uses [Bebas Neue](https://github.com/google/fonts/tree/main/ofl/bebasneue), licensed under the [SIL Open Font License](../../fonts/OFL.txt), as a condensed display face. This is not the game's proprietary font.

The Arsenal controls and mod names use [Roboto Condensed](https://github.com/google/fonts/tree/main/ofl/robotocondensed), under its [SIL Open Font License](../../fonts/RobotoCondensed-OFL.txt). Both fonts are served locally.

## Arsenal artwork and UI textures

`npm run catalog:arsenal-art` downloads unmodified game artwork into `../arsenal/` and writes `src/data/arsenalArt.json`. The [asset manifest](../arsenal/sources.json) records the exact source URL for every file and the official pages used to resolve ability names. The browser only requests local assets.

- Warframe portraits: the community wiki's `*Full.png` renders, from `wiki.warframe.com/images/` or the legacy Warframe wiki's `static.wikia.nocookie.net/warframe/images/` CDN. Original filename extensions are retained even when the CDN negotiates WebP. Missing portraits fall back to that exact equipment's catalog artwork; no substitute variants are used.
- Ability symbols: original Public Export textures linked by [Digital Extremes' Warframe pages](https://www.warframe.com/en/game/warframes/excalibur). Ability descriptions remain from the existing pinned data snapshot. Unsupported/missing icons keep text labels.
- Category/action symbols, empty mod glass/mask, and empty arcane icon: original textures from [the game asset index](https://browse.wf/Lotus/Interface/), including `Icons/Categories/`, `Icons/StarChartLoadoutSwap/`, and `Graphics/Mods/`. CSS masks tint their existing silhouettes; the glass rim is derived from the original slot mask.
- Equipped arcane surround: original [ArcaneProgressIcon1.png](https://browse.wf/Lotus/Interface/Icons/CosmeticEnhancers/Arcanes/Progression/ArcaneProgressIcon1.png), colored by rarity in CSS. Rank diamonds use [PipDiamond_d.png](https://browse.wf/Lotus/Interface/Icons/PipDiamond_d.png).
- Damage symbols: original colored textures in [StatSymbols](https://browse.wf/Lotus/Interface/Icons/StatSymbols/), including physical, elemental, combined elemental, Void, and Sentient damage; True damage uses `Icons/Damage/Finisher_d.png`. Exact URLs are in the asset manifest. Composite elements display their constituent icons beside the name.

Warframe artwork and UI textures are © Digital Extremes. Community code licenses and font licenses do not transfer rights to those assets. This is an independent fan tool.

## Supplemental weapon attributes

`src/data/arsenalWeaponStats.json` is an adapted subset of WARFRAME Wiki contributors' [primary](https://wiki.warframe.com/w/Module:Weapons/data/primary), [secondary](https://wiki.warframe.com/w/Module:Weapons/data/secondary), and [melee](https://wiki.warframe.com/w/Module:Weapons/data/melee) data tables, under [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/). That license applies to this derived reference dataset. `npm run catalog:arsenal-stats` selects utility fields for exact catalog equipment IDs and exact attack names, converts names and shapes to JSON, and records the pinned revisions, retrieval date, and SHA-256 hashes. The snapshot contains no executable wiki code. Accuracy categories follow the thresholds in the wiki's [Module:Weapons](https://wiki.warframe.com/w/Module:Weapons).

These separately dated base reference values supplement the display only. They do not replace or amend the pinned WFCD damage/calculation data. The UI marks them with dotted underlines, includes attribution under Sources & model coverage, and labels Incarnon sections as excluding evolution bonuses.
