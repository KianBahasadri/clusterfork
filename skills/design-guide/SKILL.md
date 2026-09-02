---
name: design-guide
description: Concrete component specifications, design tokens, and core design principles for building clean, accessible, high-craft GUIs across web, desktop, and mobile.
metadata:
  short-description: UI component specifications and design tokens
---

# Design Guide & Component Specifications

Build interfaces with quiet defaults, direct manipulation, and progressive mastery. Rather than applying decorative treatments across an entire surface, design interfaces from consistent structural tokens and concrete, accessible component rules.

## Core Principles & Authority

1. **Color Exclusively Conveys Information (`[INV]`):** Colors (hues) are strictly reserved for communicating meaning, state, and data. They must **never** be used for decoration, aesthetic flair, brand styling, or general interface chrome. The entire structural foundation—canvas, cards, text, dividers, borders, nominal buttons, tabs, selection controls, and focus indicators—is strictly neutral and monochromatic.
2. **Non-Color Meaning (`[REQ]`):** Color must never be the sole carrier of status or action (WCAG 2.2 SC 1.4.1). Whenever a color communicates an informational state (such as an alert, success, or error), it must be accompanied by text, shape, iconography, or position.
3. **Quiet Nominal Default (`[SIG]`):** Healthy, routine state should look settled and neutral. Reserve brightness, saturation, and motion for changes, active user focus, or genuine exceptions.
4. **Truthful State (`[INV]`):** Displayed values, capability, and progress must reflect the real system. Never show unverified external success or collapse simulated/derived data into observed ground truth.
5. **Platform Conventions (`[CONV]`):** Default to native controls, selection, scrolling, focus order, and keyboard shortcuts. Replace them only when the replacement preserves expected behavior and adds measurable capability.

---

## Design Tokens

### Monochromatic Structural Tokens (Chrome, Structure & Typography)
Surfaces, borders, typography, and nominal controls use exclusively monochromatic tokens:

* `--canvas`: Base viewport background (`#080b0d` dark / `#ecefec` light).
* `--surface`: Content containers, panels, cards (`#11171b` dark / `#f7f8f5` light).
* `--surface-raised`: Elevated overlays, dialogs, popovers, dropdowns (`#151d21` dark / `#ffffff` light).
* `--ink`: Standard body text, prose, data values (`#dbe3e6` dark / `#172027` light).
* `--ink-strong`: High-contrast headings, primary metrics, active labels, primary action fills (`#f3f6f5` dark / `#0a0d0f` light).
* `--muted`: Secondary metadata, captions, timestamps, unit labels (`#849094` dark / `#536168` light).
* `--faint`: Tertiary borders, inactive icons, placeholder hints (`#566166` dark / `#778287` light).
* `--line`: Standard structural borders, table row dividers (`#263137` dark / `#c8d1d0` light).
* `--line-strong`: High-contrast dividers, active borders, active tab underlines (`#3a4a51` dark / `#9dacab` light).
* `--focus`: High-contrast keyboard navigation focus ring (`#9cc8ff` dark / `#005fcc` light).

### Informational Color Tokens (Strictly Reserved for Meaning & State)
Hues are applied **only** when directly communicating system state, errors, or discrete data series:

* `--good`: Confirmed success, healthy state (`#79c99e` dark / `#1d6846` light).
* `--caution`: Degraded condition, warning, impending threshold (`#d6ad63` dark / `#805600` light).
* `--danger`: Critical error, destructive action, hard failure (`#df7e78` dark / `#9a332f` light).
* `--derived`: Estimated, modeled, or synthetic data distinguished from ground truth (`#b9aaef` dark / `#63559b` light).
* *Rule:* Never apply these colors to nominal buttons, background headers, navigational chrome, or decorative accents.

---

## Component Specifications

### 1. Buttons & Action Triggers
* **Variants:**
  * *Primary:* Solid neutral high-contrast fill (`--ink-strong`) with inverted text (`--canvas`). Never a colored accent button. Use once per primary action on a surface.
  * *Secondary / Outline:* Transparent fill, 1px `--line` border, `--ink` text. On hover: `--surface-raised` background and `--line-strong` border.
  * *Quiet / Ghost:* Transparent fill, no border, `--muted` text. On hover: `--ink-strong` text and subtle neutral background tint.
  * *Danger:* Red fill or border (`--danger`). The **only** colored button variant, permitted because it conveys critical risk and destructive consequences.
  * *Icon Button:* Square/circular target (minimum 36×36px visual, 44×44px touch area) with neutral border and icon. Must have an accessible label via `aria-label` or `title` plus a tooltip.
