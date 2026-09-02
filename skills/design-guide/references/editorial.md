# Editorial and Reading Interfaces

Use this guide when sustained reading, scanning, reference, or learning is the dominant task: articles, essays, blogs, documentation, publications, and dedicated reading views.

Do not select it merely because a page contains text. Product marketing is promotional, a form is transactional, and a live API console embedded in documentation is locally operational.

## Outcome

Give authored content first claim on attention. Readers can begin without clearing interface chrome, maintain orientation, distinguish argument from evidence, follow and return from references, and adapt the reading surface without losing content or place.

## Reading Frame

- `[INV]` Maintain one coherent primary reading path. Navigation, sharing, subscription, annotation, and personalization cannot occlude or reorder required prose, unexpectedly shift it, or fragment it into interface detours.
- `[SIG]` Start Latin-script body copy at a moderate measure, roughly 45–75 characters per line, with a stable start edge. Treat that range as a tunable starting convention—not a cognitive constant—and adapt it to the script, face, genre, viewport, and reading goal.
- `[EVID READ-3]` Test measure against both comprehension and speed. A medium line helped in one controlled screen-reading task, while the wider literature varies and readers' preferences need not predict performance.
- `[EVID READ-1 READ-2]` When proofreading or fine-detail discrimination matters, consider positive polarity as the default. Preserve a designed dark alternative where the audience, environment, platform, or preference supports it; the evidence does not make light mode universally comfortable or superior.
- `[EVID READ-4]` Judge type at its rendered size, distance, scaling, language, and face. No nominal pixel or point size is universal.
- `[SIG]` Keep long-form body typography stable enough to recede during reading. Concentrate display type, color, illustration, and expressive composition at titles, openings, section transitions, and evidence-bearing media.
- `[SIG]` Choose serif, sans-serif, or another category from the actual face and genre. Test character discrimination, italics, weight availability, line spacing, and font fallback instead of assigning readability to a category name.
- `[HYP]` Reading progress, focus modes, ambient theme shifts, reactions, and persistent reading-time estimates are optional experiments. Retain them only when they improve orientation or agency; dwell time alone is not evidence of comprehension.

## Structure and Navigation

- `[REQ A11Y-1]` Encode language, meaningful sequence, headings, lists, quotations, figures, captions, landmarks, and link purposes programmatically. Preserve bypass navigation, keyboard operation, visible focus, and a reading order that matches the intended document.
- `[INV]` The main argument remains coherent when sidebars, embeds, footnotes, and optional enhancement are removed or linearized.
- `[EVID NAV-1]` Use descriptive headings, link labels, previews, and structural cues that give readers a truthful sense of what lies behind a path.
- `[EVID READ-5]` In explanatory material, consider meaningful signals such as headings and structural emphasis when they clarify the conceptual model. Avoid over-signposting fiction, poetry, or intentionally nonlinear work.
- `[EVID READ-6]` Avoid forcing required linear learning through repeated link traversal without evidence that branching serves the audience. Hyperlinks remain valuable for reference, optional depth, and expert exploration.
- `[CONV]` Place stable article identity near the opening: title, author or responsible organization, publication date, material-update date, and category when useful. Represent dates and authorship as data, not decorative microcopy.
- `[INV]` A table of contents mirrors real headings, deep-links to them, and preserves normal history. It cannot become a second competing outline.
- `[INV]` Footnote and endnote navigation is reciprocal: a reader can inspect the note and return to the same reading position. External source links preserve normal platform history and opening behavior.
- `[REQ A11Y-1]` Distinguish links without color alone and make their purpose understandable in context.
- `[CONV]` Start with underlines or another familiar persistent cue for links in prose rather than hiding discoverability until hover.

## Prose, Evidence, and Media

