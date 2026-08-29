# Pre-ship checklist

Run before calling UI work done. Every "no" is a fix, not a note. Items apply to GUI, TUI, and CLI unless a surface is named.

1. **Hierarchy** — can a first-time viewer say in 3 seconds what the primary task/action is?
2. **One primary action** — exactly one emphasized primary control per view/command.
3. **Consistency** — every spacing/size/color value came from FOUNDATIONS.md (no `margin: 7px`).
4. **Same thing, same look** — identical components across the screen use identical markup/classes, not re-styled copies.
5. **States complete** — every interactive element has hover, focus-visible, active, disabled (GUI) / visible focus (TUI) / working `--help` (CLI).
6. **Feedback** — every action produces a visible response; every long operation shows progress; nothing succeeds or fails silently.
7. **Errors useful** — every error says what happened, why, and what to do; user input survives errors.
8. **Empty & loading** — no blank regions; empty states teach; loading states appear for >300ms operations.
9. **Destructive guarded** — confirms name the object and consequence; recoverable where possible.
10. **Color hygiene** — contrast ≥ 4.5:1 text (3:1 large/borders); meaning never carried by color alone; `NO_COLOR`/monochrome still fully usable (TUI/CLI).
11. **Keyboard** — everything reachable by keyboard; Esc cancels dialogs; focus order is sensible (GUI/TUI).
12. **Text** — labels are verbs, error sentences are sentences, no truncated content without a `…`/tooltip escape hatch.
13. **Scope check** — anything shown that the current task does not need was removed or demoted.
