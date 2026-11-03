"""Tests of scripts/publish.py: input and package validation, the plan and the upload order.

The upload runs against an in-memory client; no network.

Run: python3 -I -B -m unittest discover -s tests -v (from the component folder).
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
FIXTURE = os.path.join(HERE, "fixtures", "basic")
EXPECTED = os.path.join(HERE, "fixtures", "expected", "basic.sha256")


def load(name):
    spec = importlib.util.spec_from_file_location(f"r2_docs_{name}", os.path.join(SCRIPTS, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publish = load("publish")
S3 = publish.S3


class MemoryClient:
    """Records writes; mirrors the conditional semantics of s3.Client."""

    def __init__(self, objects=None, fail_key=None, hold_seconds=0):
        self.objects = dict(objects or {})
        self.writes = []
        self.fail_key = fail_key
        self.hold_seconds = hold_seconds
        self.lock = threading.Lock()

    def prefix_exists(self, prefix):
        return any(key.startswith(prefix) for key in self.objects)

    def put_if_absent(self, key, body, content_type):
        if key == self.fail_key:
            raise S3.S3Error(f"put {key}: HTTP 500 (InternalError)")
        if self.hold_seconds:
            threading.Event().wait(self.hold_seconds)
        with self.lock:
            if key in self.objects:
                raise S3.AlreadyExists(f"put {key}: the object already exists")
            self.objects[key] = body
            self.writes.append((key, content_type, True))

    def put(self, key, body, content_type):
        self.objects[key] = body
        self.writes.append((key, content_type, False))


class PackageCase(unittest.TestCase):
    """Copies the fixture into a temporary workspace."""

    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.workspace)
        self.root = os.path.join(self.workspace, "package")
        shutil.copytree(FIXTURE, self.root)

    def config(self, **overrides):
        config = {
            "root": self.root,
            "package_name": "example-library",
            "version": "",
            "kinds": ["docs", "xref", "schemas"],
            "bucket": "example-bucket",
            "endpoint": "https://storage.example.com",
            "write_manifest": True,
            "dry_run": True,
        }
        config.update(overrides)
        return config

    def write(self, relative, data=b"x"):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)

    def write_manifest(self, value):
        self.write("manifest.json", value if isinstance(value, bytes) else json.dumps(value).encode())


class InputValidationTest(unittest.TestCase):
    def test_package_name(self):
        self.assertEqual(publish.validate_package_name("my.lib_2-x"), "my.lib_2-x")
        for bad in ("", "-lib", "a/b", "a b", "x" * 101):
            with self.subTest(bad=bad), self.assertRaisesRegex(publish.InputError, "package-name"):
                publish.validate_package_name(bad)

    def test_version(self):
        self.assertEqual(publish.validate_version("v1.4.0-rc.1"), "v1.4.0-rc.1")
        for bad in ("", ".hidden", "v1/2", "v1..2", "v1+build", "../v1"):
            with self.subTest(bad=bad), self.assertRaisesRegex(publish.InputError, "version"):
                publish.validate_version(bad)

    def test_kinds(self):
        self.assertEqual(publish.parse_kinds("docs, xref\nschemas"), ["docs", "xref", "schemas"])
        for bad in ("", " , ", "Docs", "docs,docs", "a/b"):
            with self.subTest(bad=bad), self.assertRaisesRegex(publish.InputError, "kinds"):
                publish.parse_kinds(bad)

    def test_bucket(self):
        self.assertEqual(publish.validate_bucket("docs-bucket"), "docs-bucket")
        with self.assertRaisesRegex(publish.InputError, "CLOUDFLARE_R2_DOCS_PUBLISH_BUCKET"):
            publish.validate_bucket("")
        for bad in ("ab", "Docs", "-docs", "a..b", "a/b"):
            with self.subTest(bad=bad), self.assertRaisesRegex(publish.InputError, "bucket must match"):
                publish.validate_bucket(bad)

    def test_endpoint(self):
        self.assertEqual(publish.validate_endpoint("https://abc.r2.example.com/"), "https://abc.r2.example.com")
        self.assertEqual(publish.validate_endpoint("https://host:8443"), "https://host:8443")
        with self.assertRaisesRegex(publish.InputError, "CLOUDFLARE_R2_DOCS_PUBLISH_ENDPOINT"):
            publish.validate_endpoint("")
        for bad in ("http://host", "https://user:pw@host", "https://host/bucket", "https://host?x=1", "host"):
            with self.subTest(bad=bad), self.assertRaisesRegex(publish.InputError, "endpoint must be"):
                publish.validate_endpoint(bad)

    def test_dry_run_accepts_an_empty_endpoint(self):
        self.assertEqual(publish.validate_endpoint("", dry_run=True), "")
        with self.assertRaisesRegex(publish.InputError, "endpoint must be"):
            publish.validate_endpoint("http://host", dry_run=True)

    def test_bool(self):
        self.assertTrue(publish.parse_bool("dry-run", "true"))
        with self.assertRaisesRegex(publish.InputError, "dry-run"):
            publish.parse_bool("dry-run", "yes")


class PathTest(PackageCase):
    def test_relative_directory(self):
        self.assertEqual(publish.resolve_package_dir("package", self.workspace), os.path.realpath(self.root))
        self.assertEqual(publish.resolve_package_dir("package/.", self.workspace), os.path.realpath(self.root))

    def test_refuses_traversal_absolute_and_missing(self):
        for bad in ("", "../package", "package/../package", self.root, "missing"):
            with self.subTest(bad=bad), self.assertRaisesRegex(publish.InputError, "path"):
                publish.resolve_package_dir(bad, self.workspace)

    def test_refuses_a_link_out_of_the_workspace(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside)
        os.symlink(outside, os.path.join(self.workspace, "link"))
        with self.assertRaisesRegex(publish.InputError, "leaves the workspace"):
            publish.resolve_package_dir("link", self.workspace)


class ManifestTest(PackageCase):
    def test_missing(self):
        os.remove(os.path.join(self.root, "manifest.json"))
        with self.assertRaisesRegex(publish.InputError, "manifest.json is missing"):
            publish.build_plan(self.config())

    def test_not_json(self):
        self.write_manifest(b"{not json")
        with self.assertRaisesRegex(publish.InputError, "not valid UTF-8 JSON"):
            publish.build_plan(self.config())

    def test_not_an_object(self):
        self.write_manifest([1, 2])
        with self.assertRaisesRegex(publish.InputError, "JSON object"):
            publish.build_plan(self.config())

    def test_other_package(self):
        self.write_manifest({"package": "other", "latest": "v1.2.0"})
        with self.assertRaisesRegex(publish.InputError, "names package 'other'"):
            publish.build_plan(self.config())

    def test_latest_differs_from_version(self):
        with self.assertRaisesRegex(publish.InputError, "latest is 'v1.2.0', not version 'v9'"):
            publish.build_plan(self.config(version="v9"))

    def test_no_version_anywhere(self):
        self.write_manifest({"package": "example-library"})
        with self.assertRaisesRegex(publish.InputError, "pass the input version"):
            publish.build_plan(self.config())

    def test_version_input_without_latest(self):
        self.write_manifest({})
        self.assertEqual(publish.build_plan(self.config(version="v1.2.0"))["version"], "v1.2.0")

    def test_unsafe_latest(self):
        self.write_manifest({"latest": "../v1"})
        with self.assertRaisesRegex(publish.InputError, "manifest.json latest must match"):
            publish.build_plan(self.config())

    def test_versions_must_name_the_version(self):
        self.write_manifest({"latest": "v1.2.0", "versions": {"v1.1.0": {}}})
        with self.assertRaisesRegex(publish.InputError, "versions does not name the version 'v1.2.0'"):
            publish.build_plan(self.config())
        self.write_manifest({"latest": "v1.2.0", "versions": ["v1.1.0", "v1.2.0"]})
        self.assertEqual(publish.build_plan(self.config())["version"], "v1.2.0")
        self.write_manifest({"latest": "v1.2.0", "versions": "v1.2.0"})
        with self.assertRaisesRegex(publish.InputError, "versions must be an object or a list"):
            publish.build_plan(self.config())

    def test_symlinked_manifest(self):
        os.rename(os.path.join(self.root, "manifest.json"), os.path.join(self.workspace, "m.json"))
        os.symlink(os.path.join(self.workspace, "m.json"), os.path.join(self.root, "manifest.json"))
        with self.assertRaisesRegex(publish.InputError, "not a regular file"):
            publish.build_plan(self.config())


class LayoutTest(PackageCase):
    def test_plan_matches_the_expected_listing(self):
        plan = publish.build_plan(self.config())
        with open(EXPECTED, encoding="utf-8") as handle:
            expected = handle.read()
        self.assertEqual(publish.payload_lines(plan), expected)
        self.assertEqual(publish.payload_sha256(plan), hashlib.sha256(expected.encode()).hexdigest())
        self.assertEqual(plan["version"], "v1.2.0")
        self.assertEqual(
            plan["prefixes"],
            ["example-library/docs/v1.2.0/", "example-library/xref/v1.2.0/", "example-library/schemas/v1.2.0/"],
        )
        self.assertEqual(plan["manifest"]["key"], "example-library/manifest.json")
        self.assertEqual(len(plan["objects"]), 4)

    def test_kinds_may_be_absent(self):
        shutil.rmtree(os.path.join(self.root, "xref"))
        shutil.rmtree(os.path.join(self.root, "schemas"))
        plan = publish.build_plan(self.config())
        self.assertEqual({item["key"].split("/")[1] for item in plan["objects"]}, {"docs"})
        # The immutability check still covers the absent kinds.
        self.assertEqual(
            plan["prefixes"],
            ["example-library/docs/v1.2.0/", "example-library/xref/v1.2.0/", "example-library/schemas/v1.2.0/"],
        )

    def test_without_the_manifest(self):
        plan = publish.build_plan(self.config(write_manifest=False))
        self.assertIsNone(plan["manifest"])
        self.assertNotIn("manifest.json", publish.payload_lines(plan))

    def test_unexpected_root_entry(self):
        for name in ("README.md", "assets/x.png", ".hidden"):
            with self.subTest(name=name):
                self.write(name)
                with self.assertRaisesRegex(publish.InputError, "unexpected entry at the package root"):
                    publish.build_plan(self.config())
                path = os.path.join(self.root, name.split("/")[0])
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)

    def test_kind_not_in_kinds(self):
        with self.assertRaisesRegex(publish.InputError, "'xref'"):
            publish.build_plan(self.config(kinds=["docs", "schemas"]))

    def test_kind_with_two_versions(self):
        self.write("docs/v1.1.0/old.md")
        with self.assertRaisesRegex(publish.InputError, "docs/ must hold exactly one directory"):
            publish.build_plan(self.config())

    def test_kind_with_another_version(self):
        with self.assertRaisesRegex(publish.InputError, "named after the version 'v1.3.0'"):
            self.write_manifest({"latest": "v1.3.0"})
            publish.build_plan(self.config())

    def test_kind_with_a_loose_file(self):
        self.write("docs/index.md")
        with self.assertRaisesRegex(publish.InputError, "docs/ must hold exactly one directory"):
            publish.build_plan(self.config())

    def test_symbolic_link_inside(self):
        os.symlink("/etc/hosts", os.path.join(self.root, "docs", "v1.2.0", "hosts.md"))
        with self.assertRaisesRegex(publish.InputError, "symbolic link in the package: docs/v1.2.0/hosts.md"):
            publish.build_plan(self.config())

    def test_version_entry_is_a_link(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside)
        with open(os.path.join(outside, "id_rsa"), "wb") as handle:
            handle.write(b"secret")
        shutil.rmtree(os.path.join(self.root, "docs", "v1.2.0"))
        os.symlink(outside, os.path.join(self.root, "docs", "v1.2.0"))
        with self.assertRaisesRegex(publish.InputError, "docs/v1.2.0 must be a directory, not a link or a file"):
            publish.build_plan(self.config())

    def test_version_entry_is_a_file(self):
        shutil.rmtree(os.path.join(self.root, "docs", "v1.2.0"))
        self.write("docs/v1.2.0")
        with self.assertRaisesRegex(publish.InputError, "docs/v1.2.0 must be a directory, not a link or a file"):
            publish.build_plan(self.config())

    def test_unsafe_name(self):
        self.write("docs/v1.2.0/bad\\name.md")
        with self.assertRaisesRegex(publish.InputError, "unsafe file name"):
            publish.build_plan(self.config())

    def test_no_file(self):
        for kind in ("docs", "xref", "schemas"):
            shutil.rmtree(os.path.join(self.root, kind))
        with self.assertRaisesRegex(publish.InputError, "holds no kind directory"):
            publish.build_plan(self.config())
        os.makedirs(os.path.join(self.root, "docs", "v1.2.0"))
        with self.assertRaisesRegex(publish.InputError, "holds no file"):
            publish.build_plan(self.config())

    def test_content_types(self):
        self.assertEqual(publish.content_type("a/B.MD"), "text/markdown; charset=utf-8")
        self.assertEqual(publish.content_type("x.schema.json"), "application/json; charset=utf-8")
        self.assertEqual(publish.content_type("blob"), "application/octet-stream")


class UploadTest(PackageCase):
    def setUp(self):
        super().setUp()
        self.plan = publish.build_plan(self.config())
        self.log = []

    def test_writes_every_object_then_the_manifest(self):
        client = MemoryClient()
        publish.upload(self.plan, client, log=self.log.append)
        keys = [write[0] for write in client.writes]
        self.assertEqual(keys[-1], "example-library/manifest.json")
        self.assertEqual(sorted(keys[:-1]), sorted(item["key"] for item in self.plan["objects"]))
        self.assertTrue(all(conditional for _, _, conditional in client.writes[:-1]))
        self.assertFalse(client.writes[-1][2])
        with open(os.path.join(self.root, "docs", "v1.2.0", "index.md"), "rb") as handle:
            self.assertEqual(client.objects["example-library/docs/v1.2.0/index.md"], handle.read())

    def test_refuses_a_published_version_before_any_write(self):
        client = MemoryClient({"example-library/xref/v1.2.0/old.html": b"x", "example-library/manifest.json": b"{}"})
        with self.assertRaisesRegex(S3.AlreadyExists, "already published under example-library/xref/v1.2.0/"):
            publish.upload(self.plan, client, log=self.log.append)
        self.assertEqual(client.writes, [])

    def test_refuses_a_version_published_under_an_absent_kind(self):
        shutil.rmtree(os.path.join(self.root, "xref"))
        shutil.rmtree(os.path.join(self.root, "schemas"))
        plan = publish.build_plan(self.config())
        client = MemoryClient({"example-library/xref/v1.2.0/index.html": b"x"})
        with self.assertRaisesRegex(S3.AlreadyExists, "already published under example-library/xref/v1.2.0/"):
            publish.upload(plan, client, log=self.log.append)
        self.assertEqual(client.writes, [])

    def test_first_failure_stops_the_writes_not_started(self):
        first = self.plan["objects"][0]["key"]
        client = MemoryClient(fail_key=first, hold_seconds=0.3)
        with mock.patch.object(publish, "UPLOAD_WORKERS", 1), self.assertRaisesRegex(S3.S3Error, "HTTP 500"):
            publish.upload(self.plan, client, log=self.log.append)
        # One write may already run when the failure lands; the others never start.
        self.assertLessEqual(len(client.writes), 1)
        self.assertTrue(any("not sent" in line for line in self.log))
        self.assertNotIn("example-library/manifest.json", client.objects)

    def test_without_the_manifest_writes_only_the_version(self):
        plan = publish.build_plan(self.config(write_manifest=False))
        client = MemoryClient({"example-library/manifest.json": b"old"})
        publish.upload(plan, client, log=self.log.append)
        self.assertEqual(client.objects["example-library/manifest.json"], b"old")
        self.assertEqual(len(client.writes), len(plan["objects"]))

    def test_other_versions_do_not_block(self):
        client = MemoryClient({"example-library/docs/v1.1.0/index.md": b"x"})
        publish.upload(self.plan, client, log=self.log.append)
        self.assertEqual(client.objects["example-library/docs/v1.1.0/index.md"], b"x")

    def test_failed_object_keeps_the_manifest(self):
        client = MemoryClient({"example-library/manifest.json": b"old"}, fail_key="example-library/docs/v1.2.0/index.md")
        with self.assertRaisesRegex(S3.S3Error, "HTTP 500"):
            publish.upload(self.plan, client, log=self.log.append)
        self.assertEqual(client.objects["example-library/manifest.json"], b"old")

    def test_file_changed_after_the_plan(self):
        self.write("docs/v1.2.0/index.md", b"changed")
        client = MemoryClient()
        with self.assertRaisesRegex(publish.InputError, "file changed after validation"):
            publish.upload(self.plan, client, log=self.log.append)
        self.assertNotIn("example-library/manifest.json", client.objects)


class CommandLineTest(PackageCase):
    def run_main(self, argv, env):
        stdout, stderr = io.StringIO(), io.StringIO()
        saved = {name: os.environ.get(name) for name in env}
        os.environ.update(env)
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = publish.main(argv)
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        return code, stdout.getvalue(), stderr.getvalue()

    def plan_args(self, **overrides):
        values = {
            "path": "package",
            "package-name": "example-library",
            "kinds": "docs,xref,schemas",
            "bucket": "example-bucket",
            "endpoint": "https://storage.example.com",
            "write-manifest": "true",
            "dry-run": "true",
            "has-credentials": "false",
            "out-dir": os.path.join(self.workspace, "out"),
        }
        values.update(overrides)
        return ["plan"] + [f"--{name}={value}" for name, value in values.items()]

    def test_plan_writes_the_outputs(self):
        output = os.path.join(self.workspace, "github-output")
        env = {"GITHUB_WORKSPACE": self.workspace, "GITHUB_OUTPUT": output}
        code, stdout, stderr = self.run_main(self.plan_args(), env)
        self.assertEqual(code, 0, stderr)
        with open(output, encoding="utf-8") as handle:
            outputs = dict(line.rstrip("\n").split("=", 1) for line in handle)
        with open(EXPECTED, "rb") as handle:
            self.assertEqual(outputs["payload-sha256"], hashlib.sha256(handle.read()).hexdigest())
        self.assertEqual(outputs["version"], "v1.2.0")
        self.assertEqual(outputs["objects"], "4")
        self.assertTrue(os.path.isfile(outputs["plan-file"]))
        self.assertIn("Dry run: nothing is sent.", stdout)

    def test_dry_run_plans_without_an_endpoint(self):
        env = {"GITHUB_WORKSPACE": self.workspace, "GITHUB_OUTPUT": ""}
        code, stdout, stderr = self.run_main(self.plan_args(endpoint=""), env)
        self.assertEqual(code, 0, stderr)
        self.assertIn("Dry run: nothing is sent.", stdout)

    def test_publication_needs_an_endpoint(self):
        env = {"GITHUB_WORKSPACE": self.workspace, "GITHUB_OUTPUT": ""}
        args = self.plan_args(endpoint="", **{"dry-run": "false", "has-credentials": "true"})
        code, _, stderr = self.run_main(args, env)
        self.assertEqual(code, 1)
        self.assertIn("::error::endpoint is empty", stderr)

    def test_publication_needs_credentials(self):
        env = {"GITHUB_WORKSPACE": self.workspace, "GITHUB_OUTPUT": ""}
        code, _, stderr = self.run_main(self.plan_args(**{"dry-run": "false"}), env)
        self.assertEqual(code, 1)
        self.assertIn("::error::access-key-id or secret-access-key is empty", stderr)

    def test_invalid_input_fails_before_the_plan(self):
        env = {"GITHUB_WORKSPACE": self.workspace, "GITHUB_OUTPUT": ""}
        code, _, stderr = self.run_main(self.plan_args(path="../package"), env)
        self.assertEqual(code, 1)
        self.assertIn("::error::path must be relative", stderr)
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "out")))

    def test_upload_needs_the_credential_variables(self):
        env = {"S3_ACCESS_KEY_ID": "", "S3_SECRET_ACCESS_KEY": ""}
        code, _, stderr = self.run_main(["upload", "--plan-file=/nonexistent"], env)
        self.assertEqual(code, 1)
        self.assertIn("S3_ACCESS_KEY_ID or S3_SECRET_ACCESS_KEY is empty", stderr)


if __name__ == "__main__":
    unittest.main()
