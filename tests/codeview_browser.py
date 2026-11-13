"""Real-browser Codeview checks, using disposable data and offline module snapshots.

Run with uv run --no-project --with-requirements automation/controller/requirements.txt
--with playwright python tests/codeview_browser.py. Chromium must be installed.
"""
import csv
import io
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from test_codeview_custom_modules import load_module
from test_codeview_deployments import resource
from test_codeview_router import REPO_ROOT, ServerBootTestCase as Fixture, git, srv


def prepare():
    for index in range(45):
        path = Fixture.repo / ("scripts" if index % 2 else "src") / f"sample_{index:02}.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"# TODO: sample {index}\n" + "value = 1\n" * (index + 1))
    (Fixture.repo / "old.py").write_text("old = True\n")
    (Fixture.repo / "package.json").write_text(json.dumps({
        "name": "browser-fixture", "dependencies": {"alpha": "^1", "missing": "^2"}}))
    (Fixture.repo / "package-lock.json").write_text(json.dumps({"packages": {
        "node_modules/alpha": {"version": "1.0.0"},
        "node_modules/indirect": {"version": "3.0.0"}}}))
    infra = Fixture.repo / "infra"
    infra.mkdir()
    (infra / "main.tf").write_text('resource "azurerm_resource_group" "example" {}\n')
    git(["add", "-A"], Fixture.repo)
    git(["-c", "user.email=test@example.test", "-c", "user.name=Example Author", "commit", "-qm", "Add browser fixture"], Fixture.repo)
    (Fixture.repo / "old.py").unlink()
    git(["add", "-A"], Fixture.repo)
    git(["-c", "user.email=test@example.test", "-c", "user.name=Example Author", "commit", "-qm", "Delete historical file"], Fixture.repo)
    srv.scan_all(Fixture.repo, Fixture.shape, Fixture.codeview_dir, Fixture.state, force=True)
    modules = []
    for name in ["scripts", "skills", "statusline", "deployments"]:
        mod = load_module(name, REPO_ROOT / ".codeview" / "modules" / f"{name}.py")
        with patch("pathlib.Path.home", return_value=Fixture.repo / "home"):
            if name == "deployments":
                snapshot = {"serial": 1, "resources": [resource("azurerm_resource_group", "example", {
                    "name": "example", "location": "eastus"})]}
                with patch.object(mod, "read_state", return_value=(snapshot, "Offline snapshot")):
                    data = mod.scan(Fixture.repo)
            else:
                data = mod.scan(REPO_ROOT)
        html = mod.render(data)
        route = (True, name, lambda req, html=html: (200, html, "text/html; charset=utf-8"), None)
        Fixture.httpd.app["module_routes"][("GET", f"/m/{name}")] = route
        modules.append({"name": name, "description": name, "ok": True})
    Fixture.httpd.app["modules"] = modules
    srv.log_line(Fixture.state, "scan: failed fixture check")
    srv.log_line(Fixture.state, "watch: fixture ready")


def tab(page, name):
    page.evaluate("""async name => {
      showPanel(name);
      if (name === 'overview') await refresh();
      else await window['render' + name[0].toUpperCase() + name.slice(1)]();
    }""", name)


def choose(page, label, value):
    field = page.get_by_role("combobox", name=label, exact=True)
    field.click()
    field.fill(value)
    bounds = field.evaluate("""input => {
      const popup = document.getElementById(input.getAttribute('aria-controls')).getBoundingClientRect();
      return {left: popup.left, right: popup.right, viewport: document.documentElement.clientWidth};
    }""")
    assert bounds["left"] >= 11.9 and bounds["right"] <= bounds["viewport"] - 11.9, (label, bounds)
    page.get_by_role("option", name=value, exact=True).click()
    assert field.get_attribute("aria-expanded") == "false"


def visible_rows(page, table):
    return page.locator(f"#{table} tbody tr:not([hidden])")


