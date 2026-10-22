# GRADE Book Download Site

The [GRADE Book reference](../assets/grade-book/index.html) is a single page whose primary control is a physical book. Rotate and zoom the closed book, then open it onto a spread: the inside cover on the left, the format list on the right. On a narrow screen the same leaf turns onto one page.

Use this reference for that object, the header archive, and the requirement that the downloads remain usable with JavaScript unavailable. Shared catalog tokens, generic buttons, and overlay geometry stay owned by [Foundations](foundations.md), [Controls and Forms](controls-and-forms.md), and [Overlays and Feedback](overlays-and-feedback.md). This page keeps its own canvas, type, and book geometry. Replace the title, cover, formats, and edition rows with the product being built.

The snapshot’s scripts and styles come from grade-book-offline. Its cover is a generated stand-in (`cover-flat`). The official cover and the book files are not included. Format links point at the upstream edition of 20 October 2026. The four archive rows are sample data and all remain on this page.

## Page chrome

- Put the wordmark at the left of the header. Under it, stack “Other editions” and “About & help” as disclosure panels, then a link to the original work. Put the theme toggle at the top right.
- Give the header `z-index` above the book and `pointer-events: none`, then restore pointer events on the wordmark, disclosures, panels, original link, and theme toggle. A zoomed book may pass underneath the header; the controls stay in front and clickable.
- Include a skip link to `#downloads`. The visible page title can be visually hidden when the book itself is the heading.
- The stage is a grid that centers one book. Desktop width is 384px and the height is 1.415 times the width, in a stage with `perspective: 1800px`. At 860px and below, the width is `min(340px, 100vw - 64px)`.
- The resting pose is 5° of pitch and −22° of yaw, with −3° of roll (−2° at 860px and below). Pitch is clamped to −75° through 75°. Yaw wraps to −180° through 180°.

## Closed book

- Drag with the primary pointer only while the book is closed. Movement below 6px (10px for touch) is still a click. Past that threshold, capture the pointer except on touch, where retargeting the capture would cancel the gesture. Horizontal movement yaws at 0.55° per pixel and vertical movement pitches at 0.35° per pixel.
- On release, coast for up to 700ms, multiplying velocity by 0.88 each 16ms, and stop below a combined speed of 0.015. Skip the coast when reduced motion is requested, the gesture was cancelled, or more than 100ms passed since the last move. For 400ms after a drag, swallow the click so the book does not open.
- A click on the closed book, other than on a button, opens it. The cover button, the “Open the book” caption, and `#downloads` do the same. Arrow keys on the cover yaw by 15° or pitch by 10°. Home restores the resting pose.
- While the cover button is focused and the book is closed, `+`, `=`, and `-` zoom by 1.15, and `0` returns to the resting fit. Those keys are handled on the stage, so they also work while focus is inside the open page. Ignore them when Ctrl, Alt, or Meta is held.
- Wheel over the stage zooms (`zoom * exp(-delta * 0.0015)`, delta clamped to ±300, line and page modes converted to pixels). Do not zoom when Ctrl or Meta is held, so a pinch can still scale the page, or when the pointer is over a panel that actually scrolls. A second touch cancels a drag and leaves pinch-zoom available. One-finger dragging does not scroll the page.
- The resting scale is the largest scale at or below 1 that keeps the projected book inside the stage with a 12px margin. A deliberate zoom may go down to 0.55. Its ceiling is the scale at which the cover’s highest and lowest points sit 40px past the screen, or, while open, the scale at which the page height is the screen plus that same strip on both ends. Past the resting scale, mark the page so the book may paint outside the stage and reach the screen edges. An open page taller than the screen is shifted until the overflow is split evenly above and below.
- Prefer a WebGL2 drawing of the closed book: one mesh with rounded outer corners, a flat cover texture, a spine label, and page edges. Use the default antialiasing, draw at extra resolution up to 3× within a pixel budget, and filter the cover with mipmaps and anisotropy. Redraw during a drag or a transform and stop once the book is still. Hide that canvas if the context is lost or the shaders fail, and show the CSS faces instead.
- A cover with `cover-flat` is the board face and is uploaded as-is. Without that class, crop the publisher bleed before upload (about 4.66% from the left and 0.71% from the top) and use the matching CSS image offsets.
- Suspend the WebGL canvas for the opening turn and while the book stays open. Resume it after the book has closed.

