---
name: design-guide
description: Design, implement, review, audit, or revise user-facing GUIs in Kian's evidence-calibrated style by routing each surface to a guide for editorial reading, operational and analytical work, transactional workflows, or promotional and brand communication across web, desktop, and mobile.
metadata:
  short-description: Route GUI work to a situation-specific guide
---

# Situation-Specific GUI Design

Classify the interface before styling it. A product can contain several interface situations; apply a guide to the current surface or region rather than forcing one visual system over the whole product.

## Authority

Resolve design conflicts in this order:

1. Truthful system behavior and applicable safety, accessibility, privacy, and legal requirements.
2. The user's stated outcome and scope, including existing product contracts, brand, design system, and public interfaces.
3. Native platform and input conventions.
4. Findings that transfer to the actual users, task, environment, and consequences.
5. The selected guide's signature defaults.
6. Explicitly identified experiments.

Do not use this skill to expand the assignment. Preserve an established design system unless the user asks to change it. Report adjacent defects instead of fixing them unless they block the requested work.

## Evidence Language

The guides label prescriptions so unlike claims do not acquire equal authority:

- `[REQ]` — an external requirement. A situational-guide tag flags a potentially applicable requirement family; before calling it mandatory or reporting nonconformance, resolve the exact standard, version, criterion, scope, and conformance level.
- `[INV]` — a non-negotiable truth, integrity, or task-correctness constraint derived from the product, content, domain, or selected situation; never merely a visual preference.
- `[EVID]` — an empirical or research-supported heuristic. The tag does not encode evidence strength; inspect source type, convergence, and limits, then apply only within the studied or defensibly transferred scope.
- `[CONV]` — an established platform or domain convention. Depart deliberately and preserve expected operation.
- `[SIG]` — Kian's signature design judgment or default. Use after higher-authority constraints are satisfied.
- `[HYP]` — a hypothesis to validate locally rather than present as settled guidance.

Reserve absolute language such as *must* and *never* for `[REQ]` and `[INV]`. Prefer *start with*, *usually*, *consider*, or *test* for the other classes.

Read [references/evidence.md](references/evidence.md) before asserting `[REQ]` or `[EVID]`, making a consequential tradeoff, or checking whether a finding transfers. A citation supports only the claim and context recorded there; it does not convert adjacent design preferences into evidence.

## Route by the Dominant User Task

- **Read or understand a sustained body of text** — use [references/editorial.md](references/editorial.md) for articles, essays, blogs, documentation, publications, and reading views.
- **Monitor, analyze, compare, or control a system, or repeatedly create or edit domain artifacts in a working tool** — use [references/operational.md](references/operational.md) for dashboards, developer tools, data products, editors, consoles, and technical desktop or mobile software.
- **Complete a structured goal and commit information or value** — use [references/transactional.md](references/transactional.md) for forms, checkout, onboarding, booking, account setup, and stepwise workflows.
- **Understand a proposition, experience a brand, or choose a next step** — use [references/promotional.md](references/promotional.md) for landing pages, launches, portfolios, campaigns, and product marketing.

“Dark,” “minimal,” “dense,” and “flashy” are treatments, not routes. A landing page may be restrained; a reading experience may be expressive; a dashboard may be light. Route by the user's job and attention mode.

For a hybrid product, assign one primary guide per surface or bounded region. A secondary guide's style and workflow defaults are warranted only when a region has a distinct user goal and interaction or state lifecycle; a search field or navigation control alone does not make a page hybrid. Cross-cutting truth, disclosure, safety, and commitment rules still apply wherever relevant. For example, a product home page may be promotional, its documentation editorial, its signup transactional, and its console operational. Apply shared contracts everywhere. When same-authority defaults conflict, the guide for the active local task wins inside its region. Do not average guide defaults into an incoherent compromise. If no guide fits, use the shared contracts, study the actual context, and treat new prescriptions as hypotheses.

Length alone does not make persuasive copy editorial. For advertorial, sponsored, affiliate, or branded editorial, route the reading experience by its dominant task while also applying promotional claim and disclosure constraints and transactional commitment constraints where they govern the same content or action.