def main():
    chromium = shutil.which("chromium") or shutil.which("google-chrome")
    if not chromium:
        raise RuntimeError("Chromium is required for the browser checks")
    Fixture.setUpClass()
    try:
        prepare()
        output = Path(tempfile.mkdtemp(prefix="codeview-browser-"))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=chromium, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
            context.route("https://fonts.googleapis.com/**", lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(10000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{Fixture.port}", wait_until="domcontentloaded")
            page.wait_for_selector(".overview-board")

            # Overview rankings lead to a filtered file collection.
            page.locator("[data-overview-directories] [data-rank='src']").click()
            page.wait_for_selector("#file-table")
            assert all(value == "src" for value in visible_rows(page, "file-table").evaluate_all("rows => rows.map(row => row.dataset.directory)"))
            page.get_by_role("button", name="Reset", exact=True).click()
            assert visible_rows(page, "file-table").count() == 25
            page.get_by_role("button", name="Next page", exact=True).click()
            assert "26–" in page.locator(".collection-range:visible").inner_text()
            choose(page, "Columns", "All metrics")
            assert page.locator("#file-table thead th:not([hidden])").count() == 10
            choose(page, "Columns", "Summary")
            assert page.locator("#file-table thead th:not([hidden])").count() == 5
            page.locator("#file-filter").fill("sample_01")
            assert visible_rows(page, "file-table").count() == 1
            with page.expect_download() as export:
                page.get_by_role("button", name="Export CSV", exact=True).click()
            records = list(csv.reader(io.StringIO(Path(export.value.path()).read_text())))
            assert len(records) == 2 and records[0] == ["path", "lang", "lines", "code", "complexity"], records
            field = page.get_by_role("combobox", name="Language", exact=True)
            field.click()
            field.fill("no such language")
            assert page.get_by_text("No matching options", exact=True).is_visible()
            field.press("Escape")
            assert field.input_value() == "All languages"
            field.focus()
            field.press("ArrowDown")
            field.press("ArrowDown")
            field.press("Enter")
            page.get_by_role("button", name="Reset", exact=True).click()

            # File inspection preserves collection filters and exposes source search.
            page.locator("#file-filter").fill("sample_01")
            trigger = page.locator("#file-table .table-row-trigger:visible").first
            trigger.click()
            page.wait_for_selector("#source-filter")
            page.locator("#source-filter").fill("TODO")
            assert page.locator(".source-line.is-match").count() == 1
            assert "1 of 1" in page.locator("#source-matches").inner_text()
            assert page.locator(".stats-group[open]").count() == 1
            page.get_by_role("button", name="Copy path", exact=True).click()
            assert "Copied to clipboard" in page.locator("#fileDialog .copy-status").inner_text()
            page.keyboard.press("Escape")
            page.wait_for_function("!document.querySelector('#fileDialog').open && document.activeElement.matches('#file-table .table-row-trigger')")
            assert page.locator("#file-filter").input_value() == "sample_01"
            assert trigger.evaluate("element => document.activeElement === element")

            tab(page, "history")
            page.locator("#history-table .table-row-trigger:visible").first.click()
            assert page.get_by_role("dialog", name="Delete historical file", exact=True).is_visible()
            page.keyboard.press("Escape")
            tab(page, "churn")
            choose(page, "Files", "Historical")
            assert visible_rows(page, "churn-table").count() == 1
            page.locator("#churn-table .table-row-trigger:visible").click()
            assert page.get_by_role("dialog", name="old.py", exact=True).is_visible()
            page.keyboard.press("Escape")
            tab(page, "deps")
            choose(page, "Scope", "Lock only")
            assert visible_rows(page, "deps-table").count() == 1
            assert "indirect" in visible_rows(page, "deps-table").inner_text()
            tab(page, "logs")
            choose(page, "Events", "Errors / failures")
            assert "failed fixture check" in page.locator(".log-lines").inner_text()
            page.get_by_role("button", name="Pause live", exact=True).click()
            assert page.get_by_role("button", name="Resume live", exact=True).is_visible()
            page.get_by_role("button", name="Resume live", exact=True).click()

            # Every core view fits desktop, zoom-equivalent, and phone widths in both themes.
            checked = 0
            for width in [1920, 960, 768, 375, 320]:
                page.set_viewport_size({"width": width, "height": 1000})
                for theme in ["dark", "light"]:
                    page.evaluate("theme => applyTheme(theme)", theme)
                    for name in ["overview", "history", "churn", "files", "tree", "deps", "logs"]:
                        tab(page, name)
                        dimensions = page.evaluate("({viewport: document.documentElement.clientWidth, page: document.documentElement.scrollWidth})")
                        assert dimensions["page"] <= dimensions["viewport"], (width, theme, name, dimensions)
                        assert page.locator(".rank-list:visible").evaluate_all("lists => lists.every(list => list.getBoundingClientRect().width <= 560.1)")
                        checked += 1
                        if width in [1920, 320] and theme == "dark" and name in ["overview", "churn", "files"]:
                            page.screenshot(path=str(output / f"{name}-{width}.png"), full_page=True)

            mobile = browser.new_context(viewport={"width": 320, "height": 900}, is_mobile=True, has_touch=True)
            mobile.route("https://fonts.googleapis.com/**", lambda route: route.abort())
            phone = mobile.new_page()
            phone.on("pageerror", lambda error: errors.append(str(error)))
            phone.goto(f"http://127.0.0.1:{Fixture.port}", wait_until="domcontentloaded")
            phone.wait_for_selector(".overview-board")
            for theme in ["dark", "light"]:
                phone.evaluate("theme => applyTheme(theme)", theme)
                for name in ["overview", "history", "churn", "files", "tree", "deps", "logs"]:
                    tab(phone, name)
                    assert phone.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), ("touch", theme, name)
                    checked += 1
            mobile.close()

            # Shipped modules run in their real opaque frames, with no external CSS/JS fetch.
            page.set_viewport_size({"width": 1600, "height": 1000})
            for name in ["scripts", "skills", "statusline", "deployments"]:
                page.evaluate("name => showPanel('m:' + name)", name)
                frame = page.frame_locator(f'iframe[title="{name}"]')
                frame.locator(".dashboard-layout").wait_for()
                if name != "deployments":
                    frame.locator(".module-page").wait_for()
                    row = frame.locator(".table-row-trigger:visible").first
                    row.click()
                    assert frame.get_by_role("dialog").is_visible()
                    copy = frame.locator("dialog [data-copy]")
                    if copy.count():
                        copy.first.click()
                        assert "Copied to clipboard" in frame.locator("dialog .copy-status").inner_text()
                    frame.get_by_role("button", name="Close", exact=True).click()
                    assert row.evaluate("element => document.activeElement === element")
                    frame.locator(".data-table:visible .sort-th-btn").first.click()
                    choose(frame, "Columns", "All fields")
                    choose(frame, "Columns", "Summary")
                    choose(frame, "Section", "All sections")
                    search = frame.get_by_role("searchbox")
                    search.fill("nothing matches this query")
                    assert frame.get_by_role("button", name="Reset filters", exact=True).is_visible()
                    frame.get_by_role("button", name="Reset filters", exact=True).click()
                else:
                    frame.get_by_role("combobox", name="Stack", exact=True).wait_for()
                    choose(frame, "State", "Recorded in state")
                    assert frame.locator(".deployments-resource:not([hidden])").count() == 1
                    frame.get_by_role("button", name="Expand visible", exact=True).click()
                    assert frame.locator(".deployments-resource[open]").count() == 1
                    frame.get_by_role("button", name="Collapse all", exact=True).click()
                for theme in ["dark", "light"]:
                    page.evaluate("theme => applyTheme(theme)", theme)
                    frame.locator(f'html[data-theme="{theme}"]').wait_for()
                    for width in [1600, 375, 320]:
                        page.set_viewport_size({"width": width, "height": 1000})
                        dimensions = frame.locator("html").evaluate("element => ({viewport: element.clientWidth, page: element.scrollWidth})")
                        assert dimensions["page"] <= dimensions["viewport"], (name, theme, width, dimensions)
                page.set_viewport_size({"width": 1600, "height": 1000})
                page.screenshot(path=str(output / f"module-{name}.png"), full_page=True)
            assert not errors, errors
            browser.close()
        print(f"Passed {checked} core view/theme/viewport checks, collection interactions, file inspection, and all four opaque module frames.")
        print(f"Screenshots: {output}")
    finally:
        Fixture.tearDownClass()


if __name__ == "__main__":
    main()
