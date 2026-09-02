# Transactional and Workflow Interfaces

Use this guide when the dominant task is to complete a bounded goal and commit information, money, permission, or state: forms, checkout, booking, onboarding, applications, authentication, settings, and create/update/delete workflows.

A newsletter field inside an article does not make the whole article transactional. Apply this guide to the bounded flow and let its clarity outrank the containing page's expressive defaults inside that region.

## Outcome

Let users complete the real-world task accurately and confidently with the least necessary entry. Preserve their work, make commitments explicit, and provide a reliable path through errors, interruption, and recovery.

## Questions and Data Entry

- `[INV]` For every requested datum, identify why it is needed, who or what uses it, how it is validated, how long it persists, and why this user must provide it now. Ask only relevant branches.
- `[INV]` Match the user's domain rather than the storage schema. Accept reasonable formats, use locale-appropriate names, addresses, dates, numbers, and time zones, and avoid requesting information the system can safely derive.
- `[CONV]` Keep labels and non-obvious instructions visible while their fields are being completed; placeholder text is an example, not a persistent label.
- `[REQ A11Y-1]` When WCAG 2.2 applies, programmatically expose applicable information and relationships, labels or instructions, and control names, roles, and values under SC 1.3.1 (A), 3.3.2 (A), and 4.1.2 (A).
- `[INV]` Explain non-obvious format, eligibility, requiredness, sensitivity, and consequence before entry rather than revealing them only after failure.
- `[CONV]` Prefer native controls, appropriate input types, autofill, password-manager support, paste, speech input, and system pickers where they fit the data. A constrained choice control is useful only when users can understand its option set.
- `[EVID FORM-1]` Coordinated changes to labels, layout, grouping, input design, and errors improved aggregate outcomes in the studied forms, with effects varying by form and measure. Use the bundle as support for treating a form as a system, not as proof for any single rule.
- `[HYP]` Field order, grouping, optionality, defaults, label placement, and requested data are local hypotheses. Validate them against the domain and user population rather than optimizing the database model.

## Validation and Errors

- `[REQ A11Y-1]` When WCAG 2.2 applies, identify detected errors in text and offer a known correction under SC 3.3.1 (A) and 3.3.3 (AA); do not use color alone under SC 1.4.1 (A).
- `[EVID FORM-2]` Place visual field-specific messages near their fields. The evidence supports proximity; it does not establish one physical side across writing directions, zoom levels, and viewports.
- `[REQ A11Y-1]` Expose each detected error and its relationship to the affected field programmatically under the applicable WCAG 2.2 criteria, including SC 1.3.1 (A) and 4.1.3 (AA) where status messages apply.
- `[INV]` Do not present an entry as rejected before the system has enough information to make that determination.
- `[EVID FORM-3]` Earlier validation is not automatically better. Consider a coherent field, group, or submission boundary, and test feedback timing in the actual task.
- `[CONV]` After a submission with multiple errors, consider a navigable summary plus identical local messages. Move focus or announce updates according to the platform pattern without erasing context.
- `[INV]` Preserve valid input and current position across validation, back navigation, refresh, authentication, network failure, and server rejection wherever security permits.
- `[INV]` A disabled next action has a perceivable explanation of what remains. Prefer allowing activation and explaining fixable errors when a disabled state would conceal the path forward.

## Flow and Commitment

- `[INV]` Name actions by outcome and commitment. Distinguish Save draft, Continue, Book, Pay, Create, Update, Publish, and Delete instead of hiding consequences behind Submit, OK, or Confirm.
- `[INV]` Before commitment, expose the applicable target, total cost and recurrence, time zone, cancellation or renewal terms, permissions, affected scope, and reversibility.
- `[REQ A11Y-1]` When WCAG 2.2 applies, resolve the scope and exceptions for time limits under SC 2.2.1 (A), redundant entry under SC 3.3.7 (A), accessible authentication under SC 3.3.8 (AA), and error prevention under SC 3.3.4 (AA). For every covered legal commitment, financial transaction, submitted test response, or modification or deletion of user-controllable stored data, provide the applicable reversal, checking, or review-and-confirm path.
- `[EVID DM-1]` Prefer incremental, reversible progress where the domain permits it.
- `[CONV]` Use confirmation in proportion to consequence; usually omit routine confirmation dialogs for actions that are safely and visibly undoable.
- `[CONV]` For unfamiliar public workflows, start with one coherent question, decision, or information unit per step. Merge tightly related inputs for fluent or high-frequency work when that improves comparison and speed.
- `[INV]` Progress reflects real completed work, branching, and remaining commitment. Do not invent equal percentages for unequal or conditional steps.
- `[CONV]` A review surface should provide direct Change actions and return users to the review context without replaying unaffected steps.
- `[HYP]` Treat step count, pagination, progress visualization, review placement, guest versus account flow, and optional-step timing as choices to test in the actual task. Fewer screens or clicks is not sufficient evidence of a better workflow.

