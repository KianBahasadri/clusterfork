---
name: design-guide
description: Universal UI design rules for any interface the user is building — web or desktop GUI, mobile, terminal TUI, or CLI. Use when creating, reviewing, or restyling any user-facing screen, page, dashboard, form, table, dialog, or command output; also before shipping UI changes.
---

# Design Guide

Applies to **every user-facing surface**: web pages, desktop and mobile GUIs, dashboards, terminal TUIs, and CLI output. The principles are identical everywhere. Only the concrete techniques differ per surface.

**`reference.html` renders all of it.** Nine views behind one switcher: foundations, components and data (GUI values); four application shells (`#dashboard` web, `#desktop`, `#mobile`, `#console`); and two terminal screens (`#tui`, `#cli`). Open the view for the surface you are building. Pixel values are GUI-only and never cross into terminal work — the TUI and CLI views carry their own conventions.

## Stance

**A single glance tells all.** Every value a screen depends on — count, size, key, unit, status — is printed on the thing it describes. No explanatory subtext, no caption teaching the reader what they are looking at. If a label teaches instead of reporting, delete it.

Power, speed and flexibility beat convenience: density over whitespace, keyboard over pointer, one dense screen over three friendly ones. This is a deliberate trade — harder to learn, faster forever after. These interfaces are built for power users, not for novices.

## Six principles

Violations of these are the reason UIs feel bad. Everything else is detail.

1. **Hierarchy** — the most important thing on a screen must be the most visually prominent. If everything is emphasized, nothing is.
2. **Consistency** — the same thing looks and behaves the same way everywhere. Spacing, colors, wording, and interaction patterns come from the shared scale, not improvised per screen.
3. **Affordance** — anything interactive must look interactive before it is clicked; anything not interactive must not look clickable.
4. **Feedback** — every action gets an immediate, visible response: pressed state, spinner, status line, exit code. Silence reads as broken.
5. **Simplicity** — show what is needed for the current task; hide the rest until asked (progressive disclosure). Removing is a design decision too.
6. **Accessibility** — sufficient contrast, keyboard operability, text that makes sense out of context, respect for the user's settings (font size, `NO_COLOR`, reduced motion).

## Process

1. **Understand** — what is the one primary task of this screen/command? Everything supports that task or gets demoted.
2. **Structure** — decide the information hierarchy and grouping before any styling.
3. **Visual** — apply the foundations (spacing, type, color) from the shared scale. Never invent values.
4. **Interaction** — wire up every state: hover/focus/pressed, loading, empty, error. A component without its states is half built.
5. **Check** — run [CHECKLIST.md](CHECKLIST.md) before declaring done.

## Routing

| You are doing... | Read |
|---|---|
| Starting any interface, or choosing values (spacing, type, color) | [FOUNDATIONS.md](FOUNDATIONS.md) |
| Building a specific GUI component (button, form, table, modal, nav) | [COMPONENTS.md](COMPONENTS.md), and view [reference.html](reference.html) `#components` |
| Building or reviewing a TUI screen | [COMPONENTS.md](COMPONENTS.md) § TUI, and [reference.html](reference.html) `#tui` |
| Building or reviewing a CLI command | [COMPONENTS.md](COMPONENTS.md) § CLI, and [reference.html](reference.html) `#cli` |
| Handling loading / empty / error / destructive actions / progress | [PATTERNS.md](PATTERNS.md) |
| About to call it done | [CHECKLIST.md](CHECKLIST.md) |

Companion files live in the same directory as this SKILL.md. Read only the section you need.

## Hard rules (all surfaces)

- Use values from the shared scales in FOUNDATIONS.md. Invented spacing, sizes, or colors are bugs.
- Interactive elements get visible hover **and** focus states (GUI/TUI) or discoverable help (CLI).
- Errors say what happened, why, and what to do next — never just an error code or "something went wrong".
- Destructive actions are confirmed and reversible where possible. See PATTERNS.md.
- Empty states teach: say what will appear here and how to make that happen.
- Every label reports a value; none explains the design. Explanation lives in these files, never on the screen.
- Match the platform's existing conventions before imposing your own.
