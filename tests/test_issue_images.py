"""Description parsing, untrusted downloads, and native visual input transport."""

import base64
import copy
import io
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]

from agent_session import run_agent
from call_budget import CallBudget
from dispatch_clients import Linear
from issue_images import (ImageContextError, MAX_IMAGE_BYTES, MAX_IMAGES, MAX_PIXELS,
                          capture_images, discover_images, download_image, image_digest,
                          image_prompt, materialize_images, public_address, validate_image)

FIXTURES = ROOT / "tests/fixtures/issue-images"
DESCRIPTION = ('Match the screenshots.\n\nCurrent:\n![Screen A](https://uploads.linear.app/current "Before")'
               '\n\nDesired:\n![Screen B][target]\n\nPreserve the page heading.\n\n'
               '[target]: https://uploads.linear.app/target "After"')


def screenshots():
    linear = Mock(spec=Linear)
    linear.download_image.side_effect = lambda url, **kwargs: (
        (FIXTURES / ("current.png" if url.endswith("current") else "target.png")).read_bytes(), "image/png")
    return linear


class ImageContextTests(unittest.TestCase):
    def test_inline_reference_and_html_images_keep_order_and_caption_associations(self):
        description = (DESCRIPTION + '\n\n<img src="https://example.com/a?x=1&amp;y=2" alt="Third" title="Detail">'
                       '\n\n`![code](https://example.com/ignored)`\n\n'
                       '```md\n![fenced](https://example.com/ignored)\n```\n'
                       '\\![escaped](https://example.com/ignored)')
        refs = discover_images(description)
        self.assertEqual([ref["alt"] for ref in refs], ["Screen A", "Screen B", "Third"])
        self.assertEqual([ref["title"] for ref in refs], ["Before", "After", "Detail"])
        self.assertEqual(refs[2]["source"], "https://example.com/a?x=1&y=2")

    def test_multiple_images_contain_screenshot_details_absent_from_text(self):
        linear = screenshots()
        with CallBudget(self, linear=linear, exact={"linear.download_image": 2}):
            images = capture_images(DESCRIPTION, linear.download_image)
        self.assertNotIn("Launch preview", DESCRIPTION + image_prompt(images))
        self.assertEqual([image["number"] for image in images], [1, 2])
        self.assertEqual([image["title"] for image in images], ["Before", "After"])
        self.assertNotEqual(images[0]["sha256"], images[1]["sha256"])
        self.assertEqual(base64.b64decode(images[1]["data"]), (FIXTURES / "target.png").read_bytes())

    def test_no_images_needs_no_downloads(self):
        linear = screenshots()
        for description in (None, "", "Fix the documented flag", "`![example](https://example.com/x)`"):
            with self.subTest(description=description), CallBudget(self, linear=linear):
                self.assertEqual(capture_images(description, linear.download_image), [])

    def test_duplicate_sources_download_once_and_attach_each_occurrence(self):
        linear = screenshots()
        with CallBudget(self, linear=linear, exact={"linear.download_image": 1}):
            images = capture_images('![A](https://uploads.linear.app/current)\n![B](https://uploads.linear.app/current)',
                                    linear.download_image)
        self.assertEqual([image["alt"] for image in images], ["A", "B"])

    def test_failed_images_are_numbered_without_urls_or_remote_exception_messages(self):
        linear = screenshots()
        linear.download_image.side_effect = OSError("https://example.com/?token=private lin_api_private")
        with CallBudget(self, linear=linear, exact={"linear.download_image": 2}), \
             self.assertRaises(ImageContextError) as caught:
            capture_images(DESCRIPTION, linear.download_image)
        message = str(caught.exception)
        self.assertIn("Description image 1", message)
        self.assertIn("Description image 2", message)
        self.assertNotIn("private", message)
        self.assertNotIn("https", message)

    def test_unsupported_urls_are_discovered_and_never_requested(self):
        linear = screenshots()
        for url in ("data:image/png;base64,AAAA", "file:///etc/passwd", "http://example.com/a",
                    "https://user:password@example.com/a", "https://example.com:8443/a", "javascript:alert(1)"):
            with self.subTest(url=url), CallBudget(self, linear=linear), self.assertRaises(ImageContextError):
                capture_images(f"![Screen](<{url}>)", linear.download_image)

    def test_image_count_and_total_bytes_limits_are_enforced(self):
        linear = screenshots()
        with CallBudget(self, linear=linear), self.assertRaisesRegex(ImageContextError, "at most 6"):
            capture_images("\n".join(f"![Screen](https://example.com/{i})" for i in range(MAX_IMAGES + 1)),
                           linear.download_image)
        linear.download_image.return_value = b"x" * MAX_IMAGE_BYTES, "image/png"
        linear.download_image.side_effect = None
        with patch("issue_images.validate_image"), \
             CallBudget(self, linear=linear, exact={"linear.download_image": 3}), \
             self.assertRaisesRegex(ImageContextError, "12 MiB"):
            capture_images("\n".join(f"![Screen](https://example.com/{i})" for i in range(3)), linear.download_image)

    def test_corrupt_mismatched_animated_and_oversized_images_are_rejected(self):
        png = (FIXTURES / "current.png").read_bytes()
        for raw, mime, reason in ((png, "image/jpeg", "does not match"),
                                  (png, "image/svg+xml", "unsupported"),
                                  (b"not an image", "image/png", "corrupt"),
                                  (png[:50], "image/png", "corrupt"),
                                  (b"x" * (MAX_IMAGE_BYTES + 1), "image/png", "5 MiB")):
            with self.subTest(reason=reason), self.assertRaisesRegex(ImageContextError, reason):
                validate_image(raw, mime)
        for size in ((8193, 1), (5000, MAX_PIXELS // 5000 + 1)):
            output = io.BytesIO()
            Image.new("RGB", size).save(output, format="PNG")
            with self.assertRaisesRegex(ImageContextError, "dimensions"):
                validate_image(output.getvalue(), "image/png")
        output = io.BytesIO()
        Image.new("RGB", (2, 2)).save(output, format="WEBP", save_all=True,
                                      append_images=[Image.new("RGB", (2, 2), "red")], duration=100)
        with self.assertRaisesRegex(ImageContextError, "animated"):
            validate_image(output.getvalue(), "image/webp")

    def test_supported_formats_decode_successfully(self):
        for kind, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            output = io.BytesIO()
            Image.new("RGB", (4, 4), "red").save(output, format=kind)
            validate_image(output.getvalue(), mime)

    def test_runner_checks_manifest_and_bytes_before_creating_any_visual_inputs(self):
        images = capture_images(DESCRIPTION, screenshots().download_image)
        config = {"images": images, "binding": {"images_sha": image_digest(images)}}
        for change in ("manifest", "bytes", "size", "number"):
            broken = copy.deepcopy(config)
            if change == "manifest":
                broken["images"].reverse()
            elif change == "bytes":
                broken["images"][0]["data"] = base64.b64encode(b"changed").decode()
            else:
                broken["images"][0][change] = -1
                broken["binding"]["images_sha"] = image_digest(broken["images"])
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory, \
                 self.assertRaises(ImageContextError):
                materialize_images(broken, directory)
        with tempfile.TemporaryDirectory() as directory:
            paths = materialize_images(config, directory)
            self.assertEqual([Path(path).read_bytes() for path in paths],
                             [(FIXTURES / name).read_bytes() for name in ("current.png", "target.png")])
            self.assertTrue(all(Path(path).stat().st_mode & 0o777 == 0o400 for path in paths))
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(materialize_images({}, directory), [])
            self.assertFalse(list(Path(directory).iterdir()))

    def test_coding_turn_uses_native_local_images_in_order(self):
        images = capture_images(DESCRIPTION, screenshots().download_image)
        config = {"images": images, "readiness": {"images_sha": image_digest(images)},
                  "model": "gpt-6.1-sol", "effort": "max", "issue": {"id": "issue"}}
        server = Mock()
        server.call.side_effect = [{"thread": {"id": "thread"}}, {"turn": {"id": "turn"}}]
        server.next_event.side_effect = [{"method": "turn/completed", "params": {
            "threadId": "thread", "turn": {"id": "turn", "status": "completed"}}}, None]
        broker = Mock()
        with tempfile.TemporaryDirectory() as directory:
            paths = materialize_images(config, directory)
            run_agent(broker, config, directory, directory, DESCRIPTION + image_prompt(images), {},
                      image_paths=paths, server_factory=lambda *args, **kwargs: server)
            inputs = server.call.call_args_list[1].args[1]["input"]
            self.assertEqual(inputs[0]["type"], "text")
            self.assertIn(DESCRIPTION, inputs[0]["text"])
            self.assertEqual(inputs[1:], [{"type": "localImage", "path": path} for path in paths])
            self.assertEqual(Path(inputs[2]["path"]).read_bytes(), (FIXTURES / "target.png").read_bytes())


class DownloadTests(unittest.TestCase):
    def response(self, status=200, headers=None, body=b"pixels"):
        response = Mock()
        response.status = status
        headers = {"Content-Type": "image/png", **(headers or {})}
        response.getheader.side_effect = lambda key, default=None: headers.get(key, default)
        response.read1.side_effect = [body, b""]
        return response

    def fetch(self, responses, url="https://uploads.linear.app/image"):
        connections = [Mock() for _ in responses]
        for connection, response in zip(connections, responses):
            connection.getresponse.return_value = response
        auth = Mock(return_value="lin_api_private")
        with patch("issue_images.HTTPSConnection", side_effect=connections) as factory, \
             patch("issue_images.public_address", return_value="93.184.216.34"):
            result = download_image(url, auth)
        return result, connections, factory, auth

    def test_redirects_revalidate_and_never_forward_linear_authorization(self):
        result, connections, factory, auth = self.fetch([
            self.response(302, {"Location": "https://example.com/image?signature=private"}), self.response()])
        self.assertEqual(result, (b"pixels", "image/png"))
        self.assertEqual(factory.call_args_list[0].args, ("uploads.linear.app",))
        self.assertEqual(factory.call_args_list[1].args, ("example.com",))
        auth.assert_called_once()
        self.assertEqual(connections[0].request.call_args.kwargs["headers"]["Authorization"], "lin_api_private")
        self.assertNotIn("Authorization", connections[1].request.call_args.kwargs["headers"])
        self.assertTrue(all(connection.close.called for connection in connections))
        with patch("issue_images.socket.create_connection") as connect:
            connections[0]._create_connection(("untrusted-dns", 443), 3)
            connect.assert_called_once_with(("93.184.216.34", 443), 3, None)

    def test_public_urls_never_read_broker_credentials(self):
        _, _, _, auth = self.fetch([self.response()], "https://example.com/image")
        auth.assert_not_called()

    def test_private_destinations_and_mixed_dns_answers_are_rejected(self):
        for addresses in (["127.0.0.1"], ["169.254.169.254"], ["::1"], ["10.0.0.1"],
                          ["93.184.216.34", "192.168.0.1"]):
            with self.subTest(addresses=addresses), patch("issue_images.socket.getaddrinfo", return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses]), \
                 self.assertRaisesRegex(ImageContextError, "non-public"):
                public_address("example.com")

    def test_failed_redirect_unsupported_type_and_oversize_are_clear(self):
        responses = [(self.response(403), "HTTP 403"),
                     (self.response(headers={"Content-Type": "text/html"}), "unsupported media"),
                     (self.response(headers={"Content-Length": str(MAX_IMAGE_BYTES + 1)}), "oversized"),
                     (self.response(headers={"Content-Encoding": "gzip"}), "encoding"),
                     (self.response(302, {"Location": "http://example.com/x"}), "unsupported URL")]
        for response, reason in responses:
            with self.subTest(reason=reason), self.assertRaisesRegex(ImageContextError, reason):
                self.fetch([response])
        with self.assertRaisesRegex(ImageContextError, "redirect limit"):
            self.fetch([self.response(302, {"Location": "/next"}) for _ in range(4)])

    def test_streaming_limit_and_timeout_do_not_need_content_length(self):
        response = self.response(body=b"x" * (MAX_IMAGE_BYTES + 1))
        with self.assertRaisesRegex(ImageContextError, "oversized"):
            self.fetch([response])
        with patch("issue_images.time.monotonic", side_effect=[0, 13]), \
             patch("issue_images.public_address", return_value="93.184.216.34"), \
             self.assertRaisesRegex(ImageContextError, "timed out"):
            download_image("https://example.com/image", Mock())

    def test_linear_download_uses_existing_broker_authorization(self):
        linear = Linear(token="lin_api_private")
        with patch("issue_images.download_image", return_value=(b"pixels", "image/png")) as download:
            linear.download_image("https://uploads.linear.app/image", deadline=123)
        self.assertEqual(download.call_args.args[1](), "lin_api_private")
        self.assertEqual(download.call_args.kwargs, {"deadline": 123})


if __name__ == "__main__":
    unittest.main()