- `[INV]` Separate current factual claims from quotations, estimates, examples, opinion, sponsorship, corrections, and superseded publication states. Identify paid, sponsored, affiliate, endorsed, and other materially connected content without making it imitate independent editorial origin.
- `[EVID READ-7]` In instructional or explanatory work, remove interesting detail that competes with the intended model rather than supporting it. This evidence does not prohibit atmosphere, digression, illustration, or surprise in literary and cultural work.
- `[INV]` Place figures near the claims they explain or support. Give each a meaningful caption and an appropriate textual alternative; a decorative chart cannot masquerade as evidence.
- `[INV]` Show applicable source, author, date, method, and uncertainty close enough to evaluate a consequential claim. A “reviewed” badge without reviewer, scope, and date has no epistemic value.
- `[REQ A11Y-1]` Supply required text alternatives, captions, transcripts, pause controls, and flash-safe treatments. Critical meaning cannot exist only inside an image, animation, audio track, or hover state.
- `[HYP]` Test an interactive illustration against a static or textual equivalent for comprehension, inference, and recall—not interaction count alone.

## Reader Agency and Interaction

- `[REQ A11Y-1]` Preserve content and operation under text resize, spacing overrides, reflow, zoom, and supported high-contrast settings. Ordinary prose should not require two-dimensional scrolling at the applicable reflow condition.
- `[CONV]` Preserve browser or platform selection, copy, find, link opening, history, deep links, text-to-speech, and print behavior. Optional scripts should enhance the document rather than gate it.
- `[EVID ICON-1 ICON-2]` Label ambiguous, rare, and consequential editorial tools.
- `[CONV]` Avoid turning headings or ordinary prose into surprising controls merely to expose shortcuts.
- `[SIG]` Reveal citations, notes, definitions, and secondary actions at the point of relevance without permanently competing with the reading column.
- `[HYP]` For long or repeated sessions, consider a coherent reader-settings surface for size, measure, line height, face, foreground, and background. Validate useful ranges with the actual language and layout; avoid presenting a preset as clinically optimal.
- `[INV]` Reader settings, disclosures, search, and reference navigation preserve the current anchor and do not create uncontrolled layout jumps.
- `[SIG]` Use motion only when it preserves reading position, connects a note to its source, or explains a state transition. Avoid scroll hijacking, parallax that destabilizes text, and continual motion beside prose.

## Optional Worked Artifact

`../assets/blog-reference.html` is a runnable exploration of one public essay. It is neither normative nor a template. Inspect it only when modifying or validating that artifact, or when the user explicitly asks to borrow a demonstrated interaction. Do not copy its palette, layout, controls, sample data, or feature breadth by default.

## Anti-Patterns

- Full-viewport lines or a fixed “scientific” character count applied across scripts, devices, and tasks.
- Forced low-contrast dark prose because surrounding brand surfaces are dark.
- Sticky share rails, subscription gates, autoplay media, or consent surfaces covering the opening argument.
- Visual heading levels with no semantic structure, or headings made interactive without a clear affordance.
- Cardifying every paragraph or hiding the required argument behind repeated disclosures.
- Ambiguous “read more” links, dead citations, or footnotes with no route back.
- Progress derived from unstable document height and presented as exact.
- Decorative figures or modeled statistics that imply evidence without a defined source and method.
- Reader modes that leave invisible controls focusable or remove equivalent capability.

## Validation

- Can a reader begin without dismissing unrelated interface elements?
- Does the document remain coherent in its programmatic order and with optional enhancement disabled?
- Can keyboard and screen-reader users identify the article, navigate its heading structure, operate its tools, and return from references?
- At supported zoom, text size, spacing, and narrow viewports, is ordinary prose available without clipping or two-dimensional scrolling?
- Is the measure comfortable in the actual face, language, content, and viewport extremes? Test comprehension or retrieval when the decision matters.
- Does every figure explain, evidence, orient, or intentionally contribute to the genre?
- Do loading fonts and media preserve layout and reading position?
- For each `[HYP]`, is success defined as comprehension, retrieval, orientation, or reader control rather than raw engagement?