## Submission and Recovery

- `[INV]` Prevent duplicate commits at the system boundary. Disable or debounce repeated local activation only as an additional safeguard, not as the sole integrity mechanism.
- `[INV]` Distinguish editing, validating, submitting, accepted, processing, action required, completed, declined, failed, expired, and safe-to-retry states only when the system can observe them.
- `[INV]` On failure, preserve work, identify what happened to the degree known, state whether a commitment may have occurred, and give the next safe action.
- `[INV]` If price, availability, permission, or another material fact changes mid-flow, disclose the change before commitment and preserve unaffected input.
- `[INV]` Back, cancel, save, resume, retry, and expiration behavior matches the actual persistence and side effects. Never imply that closing a client view canceled server-side work unless confirmed.
- `[SIG]` Keep the point of action visually calm: one dominant next action, restrained secondary choices, protected error and warning treatments, and optional explanation disclosed near the relevant decision.
- `[INV]` Price, consent, recurrence, required data, scarcity, and refusal remain truthful and perceivable. Never use preselected consequential extras, disguised advertising, obstruction, or unequal treatment intended to defeat free choice.

## Authentication and Sensitive Tasks

- `[CONV]` Preserve paste and password-manager operation in authentication fields.
- `[REQ A11Y-1]` When WCAG 2.2 SC 3.3.8 (AA) is in scope, do not require a cognitive-function test for authentication without satisfying one of that criterion's exceptions.
- `[INV]` Explain why sensitive or surprising information is requested and how it affects the transaction. Minimize exposure in summaries, logs, analytics, and saved drafts.
- `[CONV]` Preserve the transaction across an authentication detour and return to the same safe point. Reconfirm material facts that may have changed while the user was away.
- `[HYP]` Social sign-in, passkeys, magic links, guest checkout, account creation timing, and trust copy depend on threat model, platform, population, and recovery needs.

## Anti-Patterns

- Asking for information without a delivery, legal, safety, operational, or user-value reason.
- Placeholder-only labels or constraints revealed only after failure.
- Red errors shown while a user is still typing a potentially valid value.
- A summary with no field links, a field message with no programmatic association, or color as the only cue.
- Clearing answers or asking for the same information again after back, refresh, sign-in, timeout, or network failure.
- A disabled Continue button with no explanation.
- A spinner that does not distinguish accepted, processing, declined, failed, or safe-to-retry.
- Generic action labels where activation creates a charge, booking, account, permission, deletion, or publication.
- Fees, recurrence, cancellation terms, data sharing, or irreversible effects disclosed after commitment.
- Preselected consequential choices, bundled consent, or visual pressure that changes the apparent meaning of an option.
- One-question-per-page applied mechanically to high-frequency expert work.

## Validation

- Can representative users complete the real-world outcome correctly on the first attempt and recover without outside help?
- Measure completion, first-attempt success, errors, recovery time, abandonment by step, duplicate submissions, and confidence; segment by device, language, expertise, and access method.
- Before commitment, can users explain what will happen, to whom or what, when, for how much, whether it recurs, and how it can be changed or canceled?
- Are labels, hints, requiredness, constraints, errors, and status updates perceivable visually and programmatically?
- Test paste, autofill, password managers, speech input, keyboard, screen readers, zoom, mobile keyboards, and localization.
- Test back, refresh, interrupted authentication, expired sessions, lost connectivity, server rejection, payment decline, changed availability, concurrent edits, and duplicate activation.
- Does changing one reviewed answer preserve unaffected answers and return to the correct review context?
- Does branching ask only relevant questions, and can users understand why sensitive or unexpected data is required?
- Compare workflow structure, validation timing, and defaults with actual users instead of counting screens or clicks.