* **States:**
  * *Hover:* Shift background brightness by 8–12% neutrally.
  * *Active:* Slight downscale (`transform: scale(0.98)`).
  * *Focus-Visible:* 2px solid `--focus` ring with 2px offset.
  * *Disabled:* `opacity: 0.45`, `cursor: not-allowed`, `aria-disabled="true"`.
  * *Loading / Busy:* Retain fixed button width to prevent layout jump; show inline neutral spinner; set `aria-busy="true"` and disable repeated clicks.

### 2. Dropdowns & Menus
* **Trigger:**
  * Button displaying current selected value with a right-aligned chevron icon (`▾` or SVG). Neutral background and border.
  * Set `aria-haspopup="listbox"` (or `"menu"`) and `aria-expanded="true/false"`.
* **Menu Surface:**
  * Elevated container (`--surface-raised`), 1px `--line-strong` border, neutral box shadow.
  * Constrained height (`max-height: 280px; overflow-y: auto`).
  * Options: 32–36px row height, comfortable padding (8px 12px). Selected option indicated by a checkmark (`✓`) and bold `--ink-strong`, not a colored fill.
* **Keyboard & Dismissal:**
  * `Down Arrow` opens menu and focuses first option.
  * `Up/Down Arrows` navigate options; `Enter` or `Space` selects; `Escape` closes and restores focus to trigger.
  * Dismisses on click outside or viewport blur.

### 3. Form Inputs & Textareas
* **Structure & Labeling:**
  * Always pair inputs with a visible `<label for="id">`. Never use placeholder text as a substitute for a label.
  * Optional help text placed below label or input, linked via `aria-describedby`.
* **Field Styling:**
  * Background: `--surface` or `--canvas`.
  * Border: 1px solid `--line`, border-radius 4–6px, padding 8px 12px.
  * Font: Proportional `--ui` for text, `--mono` for code/tokens/API keys.
* **States:**
  * *Hover:* Border becomes `--line-strong`.
  * *Focus:* Border becomes `--ink-strong` with 2px `--focus` ring.
  * *Invalid / Error:* Border becomes `--danger`. Render an inline error message beneath the field with `role="alert"`, an error icon, and clear instructions to fix the error. This is where color is permitted, because it conveys an error state.
  * *Search / Clearable:* Magnifier icon on left; clear ("×") button on right appearing only when input has a value.

### 4. Selection Controls (Checkboxes, Radios, Switches)
* **Checkboxes:**
  * 16×16px or 18×18px square, 3px border radius.
  * Checked: Solid `--ink-strong` fill with contrasting inverted checkmark (`--canvas`). Supports indeterminate dash state. No decorative color.
* **Radio Buttons:**
  * 16×16px or 18×18px circle. Checked: Outer border and centered solid dot in `--ink-strong`.
  * Always group related radio buttons inside `<fieldset>` with `<legend>`.
* **Toggle Switches:**
  * Pill track (36×20px), sliding circle thumb (16×16px).
  * State transition: Track shifts from neutral `--line` (off) to neutral `--ink-strong` (on).
  * Set `role="switch"` and `aria-checked="true/false"`.
* **Target & Label:** Clicking label toggles the control. Minimum 44px clickable touch row.

### 5. Labels, Badges, Tags & Metadata
* **Form Labels:** 13–14px, weight 500/600, `--ink` or `--ink-strong`. Mark required fields with text `(required)` or an accessible symbol with `aria-hidden="true"`.
* **Status Badges / Pills (Informational Color):**
  * Used **specifically** to communicate health, condition, and telemetry state.
  * Compact padding (2px 8px), border-radius 4px or 999px (pill).
  * Composed of a soft background tint + crisp text + 6px indicator dot.
  * *Nominal:* Neutral tint, `--muted` text.
  * *Good:* `--good-soft` tint, `--good` text.
  * *Caution:* `--caution-soft` tint, `--caution` text.
  * *Danger:* `--danger-soft` tint, `--danger` text.
  * *Derived:* `--derived-soft` tint, `--derived` text.
  * *Never use a bare colored dot without text.*
* **Metadata Tags & Keyboard Shortcuts:** Non-status tags, commit hashes, versions, and keyboard hints (`<kbd>`) are strictly neutral (`--surface-raised`, `--line`, `--muted`).

### 6. Tables & Data Grids
* **Alignment Rules:**
  * Text and descriptions: Left-aligned.
  * Numbers, timestamps, metrics, currency: Right-aligned with tabular figures (`font-variant-numeric: tabular-nums`).
  * Status badges: Centered or left-aligned with headers.
  * Action menus / buttons: Right-aligned in final column.
