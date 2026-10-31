# Codeview modules

Trusted `<repo>/.codeview/modules/*.py` files become [dashboard](dashboard.md) tabs.
They execute with server permissions; browser framing does not sandbox Python.

## API contract

```python
NAME = "my-tab"          # optional; filename stem by default
DESCRIPTION = "tooltip"  # optional

def register(reg):
    def page(req):
        return (200, "<h1>My tab</h1>", "text/html; charset=utf-8")
    reg.add_route("GET", "", page)       # /m/my-tab/
```

Unique names match `^[a-z0-9][a-z0-9-]*$`; loading follows filename order. Routes
stay under `/m/<NAME>/`; `""` and `"/"` share the main page. Verbs: GET/POST/PUT/
DELETE/PATCH. URL-safe segments cannot escape the namespace; duplicates fail.
Handlers return `(status, body, content_type)`; dicts become JSON, strings/bytes
use supplied type. Requests expose `.method`, `.path` (with query), `.query`
(last value/key), `.headers`, `.json()`, `.text()`. Read body once: accessors consume
its stream. Import/registration failure yields a broken tab/traceback without
preventing startup. Modules own routes/pages; no frontend framework is required.

## Frames and theme

Frames use `sandbox="allow-scripts allow-forms"` without `allow-same-origin`:
opaque origin, no direct parent/storage access. Guard storage/clipboard rejection.
Read initial `?theme=dark|light`, then `{type: "codeview-theme", theme}` messages
from `window.parent`. Direct pages may fall back to guarded storage/OS preference;
ignoring handoff leaves rendering functional but theme unsynchronized.
The [skill](../skills/add-codeview-module/SKILL.md) owns full templates, bootstrap,
CSS vocabulary and workflow; follow existing target modules when available.

## Reloading and validation

Watcher filename/mtime changes trigger restart after about five quiet seconds,
with listening port retained and a 30-second minimum restart interval. Browsers
reload on startup. Without watch, use `codeview restart`; `reload` scans only git data.

Normal [suite](conventions.md#local-validation): `test_codeview_modules.py` and
`test_codeview_router.py` cover registration/dispatch, `test_codeview_custom_modules.py`
covers shipped tabs, and `test_codeview_wrapper.py` covers daemon control.
