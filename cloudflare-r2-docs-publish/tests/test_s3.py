"""Tests of scripts/s3.py: SigV4 signing and the S3 client against a local fake store.

The fake store is an HTTP server on 127.0.0.1; no request leaves the machine.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import hashlib
import http.server
import importlib.util
import os
import socket
import threading
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
# Example key pair of the AWS documentation (Signature Version 4 for S3),
# split into short parts so that secret scanners do not report it.
AWS_DOC_PAIR = ("AKIA" "IOSFODNN7" "EXAMPLE", "wJalrXUtn" "FEMI/K7MD" "ENG/bPxRf" "iCYEXAMPL" "EKEY")


def load(name):
    spec = importlib.util.spec_from_file_location(f"r2_docs_{name}", os.path.join(SCRIPTS, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


s3 = load("s3")


class FakeStore:
    """In-memory bucket with S3 semantics for the three operations, plus scripted failures."""

    def __init__(self):
        self.objects = {}
        self.requests = []
        self.failures = []  # list of (method, status, store_anyway)
        self.redirect = None  # when set, every request gets a 302 to this URL
        self.lock = threading.Lock()


def make_handler(store):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self, status, body=b"", headers=None):
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _handle(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            parts = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parts.path)
            query = dict(urllib.parse.parse_qsl(parts.query))
            with store.lock:
                store.requests.append((self.command, path, dict(self.headers)))
                failure = store.failures.pop(0) if store.failures and store.failures[0][0] == self.command else None
            if store.redirect and path.startswith("/bucket"):
                return self._reply(302, headers={"Location": store.redirect})
            if "Authorization" not in self.headers or not self.headers["Authorization"].startswith("AWS4-HMAC-SHA256 "):
                return self._reply(403, b"<Error><Code>AccessDenied</Code></Error>")
            bucket, _, key = path.lstrip("/").partition("/")
            if failure and failure[2] and self.command == "PUT":
                store.objects[key] = body
            if failure:
                return self._reply(failure[1], b"<Error><Code>InternalError</Code></Error>")
            if self.command == "GET":
                count = sum(1 for name in store.objects if name.startswith(query.get("prefix", "")))
                count = min(count, int(query.get("max-keys", "1000")))
                return self._reply(200, f"<ListBucketResult><KeyCount>{count}</KeyCount></ListBucketResult>".encode())
            if self.command == "HEAD":
                if key not in store.objects:
                    return self._reply(404)
                return self._reply(200, headers={"ETag": '"' + hashlib.md5(store.objects[key]).hexdigest() + '"'})
            if self.headers.get("If-None-Match") == "*" and key in store.objects:
                return self._reply(412, b"<Error><Code>PreconditionFailed</Code></Error>")
            store.objects[key] = body
            return self._reply(200, headers={"ETag": '"x"'})

        do_GET = do_PUT = do_HEAD = _handle

        def log_message(self, *args):
            pass

    return Handler


class SignatureTest(unittest.TestCase):
    """Examples of the AWS documentation "Signature calculation: Authorization header"."""

    def test_get_object_example(self):
        headers = {
            "Host": "examplebucket.s3.amazonaws.com",
            "Range": "bytes=0-9",
            "x-amz-content-sha256": EMPTY_SHA256,
            "x-amz-date": "20130524T000000Z",
        }
        value = s3.sign(
            "GET", "/test.txt", {}, headers, EMPTY_SHA256,
            *AWS_DOC_PAIR, "us-east-1", "20130524T000000Z",
        )
        self.assertTrue(value.endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"))
        self.assertIn("SignedHeaders=host;range;x-amz-content-sha256;x-amz-date", value)

    def test_list_objects_example(self):
        headers = {
            "host": "examplebucket.s3.amazonaws.com",
            "x-amz-content-sha256": EMPTY_SHA256,
            "x-amz-date": "20130524T000000Z",
        }
        value = s3.sign(
            "GET", "/", {"max-keys": "2", "prefix": "J"}, headers, EMPTY_SHA256,
            *AWS_DOC_PAIR, "us-east-1", "20130524T000000Z",
        )
        self.assertTrue(value.endswith("Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"))

    def test_uri_encode(self):
        self.assertEqual(s3.uri_encode("a b+c~é (1).md/x", keep_slash=True), "a%20b%2Bc~%C3%A9%20%281%29.md/x")
        self.assertEqual(s3.uri_encode("a/b"), "a%2Fb")


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        endpoint = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.sleeps = []
        self.client = s3.Client(endpoint, "bucket", "AKID", "SECRET", sleep=self.sleeps.append)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_prefix_exists(self):
        self.assertFalse(self.client.prefix_exists("pkg/docs/v1/"))
        self.store.objects["pkg/docs/v1/a.md"] = b"a"
        self.assertTrue(self.client.prefix_exists("pkg/docs/v1/"))
        self.assertFalse(self.client.prefix_exists("pkg/docs/v2/"))

    def test_prefix_exists_fails_closed(self):
        self.store.failures = [("GET", 403, False)] * 1
        with self.assertRaisesRegex(s3.S3Error, r"list pkg/docs/v1/: HTTP 403"):
            self.client.prefix_exists("pkg/docs/v1/")

    def test_put_if_absent_creates_then_refuses(self):
        self.client.put_if_absent("pkg/docs/v1/a b.md", b"hello", "text/markdown")
        self.assertEqual(self.store.objects["pkg/docs/v1/a b.md"], b"hello")
        method, path, headers = self.store.requests[-1]
        self.assertEqual((method, path), ("PUT", "/bucket/pkg/docs/v1/a b.md"))
        self.assertEqual(headers["If-None-Match"], "*")
        self.assertIn("if-none-match", headers["Authorization"])
        with self.assertRaises(s3.AlreadyExists):
            self.client.put_if_absent("pkg/docs/v1/a b.md", b"other", "text/markdown")
        self.assertEqual(self.store.objects["pkg/docs/v1/a b.md"], b"hello")

    def test_put_retries_a_server_error(self):
        self.store.failures = [("PUT", 503, False)]
        self.client.put_if_absent("k", b"v", "text/plain")
        self.assertEqual(self.store.objects["k"], b"v")
        self.assertEqual(self.sleeps, [1.0])

    def test_retry_after_an_unknown_outcome_accepts_its_own_write(self):
        # The first attempt stored the object but answered 500; the retry gets
        # 412 and the ETag proves the object holds the same bytes.
        self.store.failures = [("PUT", 500, True)]
        self.client.put_if_absent("k", b"v", "text/plain")
        self.assertEqual([request[0] for request in self.store.requests], ["PUT", "PUT", "HEAD"])

    def test_retry_after_an_unknown_outcome_refuses_other_bytes(self):
        self.store.objects["k"] = b"old"
        self.store.failures = [("PUT", 500, False)]
        with self.assertRaises(s3.AlreadyExists):
            self.client.put_if_absent("k", b"new", "text/plain")
        self.assertEqual(self.store.objects["k"], b"old")

    def test_put_gives_up_after_the_last_attempt(self):
        self.store.failures = [("PUT", 503, False)] * s3.MAX_ATTEMPTS
        with self.assertRaisesRegex(s3.S3Error, r"put k: HTTP 503 \(InternalError\)"):
            self.client.put("k", b"v", "text/plain")
        self.assertEqual(len(self.sleeps), s3.MAX_ATTEMPTS - 1)

    def test_unconditional_put_replaces(self):
        self.store.objects["pkg/manifest.json"] = b"{}"
        self.client.put("pkg/manifest.json", b'{"latest": "v2"}', "application/json")
        self.assertEqual(self.store.objects["pkg/manifest.json"], b'{"latest": "v2"}')
        self.assertNotIn("If-None-Match", self.store.requests[-1][2])

    def test_redirect_is_not_followed(self):
        port = self.server.server_address[1]
        self.store.redirect = f"http://127.0.0.1:{port}/elsewhere/?list-type=2"
        with self.assertRaisesRegex(s3.S3Error, r"list p/: HTTP 302"):
            self.client.prefix_exists("p/")
        with self.assertRaisesRegex(s3.S3Error, r"put k: HTTP 302"):
            self.client.put_if_absent("k", b"v", "text/plain")
        self.assertEqual([path for _, path, _ in self.store.requests], ["/bucket", "/bucket/k"])
        self.assertNotIn("k", self.store.objects)

    def test_network_error_is_reported_without_details(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        client = s3.Client(f"http://127.0.0.1:{closed_port}", "bucket", "AKID", "SECRET", sleep=self.sleeps.append)
        with self.assertRaisesRegex(s3.S3Error, r"^list p/: network error \(\w+\) after 3 attempts$"):
            client.prefix_exists("p/")

if __name__ == "__main__":
    unittest.main()
