# Codeview modules

A `*.py` file in `<repo>/.codeview/modules/` becomes a dashboard tab.
Modules execute as ordinary Python from the repository; only run modules you
trust to run with the server's permissions. Dashboard usage and this repo's
shipped tabs are listed in [Dashboard](dashboard.md).

## API contract

```python
NAME = "my-tab"          # optional; defaults to filename stem
DESCRIPTION = "tooltip"  # optional

def register(reg):
    def page(req):
        return (200, "<h1>My tab</h1>", "text/html; charset=utf-8")
    reg.add_route("GET", "", page)       # /m/my-tab/
```

Names must match `^[a-z0-9][a-z0-9-]*$`. Modules load in filename order;
choose unique names. Routes are forced under `/m/<NAME>/`, and `""` and
`"/"` identify the same main page. Supported registration verbs are GET,
POST, PUT, DELETE, and PATCH. Paths use URL-safe segments without escaping
the namespace; duplicate routes within a registry are rejected.

A handler returns `(status, body, content_type)`. A `dict` body is JSON;
strings and bytes are sent with the supplied content type. The request has
`.method`, `.path` (including query), `.query` (last value per key),
`.headers`, `.json()`, and `.text()`. Read the body once: both accessors
consume the input stream.

An import or registration failure becomes a broken tab with its traceback
rather than preventing the dashboard from starting. The module namespace
owns its page and HTTP routes; no frontend framework is required.

## Frames and theme

The host frames module documents with `sandbox="allow-scripts allow-forms"`
and without `allow-same-origin`. The frame has an opaque origin: stored
browser state and the parent document are not directly accessible. Guard
`localStorage` access and clipboard writes against rejection.

The host supplies the current theme as `?theme=dark` or `?theme=light` for
first paint and sends `{type: "codeview-theme", theme}` messages on toggles.
Read the query value, then listen for messages from `window.parent`.
A directly opened module page can fall back to guarded storage and the OS
color-scheme preference. A module that ignores the handoff still renders,
but does not follow the host's theme.

The [add-codeview-module skill](../skills/add-codeview-module/SKILL.md) owns
the full page template, theme bootstrap, host CSS vocabulary, and build
workflow. Follow a target repository's existing modules where available.

## Reloading and validation

The watcher fingerprints module filenames/mtimes. After roughly five seconds
without further module changes, it restarts the server while holding the
listening port; a 30-second minimum restart interval limits repeated restarts.
Browsers reload after the new process starts. With watching disabled, use
`codeview restart`. `codeview reload` only refreshes git data.

The core `test_codeview_modules.py` and `test_codeview_router.py` exercise
registration and HTTP dispatch. `test_codeview_custom_modules.py` covers
this repository's modules and `test_codeview_wrapper.py` covers daemon
control. Use the [normal suite command](conventions.md#local-validation).
