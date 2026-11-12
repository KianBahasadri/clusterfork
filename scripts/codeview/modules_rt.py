"""Drop-in module discovery and registry for codeview.

Contract for <repo>/.codeview/modules/<slug>.py:
  NAME        optional; defaults to filename stem; ^[a-z0-9][a-z0-9-]*$
  DESCRIPTION optional tab tooltip
  register(reg)  required; reg.add_route(method, path, handler). Paths are
                 always namespaced under /m/<NAME>/ by the registry; pass ""
                 for the module's main page (/m/<NAME>/). Handler returns
                 (status:int, body, content_type:str); body may be dict
                 (served as JSON), str, or bytes.

Broken modules never break boot: failures become a broken tab.
"""
from __future__ import annotations

import importlib.util
from html.parser import HTMLParser
import re
import traceback
from pathlib import Path

MODULE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ROUTE_PATH_RE = re.compile(r"^$|^/(?:[A-Za-z0-9._~-]+/?)*$")
ALLOWED_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")


def inline_host_styles(page: str, css: str) -> str:
    """Keep opaque module frames styled without a loopback subresource request."""
    offsets = [0]
    for line in page.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    links = []

    class StylesheetLinks(HTMLParser):
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if (tag == "link" and values.get("href") == "/assets/app.css"
                    and "stylesheet" in (values.get("rel") or "").lower().split()):
                line, column = self.getpos()
                start = offsets[line - 1] + column
                links.append((start, start + len(self.get_starttag_text())))

    StylesheetLinks().feed(page)
    style = '<style data-codeview-host-styles>\n' + css.replace("</style", "<\\/style") + "\n</style>"
    for start, end in reversed(links):
        page = page[:start] + style + page[end:]
    return page


class ModuleError(Exception):
    pass


class RouteRegistry:
    """Collects routes from one module, forced under its namespace."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.routes: dict[tuple[str, str], object] = {}
        self.description = ""

    def add_route(self, method: str, path: str, handler) -> None:
        method = method.upper()
        if method not in ALLOWED_METHODS:
            raise ModuleError(f"unsupported method {method!r}")
        if not ROUTE_PATH_RE.match(path):
            raise ModuleError(f"bad route path {path!r}")
        if not callable(handler):
            raise ModuleError("handler must be callable")
        # Namespace is enforced here; paths canonicalize to trailing-slash-
        # stripped form so "" and "/" are one route (dispatch rstrips too).
        full = f"/m/{self.slug}{path}".rstrip("/") or f"/m/{self.slug}"
        key = (method, full)
        if key in self.routes:
            raise ModuleError(f"duplicate route {method} {full}")
        self.routes[key] = handler


def load_modules(modules_dir: Path) -> list[dict]:
    """Load every *.py under modules_dir sorted by filename.

    Returns dicts: {name, description, file, ok, error,
                    routes: {(verb, path): fn}}
    """
    results: list[dict] = []
    if not modules_dir.is_dir():
        return results
    for file_path in sorted(modules_dir.glob("*.py")):
        entry: dict = {
            "name": file_path.stem,
            "description": "",
            "file": str(file_path),
            "ok": True,
            "error": None,
            "routes": {},
        }
        try:
            spec = importlib.util.spec_from_file_location(
                f"codeview_mod_{file_path.stem}", file_path)
            if spec is None or spec.loader is None:
                raise ModuleError("could not create import spec")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            name = getattr(mod, "NAME", None) or file_path.stem
            if not MODULE_NAME_RE.match(name):
                raise ModuleError(
                    f"invalid NAME {name!r} (must match ^[a-z0-9][a-z0-9-]*$)")
            reg_fn = getattr(mod, "register", None)
            if not callable(reg_fn):
                raise ModuleError("module has no callable register(reg)")
            reg = RouteRegistry(name)
            reg_fn(reg)
            entry.update({
                "name": name,
                "description": str(getattr(mod, "DESCRIPTION", "") or ""),
                "routes": reg.routes,
            })
        except Exception as exc:  # noqa: BLE001 — broken tabs are the feature
            entry["ok"] = False
            entry["error"] = "".join(traceback.format_exception(exc)).strip()
        results.append(entry)
    return results
