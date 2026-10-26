# GRADE Book Download Site

Use [KianBahasadri/grade-book-offline](https://github.com/KianBahasadri/grade-book-offline) as an external example of a download page built around an interactive book, an edition archive, and downloads that remain accessible without JavaScript.

## Where to find it

- **Published site:** [GRADE Book offline editions](https://kianbahasadri.github.io/grade-book-offline/).
- **Current source:** Browse the repository's default branch. Its [README](https://github.com/KianBahasadri/grade-book-offline#download-website) explains the website and current local preview workflow.
- **Local checkout, if present:** `~/grade-book-offline/`. Source paths below are relative to that repository, not this skill.
- **Website source:** [site/](https://github.com/KianBahasadri/grade-book-offline/tree/main/site). The HTML is a template; [scripts/site.py](https://github.com/KianBahasadri/grade-book-offline/blob/main/scripts/site.py) builds the published pages from release metadata.
- **Behavior checks:** [tests/test_site.py](https://github.com/KianBahasadri/grade-book-offline/blob/main/tests/test_site.py) for the site's expected rendering and interactions.

## What to look for

| Area | Source starting points | Patterns to examine |
| --- | --- | --- |
| Page chrome and composition | `site/index.html`, `site/style.css` | Centering the book while keeping header utilities reachable, pointer handling when the enlarged object passes under controls, a skip link to downloads, and theme controls outside the object interaction. |
| Closed-book interaction | `site/site.js`, `site/style.css` | Separating clicks from drags, pointer capture, bounded rotation and zoom, inertia, suppressing clicks after a drag, keyboard equivalents, and preserving browser pinch zoom. |
| Closed-book rendering | `site/book-renderer.js`, `site/style.css` | Layered cover/spine/page geometry, texture filtering, drawing only while needed, and a CSS fallback when WebGL is unavailable or loses its context. |
| Opening and responsive layout | `site/site.js`, `site/style.css` | Transitioning from a closed object into a readable spread, the single-page phone treatment, interrupted opening/closing, flattening transformed layers for crisp text, and keeping zoomed content within reach. |
| Focus and motion | `site/index.html`, `site/site.js`, `site/style.css` | Keeping closed content inert, updating expanded state, moving focus into downloads and back to the cover, dismissal order for Escape, and reduced-motion behavior throughout the interaction. |
| Download choices | `site/index.html`, `scripts/site.py` | Real format links with useful file metadata, clear edition context, permanent edition pages, and downloads that remain present when scripting is unavailable. |
| Edition archive | `site/index.html`, `site/site.js`, `scripts/site.py` | Native disclosure as a baseline, progressive enhancement of search/date/sort controls, invalid date-range feedback, result counts, empty states, and keyboard focus when opening the archive. |
| Theme and fallback behavior | `site/site.js`, `site/style.css` | Following system theme until an explicit choice, accessible toggle labels, restoring fallback rendering, and keeping a usable document without scripts, animation, or WebGL. |

Read the current styles, interaction code, and tests for exact dimensions, timings, gesture thresholds, and breakpoints. Preview both wide and narrow layouts, then exercise keyboard-only use, reduced motion, and JavaScript-disabled downloads when adapting these patterns.

## Applying the example

Adapt the book interaction, archive, or progressive enhancement to the target product. Use its own title, artwork, formats, and edition data. Consult the upstream license and content attribution before reusing code, fonts, icons, or book material.

The example has its own visual treatment. Shared catalog tokens and generic control/overlay rules remain in [Foundations](foundations.md), [Controls and Forms](controls-and-forms.md), and [Overlays and Feedback](overlays-and-feedback.md).