* **Headers & Borders:**
  * Neutral sticky headers (`position: sticky; top: 0; background: var(--surface)`).
  * 1px bottom border `--line` per row. Neutral row hover highlight (`--surface-raised`).
  * Color appears **only** inside status badges or threshold-breaching values (e.g. error rate in `--danger`).
* **Empty & Loading States:**
  * Empty table displays an explicit neutral message explaining why no records exist and an action button to reset filters.

### 7. Charts & Data Graphics (Informational Color)
* **Rendering & Axes:**
  * Clean vector SVG with neutral gridlines (`--line`) and neutral axis typography (`--muted`).
  * Explicit units (e.g., "ms", "req/s", "%") on axes.
* **Series Differentiation:**
  * Color is permitted exclusively to differentiate discrete data series.
  * Series colors must be paired with stroke patterns (solid, dashed, dotted) or markers so non-color users can distinguish series.
* **Inspection & Fallback:**
  * Hover crosshair with floating neutral tooltip displaying the exact date/time and values.
  * Mandatory accessible fallback: an expandable `<details>` table displaying the exact plotted values.

### 8. Dialogs & Modals
* **Container & Backdrop:** Neutral surface (`--surface-raised`), 1px `--line-strong` border, dimmed neutral backdrop (`rgba(0,0,0,0.65)`).
* **Focus Management:**
  * Move focus to first interactive element on open.
  * Trap focus within dialog while active; `Escape` closes dialog; return focus to trigger on close.
* **Actions Footer:**
  * Cancel (quiet / secondary neutral) aligned left.
  * Primary confirm on right. Destructive actions use Danger button (`--danger`).

### 9. Tooltips & Popovers
* **Tooltips:**
  * Purely informative, neutral text hints for icon buttons or truncated strings.
  * Triggered on hover and keyboard focus after short delay (~150ms).
  * *Never contain interactive controls or links.*
* **Popovers:** Neutral floating card for interactive options (filters, menus).

### 10. Navigation, Tabs & Breadcrumbs
* **Tabs:**
  * Container has `role="tablist"`, each tab has `role="tab"` and `aria-selected="true|false"`.
  * Active tab has strong text (`--ink-strong`) and an active neutral underline (`--ink-strong` or `--line-strong`). No colored accent line.
  * Supports keyboard arrow navigation (`Left`/`Right` arrow keys cycle active tabs).
* **Breadcrumbs:**
  * Neutral `<nav aria-label="Breadcrumb">` with `<ol>`, neutral separators (`/`), and current page marked with `aria-current="page"`.

### 11. Cards & Panels
* **Container:** Purely neutral `--surface` background, 1px `--line` border, 6px border-radius, subtle neutral shadow.
* **Header & Action:** Bold neutral title, optional subtitle, right-aligned action.

### 12. Alerts, Callouts & Toasts (Informational Color)
* **Inline Callouts:**
  * Permitted to use status colors because they communicate abnormal or consequential conditions:
    * *Info / System:* Neutral or faint tint.
    * *Caution:* `--caution` icon, border, and text.
    * *Danger / Outage:* `--danger` icon, border, and text.
    * *Success:* `--good` icon, border, and text.
* **Toast Notifications:**
  * Neutral floating container (`--surface-raised`, `--line-strong`) with status icon, operation message, and neutral "Undo" action button.
  * Auto-dismisses after 4–6 seconds; pauses countdown when hovered or focused.
  * Announced via `role="status"` or `aria-live="polite"`.

---

## Layout & Section Architecture

### Component Sections Must Identify Themselves

* **Include:** One compact heading containing the ordinal and specific component name, such as `01 / Buttons & Actions`.
* **Include:** A semantic heading (`h2` at the top section level) connected to its section with `aria-labelledby`.
* **Include:** The rendered component immediately after the heading, with visible control labels, familiar affordances, and states that identify themselves.
* **Do not include:** A description, subtitle, or summary sentence explaining the component. `[INV]` A viewer must be able to identify it from its visible name, rendered content, and boundary.
* **Do not include:** A generic heading such as `01 / Specification`, a second larger title that repeats the component name, or a horizontal rule beneath the heading.
* **Do not include:** Self-evident showcase taxonomy such as `Variants` or `States: Disabled & Loading`. If those labels seem necessary, strengthen the examples' visible labels and states instead.
* **Boundary:** Use whitespace and the component container to distinguish the section; avoid extra separators or labels that describe the layout rather than the interface being demonstrated.

---

## Reference Asset

Inspect [skills/design-guide/assets/component-reference.html](assets/component-reference.html) for a runnable, interactive catalog implementing every component specified above in dark and light modes.
