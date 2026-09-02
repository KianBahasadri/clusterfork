# Operational and Analytical Interfaces

Use this guide when the dominant task is to monitor, compare, diagnose, decide, create, or repeatedly manipulate evolving system or artifact state. Typical surfaces include dashboards, observability consoles, admin tools, editors, data products, scientific tools, and technical desktop or mobile software.

Do not select it merely because the subject matter is technical. A technical tutorial is editorial; a database launch page is promotional; a settings form inside a console is locally transactional.

## Outcome

Help users determine what the current state is, what changed, how trustworthy the evidence is, and what action is available—without losing scope or context. Optimize real detection, diagnosis, comparison, and action rather than the amount of telemetry visible.

## Scope and State

- `[INV]` Give the surface an explicit scope: entity, environment, time range and time zone, filters, units, denominator, aggregation, and permissions wherever each matters. Distinguish global, regional, and local constraints.
- `[INV]` Every region governed by a scope updates with it or identifies why it does not. A sticky control paired with partially stale results is a false contract.
- `[INV]` Distinguish observed, derived, estimated, forecast, simulated, cached, stale, missing, partial, denied, and unavailable values. Missing is not zero; accepted is not completed.
- `[INV]` Put definitions, sources, freshness, uncertainty, and threshold ownership at the point of interpretation or one direct disclosure away.
- `[SIG]` Let the primary view answer the recurring operational questions. Move infrequent configuration and deep provenance behind stable, visible entries instead of presenting every capability at once.

## Attention, Density, and Layout

- `[SIG]` Keep nominal state quiet enough that a meaningful change can emerge. Allocate salience by consequence, urgency, confidence, and required action—not by component type or visual novelty.
- `[SIG]` Build density from stable grouping, alignment, concise labels, shared scales, and restrained chrome. Avoid creating a separate padded card for every value.
- `[SIG]` Give persistent elements a recurring role in decision, orientation, access, coordination, or intentional identity; avoid persistent vanity metrics and context-free counts.
- `[CONV]` Keep recurring regions and comparable values spatially stable. Preserve active filters, selections, time ranges, zoom, and drill state when users move between related views.
- `[SIG]` Use peripheral space for persistent context when the viewport supports it and contextual surfaces when it does not.
- `[INV]` A responsive version may reprioritize or sequence capability, but it preserves the user's path to required information and action.

## Tables, Charts, and Visual Encoding

- `[EVID DATA-1]` When accurate magnitude comparison is central, start with position on a common scale or length. Use angle, area, volume, and color intensity only when their lower precision fits the task, and expose exact values when they matter.
- `[CONV]` Use charts for pattern, relationship, distribution, and change; use tables or lists for exact lookup and comparison. Combine them only when each supports a distinct task.
- `[INV]` Label the quantity actually encoded, including units, denominator, aggregation, transform, baseline, and whether an axis or scale changes.
- `[EVID UNC-1]` Name the uncertainty quantity and the decision it informs. A confidence interval, outcome range, forecast band, and model confidence are not interchangeable.
- `[REQ A11Y-1]` Give consequential color a redundant text, shape, position, pattern, or value channel when the applicable non-color criterion is in scope.
- `[INV]` Critical facts have a retrieval path that does not depend on hover, pointer precision, vision, or animation alone.
- `[CONV WEB-1]` On the web, use native table semantics for tabular data. Use an interactive grid only when cell-level interaction warrants the complete grid keyboard and focus model.
- `[EVID DATA-2]` Consider an animated transition when users need to track correspondence between related statistical views; this evidence does not generalize to decorative interface motion.
- `[CONV]` Preserve a static change cue and a reduced-motion path.

## Interaction and Action

- `[EVID DM-1]` For manipulable domain objects, prefer incremental operations with immediate visible effects and a practical undo, cancel, or restore path.
- `[SIG]` Keep active constraints visible while users filter or explore. Update tightly coupled results promptly; when computation or consequence makes that inappropriate, provide an explicit Apply operation and describe its scope or cost.
- `[CONV]` Support expert speed where repeated work justifies it: keyboard commands, batch selection, saved views, history, comparison, and addressable state. Avoid inventing these features merely to make the interface look powerful.
- `[INV]` Before a consequential command, identify the target, environment, scope, and expected effect. Afterwards, report confirmed, partial, pending, failed, and recovered outcomes truthfully with the responsible actor or process.
- `[INV]` Preserve user-authored input and analytical state across failure where security and data integrity permit it. Make retry and duplicate-invocation behavior explicit.

## Artifact and Editor State

