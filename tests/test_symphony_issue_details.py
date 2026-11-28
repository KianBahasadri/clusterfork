"""Issue context pagination, Markdown safety, and Linear upload authorization."""

from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request

from symphony.__main__ import Handler
from symphony.issue_details import (
    AssetRedirect, CONNECTIONS, asset_url, issue_details, markdown, open_asset,
)


UPLOAD = "https://uploads.linear.app/workspace/file/screenshot.png"


class IssueDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_context_paginates_every_collection_and_renders_markdown(self):
        issue = {
            "description": "# Context\n\n**Description**",
            "project": {"id": "project", "name": "Project", "icon": ":house_with_garden:"},
            "team": {
                "id": "team", "name": "Kian", "key": "KIAN",
                "states": {"nodes": [
                    {"id": "s-done", "name": "Done", "type": "completed", "color": "#5e6ad2", "position": 0, "archivedAt": None},
                    {"id": "s-arch", "name": "Old", "type": "backlog", "color": "#000", "position": 0, "archivedAt": "2026-01-01"},
                    {"id": "s-back", "name": "Backlog", "type": "backlog", "color": "#bec2c8", "position": 1, "archivedAt": None},
                ]},
            },
            **{name: {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": name}}
               for name in CONNECTIONS},
        }
        pages = {name: [{"id": name}] for name in CONNECTIONS}
        pages["comments"] = [{"body": "*Reply*"}]
        pages["documents"] = [{"content": "Document **content**"}]
        pages["attachments"] = [{"url": UPLOAD}]

        async def query(text, **variables):
            self.assertNotIn("mutation", text)
            self.assertEqual(variables["id"], "KIAN-1077")
            if "after" not in variables:
                return {"issue": deepcopy(issue)}
            name = variables["after"]
            self.assertIn(name + "(first: 50, after: $after)", text)
            return {"issue": {name: {
                "nodes": pages[name], "pageInfo": {"hasNextPage": False, "endCursor": None},
            }}}

        linear = SimpleNamespace(query=AsyncMock(side_effect=query))
        context = await issue_details(linear, "KIAN-1077")
        self.assertEqual(linear.query.await_count, len(CONNECTIONS) + 1)
        self.assertIn("<strong>Description</strong>", context["descriptionHtml"])
        self.assertIn("<em>Reply</em>", context["comments"][0]["bodyHtml"])
        self.assertIn("<strong>content</strong>", context["documents"][0]["contentHtml"])
        self.assertEqual(context["project"]["icon"], "🏡")
        self.assertEqual(context["attachments"][0]["url"], asset_url(UPLOAD))
        self.assertEqual(context["children"], [{"id": "children"}])
        self.assertEqual([s["id"] for s in context["team"]["states"]], ["s-back", "s-done"])

    async def test_invalid_and_missing_issues_are_reported(self):
        linear = SimpleNamespace(query=AsyncMock(return_value={"issue": None}))
        for identifier in ("", "kian-1", "../secrets", "KIAN-1\n"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                await issue_details(linear, identifier)
        linear.query.assert_not_called()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            await issue_details(linear, "KIAN-1077")

    def test_markdown_formats_context_and_escapes_active_content(self):
        html = markdown(
            "# Heading\n\n<script>alert(1)</script>\n\n"
            '[bad](javascript:alert%281%29)\n\n<img src=x onerror=alert(1)>\n\n'
            f'![Screenshot]({UPLOAD}?width=900&height=600 "Preview")\n\n'
            f"[Download]({UPLOAD})\n\n"
            "- [x] Complete\n\n~~Removed~~\n\n"
            "| First | Second |\n| :---: | ---: |\n| A | B |"
        )
        self.assertIn("<h1>Heading</h1>", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script", html)
        self.assertIn('href="#harmful-link"', html)
        self.assertIn('src="/api/linear-asset?url=', html)
        self.assertIn('href="/api/linear-asset?url=', html)
        self.assertIn('type="checkbox"', html)
        self.assertIn("<del>Removed</del>", html)
        self.assertIn("<table>", html)
        self.assertIn('class="table-align-center"', html)
        self.assertNotIn("style=", html)
        proxied = asset_url(UPLOAD)
        self.assertEqual(parse_qs(urlsplit(proxied).query)["url"], [UPLOAD])
        self.assertEqual(asset_url("https://example.com/image.png"), "https://example.com/image.png")

    def test_markdown_chat_links_and_unsafe_content(self):
        html = markdown(
            "[Linear](https://linear.app/acme/issue/KIAN-1077) "
            "[CI](https://github.com/acme/project/actions/runs/123) "
            "[docs](../docs/symphony.md)\n\n"
            "**In Progress**\n\n"
            "<script>alert(1)</script> [bad](javascript:alert%281%29)"
        )
        self.assertIn('href="https://linear.app/acme/issue/KIAN-1077"', html)
        self.assertIn('href="https://github.com/acme/project/actions/runs/123"', html)
        self.assertIn('href="../docs/symphony.md"', html)
        self.assertIn("<strong>In Progress</strong>", html)
        self.assertEqual(html.count("<p>"), 3)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn('<a href="javascript:', html)


class IssueHttpTests(unittest.TestCase):
    def test_issue_http_cache_is_private_and_versioned_and_never_caches_errors(self):
        updated = "2026-11-20T12:00:00Z"
        for revision, failure, expected in [
            (updated, False, "private, max-age=31536000, immutable"),
            ("older-version", False, "private, max-age=60"),
            (None, False, "private, max-age=60"),
            (updated, True, "no-store"),
        ]:
            with self.subTest(revision=revision, failure=failure):
                params = {"id": "KIAN-1077"}
                if revision is not None:
                    params["updated"] = revision
                handler = object.__new__(Handler)
                handler.path = "/api/issue?" + urlencode(params)
                handler.server = SimpleNamespace(app=SimpleNamespace(linear=object()))
                handler.wfile = BytesIO()
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                handler.run_async = Mock(return_value={"updatedAt": updated})
                if failure:
                    handler.run_async.side_effect = RuntimeError("Linear unavailable")
                with patch("symphony.__main__.issue_details", new=Mock(return_value=object())):
                    handler.do_GET()
                headers = dict(call.args for call in handler.send_header.call_args_list)
                self.assertEqual(headers["Cache-Control"], expected)
                handler.send_response.assert_called_once_with(503 if failure else 200)

    def test_asset_access_is_limited_to_https_linear_uploads(self):
        with patch("symphony.issue_details.build_opener") as opener:
            for url in ("http://uploads.linear.app/file", "https://example.com/file",
                        "https://uploads.linear.app.evil/file", "file:///etc/passwd",
                        "https://user:password@uploads.linear.app/file",
                        "https://uploads.linear.app:8443/file"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    open_asset("fixture-token", url)
            opener.assert_not_called()
            open_asset("fixture-token", UPLOAD, "bytes=10-20")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, UPLOAD)
            self.assertEqual(request.get_header("Authorization"), "fixture-token")
            self.assertEqual(request.get_header("Range"), "bytes=10-20")

    def test_redirects_drop_the_linear_key_and_reject_http(self):
        request = Request(UPLOAD, headers={"Authorization": "fixture-token", "Range": "bytes=0-9"})
        redirect = AssetRedirect()
        redirected = redirect.redirect_request(request, None, 302, "Found", {}, "https://cdn.example.com/file")
        self.assertIsNone(redirected.get_header("Authorization"))
        self.assertEqual(redirected.get_header("Range"), "bytes=0-9")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            redirect.redirect_request(request, None, 302, "Found", {}, "http://cdn.example.com/file")

    def test_http_asset_response_streams_ranges_without_exposing_credentials(self):
        response = BytesIO(b"partial")
        response.status = 206
        response.headers = {
            "Content-Type": "image/png", "Content-Length": "7",
            "Content-Range": "bytes 10-16/100", "Accept-Ranges": "bytes",
        }
        handler = object.__new__(Handler)
        handler.server = SimpleNamespace(app=SimpleNamespace(linear=SimpleNamespace(token="fixture-token")))
        handler.headers = {"Range": "bytes=10-16"}
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        with patch("symphony.__main__.open_asset", return_value=response) as opener:
            handler.linear_asset(UPLOAD)
        opener.assert_called_once_with("fixture-token", UPLOAD, "bytes=10-16")
        handler.send_response.assert_called_once_with(206)
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers["Content-Range"], "bytes 10-16/100")
        self.assertIn("sandbox", headers["Content-Security-Policy"])
        self.assertNotIn("fixture-token", str(headers))
        self.assertEqual(handler.wfile.getvalue(), b"partial")
