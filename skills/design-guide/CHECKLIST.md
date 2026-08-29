# Pre-ship checklist

Run before calling UI work done. Every "no" is a fix, not a note. Items apply to GUI, TUI, and CLI unless a surface is named.

1. **Hierarchy** — can a first-time viewer say in 3 seconds what the primary task/action is?
2. **Glance test** — no label explains the design; every label reports a value. Delete anything that teaches.
3. **One primary action** — exactly one emphasized primary control per view/command.
4. **Consistency** — every spacing/size/color value came from FOUNDATIONS.md (no `margin: 7px`).
5. **Structure** — nothing is rounded; the only shadows are hard offsets on the primary action and on overlays (FOUNDATIONS.md § Structure).
6. **Same thing, same look** — identical components across the screen use identical markup/classes, not re-styled copies.
7. **States complete** — every interactive element has hover, focus-visible, active, disabled (GUI) / visible focus (TUI) / working `--help` (CLI).
8. **Feedback** — every action produces a visible response; every long operation shows progress; nothing succeeds or fails silently.
9. **Errors useful** — every error says what happened, why, and what to do; user input survives errors.
10. **Empty & loading** — no blank regions; empty states teach; loading states appear for >300ms operations.
11. **Destructive guarded** — confirms name the object and consequence; recoverable where possible.
12. **Color hygiene** — contrast ≥ 4.5:1 text (3:1 large/borders); meaning never carried by color alone; `NO_COLOR`/monochrome still fully usable (TUI/CLI).
13. **Keyboard** — everything reachable by keyboard; Esc cancels dialogs; focus order is sensible (GUI/TUI).
14. **Text** — labels are verbs, error sentences are sentences, no truncated content without a `…`/tooltip escape hatch.
15. **Scope check** — anything shown that the current task does not need was removed or demoted.