- `[INV]` Expose the active artifact, version or branch, selection, tool or mode, and dirty, saving, saved, synced, published, conflicted, or failed state wherever each affects the next action.
- `[INV]` Selection and action scope agree across canvas, outline, inspector, command surface, and preview. Hidden or mixed selection cannot silently receive an operation presented as local.
- `[INV]` Undo, redo, autosave, history, recovery, and external side effects reflect real operation boundaries; a local save indicator cannot imply an unconfirmed remote sync or publication.
- `[CONV]` Preserve viewport, selection, active tool, panel state, and unsaved work across navigation and restart where the domain and security model permit it.

## Alerts and Automation

- `[SIG]` Protect high-salience alert treatments. Reserve amber and red for degraded or harmful conditions in the local vocabulary; avoid coloring every nominal value green.
- `[EVID OPS-1 OPS-2]` Transfer control-room principles only when the underlying monitoring, consequence, training, and interruption problem is analogous. These sources do not support copying aerospace or nuclear colors, flash rates, thresholds, navigation placement, or depth limits into ordinary software by default.
- `[INV]` An alert identifies the condition, affected scope, time, consequence, acknowledgement state, responsible owner, and available response to the degree the system knows them.
- `[EVID AUTO-1 AUTO-2]` Calibrate reliance rather than maximizing trust. Expose relevant automation purpose, inputs, current responsibility, capability limits, reliability or validation basis, and intervention path at the depth needed for the decision.
- `[INV]` Confidence, freshness, uncertainty, availability, permission, and simulation status are separate facts. Never collapse them into one colored badge or a generic “AI confidence” signal.
- `[HYP]` Alert thresholds, ranking, update cadence, sound, haptics, adaptive disclosure, and explanation depth are local hypotheses. Test missed conditions, false positives, acknowledgement, interruption cost, recovery, and reliance—not noticeability alone.

## Visual Translation

- `[SIG]` A dark cockpit is an optional signature realization for sustained monitoring or dim operational environments, not a universal dark-theme mandate. Keep a considered light and high-contrast translation when the platform and scope support them.
- `[SIG]` Start with neutral structure, a restrained interaction accent, and protected status families. Product vocabulary, brand, platform, and tested domain meanings outrank the signature palette.
- `[EVID ICON-1 ICON-2]` Use familiar or tested symbols and label unfamiliar, rare, or consequential icon actions.
- `[SIG]` When a set must be scanned quickly, start with visually distinct silhouettes, but do not treat shape alone as proof of meaning.
- `[SIG]` Prefer discriminable control typography, tabular figures for aligned numerical comparison, and a text face appropriate to explanations. Test confusable glyphs, dense rendering, and actual user scaling.

## Optional Worked Artifact

`../assets/dashboard-reference.html` is a runnable fictional operations dashboard showing explicit scope, mixed source freshness, chart and table equivalence, direct inspection, reversible acknowledgement, and a bounded rollback exercise. It is neither normative nor a template. Inspect it only when modifying or validating that artifact, or when the user explicitly asks to borrow a demonstrated interaction. Do not copy its domain, palette, thresholds, fixture data, or feature breadth by default.

## Anti-Patterns

- A wall of equally prominent KPI cards or every healthy value rendered as a success signal.
- Hidden scope, mixed time zones or denominators, and panels that silently ignore a global filter.
- Missing data shown as zero, cached data shown as live, or optimistic completion shown as confirmed success.
- Pies, gauges, decorative 3D volume, or color intensity used for comparisons that require precision.
- Unlabeled scale changes, truncated axes that imply a false effect, or uncertainty decoration with no defined quantity.
- Alert colors or timing imported from an unrelated safety-critical standard.
- Hover-only inspection, inaccessible canvas data, or a grid role without the complete interaction model.
- Continuous pulsing for routine refreshes and motion that makes stable structure feel unstable.
- Destructive commands mixed with exploratory controls without a clear target, consequence, separation, or recovery path.

## Validation

- Can representative users detect the important condition, identify likely causes, compare alternatives, and take the correct action? Measure accuracy and decision time rather than widget discovery alone.
- Can they state the active entity, environment, time range, time zone, filters, units, freshness, source, missingness, and uncertainty?
- What happens when one source is stale, partial, denied, or failed while other regions remain current?
- Does a restored or shared view reproduce hidden filters, selections, aggregation, and scope—not merely the visible URL?
- Do numerical judgments meet the task's accuracy requirement across typical, sparse, extreme, negative, and missing values?
- Can keyboard, screen-reader, magnification, touch, and pointer users complete the same detection-to-action path?
- Does the layout survive maximum rows, long names, localization, outliers, narrow viewports, zoom, and user-selected density?
- For alerts, measure misses, false positives, acknowledgement time, repeated exposure, interruption, and recovery.
- For commands, test partial completion, permission change, cancellation, duplicate activation, retry, and rollback failure.
- For authoring, test crash recovery, stale and concurrent versions, merge or conflict handling, failed autosave or sync, undo boundaries, and reopening the exact working context.
