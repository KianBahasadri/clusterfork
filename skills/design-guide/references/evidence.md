# Evidence Register

Reviewed 2026-09-02. Verify current standards and platform documentation when a consequential decision depends on their exact wording, version, level, or threshold.

## How to Use This Register

Evidence calibrates a prescription; it does not generate a design by itself.

1. Define the users, goal, content or data, environment, platform, input methods, frequency, and consequences of error.
2. Select applicable requirements before heuristics. A standard is binding only when the product, jurisdiction, contract, or team adopts it.
3. Follow the claim ID from a situational guide to the record below.
4. Apply only the supported claim. Preserve the stated limits and test transfer to the actual context.
5. If evidence conflicts, prefer representative-user evaluation over a universal assertion. Record effectiveness, efficiency, comprehension, errors, recovery, and satisfaction as the task requires.

Prefer systematic reviews and converging studies for broad claims, primary experiments for narrow effects, official standards for conformance, and current platform documentation for conventions. `[EVID]` marks a research relationship, not a confidence grade: inspect the stated source type, convergence, and limits. Expert heuristics and canonical theory can be valuable without being experimental proof; label them honestly. Do not cite a whole handbook for a precise threshold without locating the relevant section.

## Foundations and Interaction

### HCD-1 — Usability is contextual