Inspect only the situational guides relevant to the task.

## Shared Contracts

- `[INV]` Derive displayed state, progress, capability, and results from the real system. Identify simulated, estimated, forecast, cached, stale, partial, unavailable, or unverified information.
- `[INV]` Activation performs the advertised action. Feedback confirms an operation; a toast, animation, count, or explanation does not substitute for it.
- `[INV]` Distinguish only lifecycle states the system can actually observe, and do not report external success before confirmation.
- `[REQ A11Y-1]` On the web, preserve applicable semantics, reading and focus order, keyboard operation, target access, text scaling, contrast, non-color meaning, and equivalent paths across supported inputs and assistive technologies.
- `[EVID A11Y-2]` For native software, use WCAG2ICT only as translation guidance and follow the platform's actual accessibility requirements and conventions.
- `[CONV]` Prefer native controls, selection, scrolling, navigation, focus, shortcuts, and context menus. Replace them only when the replacement preserves expected paths and adds relevant capability.
- `[SIG]` Spend salience deliberately. Brightness, saturation, scale, contrast, motion, sound, and interruption should reflect importance or intentional identity rather than mere availability.
- `[EVID LEARN-1 NAV-1]` Give the primary path a perceivable, meaningful entry so users can act without first studying the complete system and can predict where deeper paths lead.
- `[SIG]` Layer deeper capability near its point of relevance without making every pixel reactive; treat the particular layering as a design judgment to test.
- `[SIG]` Give visual differences a reason: role, state, hierarchy, relationship, affordance, or intentional identity.
- `[EVID ICON-1 ICON-2]` Label ambiguous, unfamiliar, rare, and consequential icons until intended users demonstrate comprehension.
- `[INV]` Define bounds, overflow, and stacking for persistent regions, expanding content, and overlays. Unrelated readable or interactive surfaces cannot occupy the same space.
- `[CONV]` Honor supported platform and user settings such as zoom, text scaling, high contrast, reduced motion, color preferences, and input alternatives. When an adopted accessibility target governs the behavior, record the exact `[REQ]` criterion.

## Work Modes

### Design or implementation

1. Inspect the existing product, content, state model, design tokens, platform, and requested scope before proposing a visual direction.
2. State the dominant user task, selected guide, important constraints, and any assumptions that affect the design.
3. Apply the shared contracts and the selected situational guide. Add only capabilities required by the task.
4. Validate behavior and presentation in proportion to risk. Use real content and state where available; label fixtures and simulations.

### Review

Stay read-only unless the user also requests changes. For every finding, report the observable issue, reproduction or evidence, affected user or task, severity, evidence class, recommendation, and material unknowns. Mark behavior that cannot be exercised as *unverified*, not broken. Do not grade signature preferences as compliance failures when higher-authority constraints permit another choice.

### Revision

Keep remediation inside the requested surface and dependencies. Prioritize truth and behavioral contracts; applicable access and safety; spatial integrity; semantic clarity; task efficiency; then expression and polish. Preserve intentional redundancy when it supports orientation, comparison, access, or consequential confirmation. Remove superseded behavior rather than layering a second owner for the same state or interaction.

## Validation

Choose checks from the selected guide, then cover the shared risks that apply:

- representative tasks with realistic content, empty and maximum content, waiting, partial, denied, failure, recovery, and repeated use;
- narrow and wide viewports, zoom and text scaling, localization, long labels, right-to-left content where supported, and user-selected extremes;
- keyboard-only, touch-only, pointer, screen reader, high-contrast or forced-colors mode, and reduced motion;
- focus restoration, history and addressable state, cancellation and retry, destructive operations, and external side effects;
- light and dark themes when both are supported, without assuming either polarity is universally superior.

`[EVID HCD-1 HCD-2]` Automated checks supplement rather than replace representative-user evaluation. Define effectiveness, efficiency, comprehension, error, and satisfaction measures from the task instead of treating checklist completion as proof of usability.
