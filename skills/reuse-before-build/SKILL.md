---
name: reuse-before-build
description: Audit planned or existing code for functionality already provided by the standard library, framework, or project dependencies. Use when asked to avoid reinventing the wheel, check whether a library already does something, or replace redundant custom implementations with maintained features.
---

# Reuse Before Build

Reduce the code the project has to maintain by using capabilities its runtime
and dependencies already provide. Find concrete replacements and verify that
they satisfy the project's requirements before removing custom behavior.

## Scope

Follow the requested scope. Otherwise focus on the current task's changes or
branch diff, reading callers and tests as needed to understand the contract.
Expand to a repository-wide audit when requested. For planned work, do this
check before introducing a new helper, abstraction, or dependency.

Match the requested action: report findings for a review; apply verified
replacements when implementation or cleanup is authorized. Invoking this skill
does not require a separate approval step for ordinary edits within that scope.

## Find what already exists

- Read the relevant dependency declarations, lockfiles, imports, and runtime
  constraints. Establish the versions and environments the project supports;
  an API in the latest release may not exist in this project.
- Look for custom implementations of general facilities such as parsing,
  validation, retries, pagination, caching, collection operations, or resource
  cleanup. Also inspect wrappers that duplicate framework behavior and manual
  plumbing that a supported configuration option or extension point supplies.
- Search the repository's existing helpers, the standard library, the framework,
  and declared dependencies. Prioritize features of packages already used in the
  relevant code. Choose the smallest suitable solution that reduces total
  maintenance, including any glue it needs.
- If those sources leave a substantial general-purpose mechanism uncovered,
  check established maintained packages as alternatives. Weigh the code they
  remove against dependency and integration costs; existing dependencies remain
  the first place to look.
- Verify candidates against documentation for the supported version or the
  installed package's public API and source. Use Context7 when available, or
  official documentation, release notes, and upstream source. Identify the exact
  API, option, or extension point; a similar name or remembered feature is not
  evidence that it covers the behavior.

Keep the search tied to the behavior under review. An audit should produce
actionable findings, not an inventory of unrelated packages.

## Decide whether it is a replacement

Compare the existing contract with the candidate, including the edge cases
that matter to its callers. Check relevant defaults, error behavior, types,
ordering, encoding, async behavior, resource ownership, and performance
constraints. Distinguish accidental implementation details from behavior the
application relies on.

Prefer supported public APIs. Account for required optional extras, build and
runtime environments, and the project's minimum supported versions. A package
present only as a transitive dependency is not a stable direct dependency.

Do not silently add or upgrade packages to make a candidate work. If that is
necessary, separate it from replacements available today and explain the
maintenance benefit and migration cost within the user's requested scope.
Adding a dependency for a trivial helper can increase the burden.

Keep custom code when it implements application policy, covers a documented
upstream gap, or preserves a required contract the candidate cannot meet.
A thin adapter can be appropriate when upstream handles the general mechanism
and the project supplies its own policy. Do not replace one small helper with
more adaptation code merely to claim fewer custom implementations.

If version support or equivalence cannot be established, mark the candidate as
unverified and leave the behavior intact. An unsuccessful search does not prove
that no upstream solution exists.

## Make and verify the change

For authorized edits, use the existing capability directly or through the
smallest necessary adapter. Update affected callers and remove obsolete helpers,
imports, and scaffolding made unnecessary by the replacement. Preserve unrelated
changes and avoid broad cleanup beyond the finding.

Run the relevant existing checks. Where equivalence is uncertain or the contract
is consequential, test observable behavior at the caller boundary, especially
edge cases where the old and new implementations could differ. Preserve useful
regression coverage when removing tests coupled to a deleted implementation.
Fewer lines alone do not establish a successful replacement.

## Report

For each finding or change, identify the custom code by file and location, the
exact replacement API and supported version, and the documentation link or
local source that establishes it. Explain what maintenance it removes and any
remaining adapter or behavior difference. State what was changed and checked,
or what remains a proposal.

Keep the report proportional to the work. If no verified replacement was found,
say which scope and dependencies were checked and briefly explain any important
custom code retained. Do not invent findings to meet a quota or claim the whole
repository is free of duplication after reviewing a diff.
