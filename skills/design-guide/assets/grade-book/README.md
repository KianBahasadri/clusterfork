# GRADE Book download site — application reference

This directory is a self-contained snapshot of the download site from [grade-book-offline](https://github.com/KianBahasadri/grade-book-offline) (`4765d01`). Open [`index.html`](index.html) directly in a browser. It needs no build step and no local server.

For the interaction rules this page demonstrates, read [`../../references/grade-book.md`](../../references/grade-book.md).

`style.css`, `site.js`, and `book-renderer.js` are the site’s own files. `index.html` is filled with four sample edition rows so search, date filtering, and sorting work offline. Those rows all stay on this page. The format buttons link to the upstream edition published on 20 October 2026.

`cover.png` is a generated stand-in, and `cover-data.js` inlines it so the WebGL texture can upload when this folder is opened as a file. The official cover, and the book files themselves, are not part of this reference. IBM Plex Sans is OFL (`FONT-LICENSE.txt`). The icon paths are Lucide (`LUCIDE-LICENSE.txt`). The favicon is the site’s book mark on the same navy.

Theme choice is stored in `localStorage` under `grade-book-theme`, the same key as the upstream site.