- **Source:** [ISO 9241-11:2018, *Usability: Definitions and concepts*](https://www.iso.org/standard/63500.html) (international standard).
- **Supports:** evaluate effectiveness, efficiency, and satisfaction for specified users, goals, and context of use.
- **Limits:** does not prescribe a visual style, component library, or universal score.
- **Use:** `[EVID]` when defining evaluation outcomes; `[REQ]` only when the standard is adopted.

### HCD-2 — Human-centred work is iterative

- **Source:** [ISO 9241-210:2019, *Human-centred design for interactive systems*](https://www.iso.org/standard/77520.html) (international standard).
- **Supports:** understand context, specify user requirements, produce solutions, and evaluate them iteratively with user involvement.
- **Limits:** a process standard cannot prove that a finished interface is usable.
- **Use:** `[EVID]` for the design workflow; `[REQ]` only when adopted.

### A11Y-1 — Web accessibility requirements

- **Source:** [WCAG 2.2](https://www.w3.org/TR/WCAG22/) (W3C Recommendation).
- **Supports:** testable web-content criteria for semantics and relationships, color use, contrast, reflow, content on hover or focus, keyboard access, focus order and visibility, pointer alternatives and targets, error handling, accessible authentication, names and roles, and status messages.
- **Limits:** criterion level and exceptions matter; WCAG does not address every disability need and is not automatically a legal mandate everywhere.
- **Use:** `[REQ]` with the exact criterion, level, technology, and product conformance target.

### A11Y-2 — Applying WCAG outside the web

- **Source:** [WCAG2ICT](https://www.w3.org/TR/wcag2ict/) (W3C Group Note).
- **Supports:** interpretation of WCAG principles for non-web documents and software.
- **Limits:** informative, not a W3C Recommendation; native platform accessibility requirements still apply.
- **Use:** `[EVID]` or `[CONV]`, and `[REQ]` only through another adopting authority.

### WEB-1 — Accessible web widget conventions

- **Source:** [WAI-ARIA Authoring Practices Guide](https://www.w3.org/WAI/ARIA/apg/) (W3C working-group guidance).
- **Supports:** established semantics, focus management, and keyboard behavior for common web GUI patterns.
- **Limits:** APG is not itself normative; examples are illustrative and require browser and assistive-technology testing. Native HTML is preferable when it supplies the needed behavior.
- **Use:** `[CONV]` alongside applicable WCAG requirements.

### DM-1 — Direct manipulation

- **Source:** Ben Shneiderman, [“Direct Manipulation: A Step Beyond Programming Languages”](https://doi.org/10.1109/MC.1983.1654471) (1983 theory and design synthesis).
- **Supports:** continuous representation of objects, rapid incremental action, immediate visible feedback, and reversibility as a coherent interaction style.
- **Limits:** not broad experimental proof that every object needs actions or that direct manipulation is best for every task and platform.
- **Use:** `[EVID]` for manipulable domain objects; `[SIG]` when extending depth beyond demonstrated user needs.

### LEARN-1 — Active users and learning through use

- **Source:** John M. Carroll and Mary Beth Rosson, “The Paradox of the Active User,” chapter 5 in [*Interfacing Thought*](https://mitpress.mit.edu/9780262031257/interfacing-thought/) (MIT Press, 1987, pp. 80–111; conceptual synthesis grounded in software-use studies).
- **Supports:** users often prioritize immediate productive action and assimilate new systems through prior knowledge rather than studying complete instructions first.
- **Limits:** does not prove that tutorials are always harmful, that all systems should be instructionless, or that progressive disclosure always improves learning. Complex and safety-critical systems may require training.
- **Use:** `[EVID]` for an immediate primary path; `[HYP]` for a particular layered-learning design.

### NAV-1 — Information scent and navigation

- **Source:** Peter Pirolli and Stuart Card, [“Information Foraging”](https://doi.org/10.1037/0033-295X.106.4.643) (1999 theory with empirical grounding).
- **Supports:** people choose information paths using cues about expected value and interaction cost.
- **Limits:** does not establish one navigation layout, menu depth, or universal disclosure rule.
- **Use:** `[EVID]` for meaningful labels, previews, and perceivable entries to deeper information.

### ICON-1 — Icons, labels, learning, and retention

- **Source:** Susan Wiedenbeck, [“The use of icons and labels in an end user application program”](https://doi.org/10.1080/014492999119129) (1999 controlled study).
- **Supports:** in the studied application, novices initially performed better with labels or icon-plus-label controls than with icons alone; icon-plus-label controls were rated easier to use.
- **Limits:** one application and novice population; it does not require permanent visible labels for every familiar icon or expert tool.
- **Use:** `[EVID]` to label unfamiliar or consequential icons and test comprehension before removing labels.

### ICON-2 — Testing graphical symbols

- **Source:** [ISO 9186-1:2014](https://www.iso.org/standard/59226.html) (international standard for comprehension testing of graphical symbols).
- **Supports:** symbol meaning should be evaluated with intended users rather than inferred from geometry or designer intuition.
- **Limits:** applies to graphical-symbol testing, not the full usability of an interactive control.
- **Use:** `[EVID]`; `[REQ]` only when adopted by the relevant domain.

## Editorial and Reading Evidence

### READ-1 — Display polarity and proofreading

- **Source:** Axel Buchner and Nadine Baumgartner, [“Text-background polarity affects performance irrespective of ambient illumination and colour contrast”](https://doi.org/10.1080/00140130701306413) (2007 experiments).
- **Supports:** positive polarity improved proofreading performance in the studied conditions; luminance contrast could not be replaced by hue contrast.
- **Limits:** proofreading is not every form of sustained reading, and the study does not settle preference, photosensitivity, low-vision needs, OLED power, or brand expression.
- **Use:** `[EVID]` for a light reading default when fine-detail performance is important, while retaining user choice where appropriate.

### READ-2 — Polarity across age groups

- **Source:** Cosima Piepenbrock et al., [“Positive display polarity is advantageous for both younger and older adults”](https://doi.org/10.1080/00140139.2013.790485) (2013 experiment).
- **Supports:** positive polarity improved visual acuity and proofreading for the younger and older groups studied.
- **Limits:** does not prove that every user, environment, display, or reading task performs better in light mode.
- **Use:** converging `[EVID]` with READ-1, not a prohibition on dark themes.

### READ-3 — Line length and reading task

- **Source:** Mary C. Dyson and Mark Haselgrove, [“The influence of reading speed and line length on the effectiveness of reading from screen”](https://doi.org/10.1006/ijhc.2001.0458) (2001 experiment).
- **Supports:** in the studied screen-reading task, a medium 55-character line supported comprehension and speed better than the short condition; speed and comprehension goals produced different behavior.
- **Limits:** line-length findings vary across studies, content, typography, viewport, language, and reading goal. Fifty-five characters is not a universal optimum.
- **Use:** `[EVID]` for starting with a moderate measure and testing the actual reading task; any exact range remains `[CONV]` or `[SIG]`.

### READ-4 — Rendered print size

- **Source:** Gordon E. Legge and Charles A. Bigelow, [“Does Print Size Matter for Reading?”](https://doi.org/10.1167/11.5.8) (2011 vision-science and typography review).
- **Supports:** reading performance depends on rendered visual size and a reader's critical print size; nominal point or pixel size alone is an inadequate universal rule.
- **Limits:** does not identify one type size or family for every device, distance, language, or visual ability.
- **Use:** `[EVID]` for testing actual rendering and preserving user scaling.

### READ-5 — Signaling in explanatory material

- **Source:** Patricia D. Mautone and Richard E. Mayer, [“Signaling as a Cognitive Guide in Multimedia Learning”](https://doi.org/10.1037/0022-0663.93.2.377) (2001 experiments).
- **Supports:** meaningful organizational signals improved some transfer outcomes in the studied short science lessons.
- **Limits:** does not establish that more headings, summaries, or emphasis improve every genre; fiction, reference work, expert material, and long-form reading present different tasks.
- **Use:** `[EVID]` when structure helps readers form an explanatory model.

### READ-6 — Linear and networked learning text

- **Source:** Helen Blom et al., [“Comprehension and navigation of networked hypertexts”](https://doi.org/10.1111/jcal.12243) (study of 80 first-year secondary-school students).
- **Supports:** in the studied learning task, networked hypertext produced lower text and structural knowledge than linear digital text.
- **Limits:** does not argue against hyperlinks generally and transfers weakly to adults, experts, reference lookup, or intentionally exploratory material.
- **Use:** `[EVID]` against making required novice learning depend on fragmented traversal without testing.

### READ-7 — Seductive details in instruction

- **Source:** Shannon F. Harp and Richard E. Mayer, [“How Seductive Details Do Their Damage”](https://doi.org/10.1037/0022-0663.90.3.414) (1998 four experiments with undergraduate science passages).
- **Supports:** interesting but instructionally irrelevant additions reduced recall and transfer in the studied explanatory material.
- **Limits:** does not show that atmosphere, visual identity, digression, or illustration is harmful in journalism, literature, criticism, or promotional storytelling.
- **Use:** `[EVID]` when an explanatory page's primary outcome is learning or transfer.

## Operational, Data, and Automation Evidence

### DATA-1 — Accuracy of quantitative encodings

- **Source:** William S. Cleveland and Robert McGill, [“Graphical Perception”](https://doi.org/10.1080/01621459.1984.10478080) (1984 theory and controlled experiments).
- **Supports:** elementary graphical encodings differ in perceptual accuracy; position along a common scale and length support more precise comparison than angle or area in the studied tasks.
- **Limits:** perceptual precision is only one criterion. Task, uncertainty, familiarity, space, and communication goal still determine the visualization.
- **Use:** `[EVID]` when precise comparison matters.

### DATA-2 — Animated transitions in statistical graphics

- **Source:** Jeffrey Heer and George Robertson, [“Animated Transitions in Statistical Data Graphics”](https://doi.org/10.1109/TVCG.2007.70539) (2007 controlled experiments).
- **Supports:** carefully designed animated transitions can improve tracking between related statistical-graphic states.
- **Limits:** does not support decorative motion, arbitrary spring effects, continuous pulses, or animation across every interface transition.
- **Use:** `[EVID]` for preserving object continuity in related data views; otherwise motion remains `[SIG]` or `[HYP]` and must satisfy accessibility requirements.

### UNC-1 — Uncertainty displays can mislead

- **Source:** Jake M. Hofman, Daniel G. Goldstein, and Jessica Hullman, [“How visualizing inferential uncertainty can mislead readers about treatment effects”](https://www.microsoft.com/en-us/research/publication/how-visualizing-inferential-uncertainty-can-mislead-readers-about-treatment-effects-in-scientific-results/) (CHI 2020, two experiments).
- **Supports:** the statistical quantity displayed changes readers' judgments; conventional confidence intervals can be misread as outcome variability.
- **Limits:** no single uncertainty encoding is universally best, and the experiments concern treatment-effect judgments.
- **Use:** `[EVID]` to name the quantity and consequence represented and test comprehension for the decision at hand.

### AUTO-1 — Appropriate reliance on automation

- **Source:** John D. Lee and Katrina A. See, [“Trust in Automation: Designing for Appropriate Reliance”](https://doi.org/10.1518/hfes.46.1.50_30392) (2004 research review and model).
- **Supports:** trust and reliance should be calibrated to automation capability and context; purpose, process, and performance information can affect reliance.
- **Limits:** more explanation or confidence display is not automatically better, and trust is not the same as correctness or reliance.
- **Use:** `[EVID]` for inspectable capability and limits, evaluated against actual reliance behavior.

### AUTO-2 — Types and levels of automation

- **Source:** Raja Parasuraman, Thomas B. Sheridan, and Christopher D. Wickens, [“A Model for Types and Levels of Human Interaction with Automation”](https://doi.org/10.1109/3468.844354) (2000 model and synthesis).
- **Supports:** automation can act at information acquisition, analysis, decision selection, and action implementation, at different levels; automation changes human work rather than simply removing it.
- **Limits:** the taxonomy does not choose the right allocation without task, reliability, consequence, workload, and failure analysis.
- **Use:** `[EVID]` when deciding what the user can inspect, approve, override, or recover.

### OPS-1 — Spaceflight display guidance

- **Source:** [NASA-STD-3001 Volume 2, Revision F](https://standards.nasa.gov/standard/NASA/NASA-STD-3001_VOL_2) (July 14, 2026), [Appendix F, Display Standard](https://www.nasa.gov/reference/appendix-f-vol-2/) (official technical requirements; verify the current revision before use).
- **Supports:** for covered spaceflight displays, task-relevant grouping, distinguishable interaction roles, explicit data state, redundant critical cues, and disciplined alert salience.
- **Limits:** scoped to flight-element displays. Its color mappings, contrast targets, font treatment, navigation placement, and alert timing are not general GUI requirements.
- **Use:** `[REQ]` only for covered programs; otherwise narrowly transferred `[EVID]` with an analogous operational problem.

### OPS-2 — Nuclear human-system interface review

- **Source:** U.S. Nuclear Regulatory Commission, [NUREG-0700 Revision 4](https://www.nrc.gov/reading-rm/doc-collections/nuregs/staff/sr0700/r4/index) (published January 2026; human-system interface design review guidelines).
- **Supports:** systematic evaluation of displays, interaction, alarms, automation, procedures, workstations, degraded conditions, and maintainability in nuclear control contexts.
- **Limits:** a safety-critical nuclear review framework, not a style guide for consumer dashboards or ordinary business software.
- **Use:** `[REQ]` when adopted in scope; otherwise `[EVID]` only where the underlying control-room problem transfers.

## Transactional Evidence

### FORM-1 — Combined web-form guidelines

- **Source:** Mirjam Seckler et al., [“Designing usable web forms: empirical evaluation of web form improvement guidelines”](https://doi.org/10.1145/2556288.2557265) (CHI 2014 controlled eye-tracking experiment, N=65).
- **Supports:** aggregate outcomes generally improved when a combined set of form guidelines was applied, with effects varying across the three forms and measures.
- **Limits:** the intervention bundled many changes, so it does not isolate every individual rule; it does not establish one layout for all languages, devices, risks, or expert workflows.
- **Use:** `[EVID]` for evaluating a coordinated guideline package, not for attributing an effect to any individual rule; follow with local task testing.

### FORM-2 — Error-message proximity

- **Source:** Mirjam Seckler et al., [“User-friendly locations of error messages in web forms: Put them on the right side of the erroneous input field”](https://doi.org/10.1016/j.intcom.2012.03.002) (2012 online study, N=303).
- **Supports:** messages placed near their fields performed better than form-top or form-bottom messages in the tested forms.
- **Limits:** a reported preference for one physical side depends on layout, viewport, magnification, and writing direction; proximity is the safer transfer.
- **Use:** `[EVID]` for visual proximity of local field errors. Use the applicable accessibility requirement, not this experiment, for programmatic association.

### FORM-3 — Validation timing

- **Source:** Javier A. Bargas-Avila et al., [“Usable error message presentation in the World Wide Web: Do not show errors right away”](https://doi.org/10.1016/j.intcom.2007.01.003) (2007 two web-form studies).
- **Supports:** in the studied forms, interruptive immediate error presentation performed worse than validation after the form was completed.
- **Limits:** does not settle validation timing for every mobile, native, asynchronous, safety-critical, or expert workflow. Modern field-completion feedback can differ from errors shown while an entry remains unfinished.
- **Use:** `[EVID]` to avoid premature interruption and to test a coherent validation boundary.

## Promotional and Brand Evidence

### AD-1 — U.S. advertising substantiation and endorsements

- **Sources:** U.S. Federal Trade Commission, [*Policy Statement Regarding Advertising Substantiation*](https://www.ftc.gov/legal-library/browse/ftc-policy-statement-regarding-advertising-substantiation) and [16 CFR Part 255, *Guides Concerning Use of Endorsements and Testimonials in Advertising*](https://www.ecfr.gov/current/title-16/chapter-I/subchapter-B/part-255).
- **Supports:** within the U.S. FTC framework, advertisers need a reasonable basis before disseminating express or implied objective claims; covered endorsements must be honest and substantiated, and unexpected material connections require clear and conspicuous disclosure.
- **Limits:** U.S.-specific legal guidance whose application depends on jurisdiction, medium, claim, audience, product, and facts. The endorsement guides are administrative interpretations, not an exhaustive global advertising code, and the current text must be verified.
- **Use:** `[REQ]` only when the relevant U.S. authority and facts apply; otherwise a prompt to identify the governing jurisdiction and obtain qualified review for consequential claims.

### PROMO-1 — Visual complexity and first impressions

- **Source:** Alexandre N. Tuch et al., [“The role of visual complexity and prototypicality regarding first impression of websites: Working towards understanding aesthetic judgments”](https://doi.org/10.1016/j.ijhcs.2012.06.003) (2012 two-study experiment).
- **Supports:** visual complexity and category prototypicality influenced rapid aesthetic ratings of website screenshots in the studied samples.
- **Limits:** screenshot aesthetics are not comprehension, trust, accessibility, conversion, long-term preference, or brand distinctiveness. “Low complexity” is not a mandate for minimalism.
- **Use:** `[EVID]` when testing first-impression appeal and category recognizability; treat a particular hierarchy as `[HYP]` and use real task and downstream measures for product decisions.

### PROMO-2 — Rapid visual-appeal judgments

- **Source:** Gitte Lindgaard et al., [“Attention web designers: You have 50 milliseconds to make a good first impression!”](https://doi.org/10.1080/01449290500330448) (2006 experiments).
- **Supports:** participants formed repeatable visual-appeal ratings after very brief homepage exposure in the studied conditions.
- **Limits:** does not establish a visitor decision deadline, explain the cause of the rating, or show effects on comprehension, trust, accessibility, or conversion.
- **Use:** `[EVID]` for deliberate first-frame composition, followed by task-specific evaluation.

### PROMO-3 — Banner-like distraction

- **Source:** Moira Burke et al., [“High-cost banner blindness: Ads increase perceived workload, hinder visual search, and are forgotten”](https://doi.org/10.1145/1121112.1121116) (2005 laboratory experiments).
- **Supports:** flashing-text banners increased perceived workload; animated and static commercial banners slowed visual search; recall of the banners themselves was low, especially for animated banners.
- **Limits:** does not prohibit expressive motion or large visuals generally; transfer depends on whether a treatment competes like an unrelated banner.
- **Use:** `[EVID]` to protect proposition and action from unrelated persistent competition.

### PERF-1 — Current field performance signals

- **Source:** Google/Chrome, [“How the Core Web Vitals thresholds were defined”](https://web.dev/articles/defining-core-web-vitals-thresholds) (official platform guidance).
- **Supports:** field measurement of loading, interaction responsiveness, and layout stability at the 75th percentile of page views, with documented operational thresholds.
- **Limits:** platform guidance rather than independent research; values and metrics can change, and passing them does not prove accessibility, comprehension, ranking, or conversion.
- **Use:** `[CONV]` after verifying current metrics and project-specific performance targets.

### ETHICS-1 — Deceptive interface patterns

- **Source:** Arunesh Mathur et al., [“Dark Patterns at Scale: Findings from a Crawl of 11K Shopping Websites”](https://doi.org/10.1145/3359183) (2019 large observational study and taxonomy).
- **Supports:** coercive, steering, and deceptive patterns were identified across shopping sites and categorized by their characteristics, influence mechanisms, and potential harms to user choice.
- **Limits:** prevalence does not measure the harm of every instance or make all persuasion unethical; legal definitions vary and require current jurisdiction-specific review.
- **Use:** `[EVID]` for recognizing and naming observed deceptive-pattern families. Product ethics establishes prohibitions; use current law for `[REQ]` assertions.
