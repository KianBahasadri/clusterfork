---
name: conservative-academic-prose-sweep
description: Edit academic or technical prose to remove embellishment, empty qualifiers, vague credibility signals, and unsupported claims while preserving technical meaning, limitations, and the author's voice. Use for paper cleanups, de-hyping, de-AI-ing, accessibility passes, or technically exact prose sweeps; do not use for ordinary copyediting that only concerns spelling or house style.
---

# Conservative Academic Prose Sweep

Revise the paper so that every sentence says what happened, what the evidence supports, and no more. The result should sound like a careful researcher explaining the work clearly, not like marketing copy and not like a thesaurus-driven rewrite.

## Governing standard

Every modifier and every claim must earn its place.

- Remove a word when deleting it loses no technical meaning.
- Replace a broad label with the concrete procedure, observation, or result behind it.
- Keep a qualifier when it changes the scope, logic, method, or artifact being described.
- Do not make a sentence stronger, more general, or more causal than the evidence permits.
- Do not weaken a precise limitation merely to make the prose smoother.
- Prefer a clear undergraduate-readable explanation over compressed specialist phrasing, while retaining necessary technical terms.

This is a conservative edit. Change only passages whose accuracy or clarity genuinely improves. Do not flatten the author's voice or rewrite sound prose merely because another wording is possible.

## Distinctions that must remain explicit

Technical papers often become misleading when they blur adjacent layers. Keep these separate:

- What exists on the host.
- What the extractor observed.
- What a semantic-analysis stage derived.
- What the encoder placed in the planning problem.
- What the domain can express when the required facts are present.
- What the planner returned for one encoded problem.
- What was executed or checked on a host.

Apply the same discipline to evaluation claims:

- Reachability in an encoded model is not execution of an exploit.
- A generated plan is not automatically a working command sequence.
- A mechanism absent from extracted facts is unavailable to the planner even if the domain has actions for it; do not call it unrepresentable.
- A domain containing predicates or actions for a mechanism does not imply that every case of that mechanism is encoded or solved.
- An unexplained timeout, crash, or exit code does not establish its cause.
- A plan returned by a non-optimal planner is a returned plan, not necessarily the cheapest or best plan.
- A delete-relaxation result proves only what the planning formalism and algorithm justify. State the exact scope of the proof.
- Replaying stages separately, supplying intermediate identities, or checking markers is not the same as running one uninterrupted end-to-end exploit. Describe the procedure honestly.

## Wording to inspect, not blindly ban

Search for terms such as:

`exactly`, `validated`, `verified`, `live`, `successfully`, `clearly`, `fully`, `robust`, `novel`, `major`, `central`, `important`, `significant`, `substantial`, `comprehensive`, `strong`, `real`, `actual`, `concrete`, `controlled`, `reproducible`, `curated`, `purpose-built`, `useful`, `clean`, `easy`, `simple`, `small`, `large`, `only`, `all`, `every`, `shows`, `demonstrates`, `reveals`, and `exposes`.

These are candidate flags, not forbidden words. For each occurrence, ask:

1. What fact does this word add?
2. Is that fact supported in the paper, data, source, or repository?
3. Does the word define a real boundary, or merely make the work sound stronger?
4. Can the sentence name the procedure or result instead?
5. Would removing it change the technical claim?

Keep terms that carry real information. For example:

- “full virtual machine” can distinguish a VM with its own kernel from a container.
- “manual procedure” can disclose how a result was produced.
- “only” can define a necessary logical restriction.
- “reproducible” can be appropriate when the artifacts and procedure that provide reproducibility are identified.
- “proved unreachable” can be appropriate when the stated analysis provides that proof within a clearly named model.

## Common repairs

### Replace credibility labels with the check

Do not write “validated IR” if the actual fact is that two JSON files are checked against schemas when loaded. State that check.

Do not write “live-validated chain” if the procedure ran markers, impersonated intermediate users, or exercised stages separately. State each operation and the observed result.

Do not write “verified behavior” when the evidence is a particular unit test, package-version comparison, permission check, or container run. Name it.

### Replace metaphorical transformations with operations

Avoid claims such as:

> The pipeline turns a running host into a planning problem and then into an escalation plan.

State the operations instead:

> The extractor records host observations in JSON, the encoder maps those facts into a PDDL problem, and the planner searches that problem for a path to the goal.

Likewise, avoid saying that a model “understands,” an IR “makes” something possible, or a domain “finds” a plan when a more precise subject exists. Extractors collect, analyses derive, encoders emit, domains define, and planners return plans.

### Replace vague benefits with the mechanism

Instead of saying that an IR “makes the benchmark easier to rerun,” explain that later stages can reload the stored snapshot, reconstruct the in-memory model, re-encode the problem, and rerun the planner without rebooting or reconnecting to the host.