## Opening

- Opening sets `is-open`, makes the download page active (`inert` and `aria-hidden` removed), and makes the cover inert. The cover’s `aria-expanded` follows. The caption becomes “Close the book” and its arrow turns 180°. Closing reverses this.
- The turn takes 1100ms with `cubic-bezier(.22, .8, .2, 1)`. On a wide screen the book translates by half its width, the front board swings to −180°, and the spread is the inside cover plus the download page. When the turn finishes, drop the 3D transforms (`is-open-flat`) so the type and icons paint as ordinary layout at the current zoom.
- At 860px and below, keep a single leaf. The cover swings toward the hinge and gives way to the inside page; the inside page stays hidden until it is a sheet again, including exactly edge-on. Closing uses a separate animation that holds the page until it is thin, then brings the cover back. It is not the opening animation played in reverse. A close that interrupts an opening seeks to the matching point on the close animation. Square the book up for the turn, and restore the resting tilt as it shuts. During the turn, hide the closed book’s thickness so it does not stick out past the page.
- With motion allowed, move focus to the first download link 1100ms after opening, unless a header panel is open. With reduced motion, focus it immediately. Closing returns focus to the cover button. Loading with `#downloads` opens the book without moving focus; a later change to that hash opens it and moves focus.
- Close from the close button, the caption, a click on the inside cover, or Escape. Escape closes an expanded header panel first and returns focus to its summary.

## Download page

- The open right-hand page (the only page on a phone) is headed “Take a copy.” with the close button on that heading. One column lists the formats. Each row is a link: format name, file size, extension, a one-line hint, and a download icon. The colophon sits at the bottom of the page: edition status and date, then language and license.
- The links are real destinations. They stay in the page when JavaScript is off, which is why the closed cover is not required to reach them.

## Edition archive

- “Other editions” is a `<details>` panel. Without JavaScript it opens natively, and the two header panels exclude each other with the `name` attribute. With JavaScript, remove `name`, animate both directions over 280ms, and keep only one panel expanded. A keyboard activation (`detail === 0`) moves focus into the edition search once the panel is open. Reduced motion skips the height animation.
- Reveal the search, date filter, and sort only after JavaScript is available. The no-JavaScript list is the full archive.
- Search is case-insensitive and accent-insensitive. Quoted phrases stay intact; other words may occur anywhere in the row’s `data-search` text. An all-digit term matches a whole word, so `2026` does not match inside a longer number. Date fields are inclusive. When the end date precedes the start date, show the alert, mark the end field invalid, and show no rows and no empty state. Otherwise show “No editions match…” when every row is filtered out.
- The status line is “N of M editions”. Sort by `data-published`, newest first until the button is pressed, then oldest first. The button’s label names the current order.
- A pointer or focus moving outside the header support closes the expanded panel.

## Theme, motion, and no JavaScript

- Default the theme to the system preference. Persist a choice only after the toggle is used (`localStorage` key `grade-book-theme`). If nothing is stored, follow later system changes. The toggle’s name and tooltip always offer the other theme. Update `theme-color` and redraw the closed book.
- Icon-button tooltips use `data-tooltip`, appear after 150ms on hover or focus, and prefer placement above the control.
- `prefers-reduced-motion: reduce` removes transitions and animations, skips the coast, snaps the header panels, and settles the book open or closed immediately.
- Before JavaScript, hide the front board so the download page is the document. Keep the header disclosures on native `<details>`. Leave the theme toggle, the open caption, the close button, and the edition filters hidden; the system theme still applies.

## Files

- [`index.html`](../assets/grade-book/index.html) is the page. Edit it directly. The catalog assembler does not build this reference.
- [`style.css`](../assets/grade-book/style.css) owns layout, the book faces, the leaf animations, and the light and dark tokens.
- [`site.js`](../assets/grade-book/site.js) owns gestures, zoom, opening, the archive, and theme.
- [`book-renderer.js`](../assets/grade-book/book-renderer.js) owns the closed-book WebGL drawing and exposes `window.createBookRenderer`.
- [`cover.png`](../assets/grade-book/cover.png) is the stand-in artwork. [`cover-data.js`](../assets/grade-book/cover-data.js) assigns that image as a data URL before the renderer starts, so opening `index.html` as a file can still upload the texture.