Instead of saying that a representation is “richer” or “more faithful,” name the additional facts or distinctions and the actions that consume them.

### Remove unsupported causal conclusions

If a planner exits after a large encoding but leaves no diagnostic, do not say that the result “exposes a scalability issue.” Report the problem size, exit behavior, resource limit, and missing diagnostic. Say that the cause remains unidentified.

Use “caused,” “isolated,” or “demonstrated” only when the experimental design supports that inference. Otherwise state the observed association or the controlled comparison.

### Scope generality precisely

Do not claim that the system “models dynamic loading” merely because it contains a load predicate. Say which load relations are extracted, which are encoded, what action consumes them, and which cases remain unsupported when those facts are absent.

Do not repair a benchmark miss with a pathname, fixture identifier, or one command string and then describe the result as general support. A general rule must be derived from mechanism-level evidence and must not emit the relation when that evidence is insufficient.

### Prefer evidence-bearing verbs

When possible, replace rhetorical verbs with the observed event:

- “The domain finds a cost-8 plan” → “The planner returns a cost-8 plan using this domain.”
- “The experiment demonstrates improved coverage” → “The system returns plans for 28 of 42 cases, compared with 7 for the baseline.”
- “The replay reveals a mismatch” → “The binary rejected the modeled identity during the container replay.”
- “A solved case shows…” → “A solved case means…” followed by the exact definition used in the evaluation.

Keep “shows,” “proves,” or “establishes” when the evidence genuinely warrants that relationship; do not remove them mechanically.

## Accessibility standard

Write as a careful expert explaining a graduate topic to an informed undergraduate.

- Prefer ordinary verbs and concrete nouns.
- Introduce specialized syntax before using it.
- Explain what a term does before relying on its name.
- Split sentences that contain several different claims or levels of evidence.
- Name the actor at each pipeline stage.
- Describe operational sequences in execution order when order matters.
- Avoid replacing understandable language with planning jargon solely to sound formal.

For example, “actions possible in the current state” may be clearer than “action instances applicable in the current state” unless the formal distinction between an action schema and a grounded action instance matters in that passage.

Accessibility must not come at the cost of accuracy. If a short sentence would hide a precondition, identity boundary, uncertainty, or model limitation, state that condition plainly.

## Review workflow

1. Confirm the requested scope and delivery rules, including whether to edit, stage, commit, push, or only report suggestions.
2. Inspect the working tree before editing and preserve unrelated user changes.
3. Read each assigned section continuously. Do not rely on search alone; semantic exaggeration often contains no obvious trigger word.
4. Mark claims involving novelty, causality, generality, validation, execution, reachability, reproducibility, or comparison.
5. For each marked claim, identify the strongest wording directly supported by the described method or available evidence.
6. Edit conservatively. Preserve citations, cross-references, LaTeX commands, defined terminology, numbers, and explicit limitations.
7. Run a candidate-word scan as a backstop. A useful starting pattern is:

   ```sh
   rg -n -i '\b(exactly|validated|validation|live|successfully|clearly|fully|major|central|important|significant|substantial|robust|novel|comprehensive|stronger|real|actual|controlled|reproducible|purpose-built|verified|useful|curated|deliberately|disposable|shows|demonstrates|reveals|exposes)\b' PAPER_SOURCE
   ```

8. Read the revised prose again without looking at the old wording. Look especially for sentences that still imply more execution, generality, causality, or certainty than the evidence provides.
9. Check the diff for accidental technical changes and whitespace errors.
10. Rebuild the paper with its normal build command. Check for errors, undefined references, overfull boxes, and layout regressions caused by the edits.
11. Follow the user's requested Git workflow exactly. Editing authorization does not imply permission to commit or push.

## Second-pass questions

The second pass is mandatory for a full-paper sweep. Ask:

- Does any sentence advertise a result before defining what was measured?
- Does any adjective substitute for a method?
- Does any sentence imply host execution when the evidence is model reachability?
- Does any sentence imply end-to-end execution when stages were checked separately?
- Does any sentence assign an operation to the wrong component?
- Does any failure explanation infer a cause that the logs do not identify?
- Does any claim about a whole mechanism rest on one fixture or one special rule?
- Can a reader tell which facts were observed, derived, encoded, planned over, and executed?
- Are words such as “only,” “all,” “every,” and “none” supported by the reported scope?
- Is every limitation still present after the prose was simplified?

## Reporting the result

Lead with what changed and whether the document rebuilt. Mention only the most consequential corrections, especially any overclaim that was narrowed. State the Git state accurately: unstaged, staged, committed, or pushed. Do not call the sweep “comprehensive,” “complete,” or “validated” merely because searches and a build succeeded; say which sections were read and which checks were run.
