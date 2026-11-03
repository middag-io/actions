#!/usr/bin/env python3
"""Validate and publish a prepared documentation package to an S3-compatible bucket.

The caller prepares the package directory; this script never builds content.
Layout of the directory (one version per publication):

    manifest.json
    <kind>/<version>/...      one directory per kind present, kinds from --kinds

Object keys in the bucket:

    <package-name>/<kind>/<version>/<path>    immutable: written once, never replaced
    <package-name>/manifest.json              mutable pointer, written last
                                              (unless --write-manifest=false)

Commands:

- plan: validates every input and the directory, then writes plan.json
  (endpoint, bucket, objects with their SHA-256) to --out-dir. No network.
  A dry run accepts an empty endpoint.
- upload: reads plan.json, refuses when the <package-name>/<kind>/<version>/
  prefix of any allowed kind (present in the package or not) already holds
  an object, writes every version object with a conditional
  "If-None-Match: *" PUT, stops at the first failed write, then writes
  manifest.json. Needs the environment variables S3_ACCESS_KEY_ID and
  S3_SECRET_ACCESS_KEY.

Standard library only; run as "python3 -I publish.py ...". See ../README.md.
"""

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import urllib.parse

PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
KIND_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:[0-9]{1,5})?$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\\]")
SEPARATORS_RE = re.compile(r"[,\n]")
MANIFEST = "manifest.json"
MANIFEST_MAX_BYTES = 1024 * 1024
OBJECT_MAX_BYTES = 100 * 1024 * 1024
KEY_MAX_BYTES = 1024
UPLOAD_WORKERS = 8
REGION = "auto"
DEFAULT_CONTENT_TYPE = "application/octet-stream"
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".gif": "image/gif",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".mdx": "text/markdown; charset=utf-8",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".xml": "application/xml; charset=utf-8",
    ".yaml": "application/yaml; charset=utf-8",
    ".yml": "application/yaml; charset=utf-8",
}


class InputError(ValueError):
    """An input or package that fails validation. The message names the input."""


def _load_s3():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "s3.py")
    spec = importlib.util.spec_from_file_location("cloudflare_r2_docs_publish_s3", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


S3 = _load_s3()


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


def validate_package_name(value):
    if not PACKAGE_NAME_RE.match(value or ""):
        raise InputError(f"package-name must match {PACKAGE_NAME_RE.pattern}: {value!r}")
    return value


def validate_version(value, what="version"):
    if not VERSION_RE.match(value or "") or ".." in value:
        raise InputError(f"{what} must match {VERSION_RE.pattern} and hold no '..': {value!r}")
    return value


def parse_kinds(value):
    kinds = [item.strip() for item in SEPARATORS_RE.split(value or "") if item.strip()]
    if not kinds:
        raise InputError("kinds must name at least one kind")
    for kind in kinds:
        if not KIND_RE.match(kind):
            raise InputError(f"kinds: {kind!r} must match {KIND_RE.pattern}")
    if len(set(kinds)) != len(kinds):
        raise InputError("kinds must not repeat a kind")
    return kinds


def validate_bucket(value):
    if not value:
        raise InputError(
            "bucket is empty: pass the input bucket "
            "(reusable workflow: or set the variable CLOUDFLARE_R2_DOCS_PUBLISH_BUCKET)"
        )
    if not BUCKET_RE.match(value) or ".." in value:
        raise InputError(f"bucket must match {BUCKET_RE.pattern}: {value!r}")
    return value


def validate_endpoint(value, dry_run=False):
    """The endpoint carries the account id: it comes from a secret, and a dry run may omit it."""
    if not value:
        if dry_run:
            return ""
        raise InputError(
            "endpoint is empty: pass it from a secret "
            "(for example CLOUDFLARE_R2_DOCS_PUBLISH_ENDPOINT; reusable workflow: the secret endpoint) "
            "or set dry-run: true"
        )
    parts = urllib.parse.urlsplit(value)
    if (
        parts.scheme != "https"
        or not HOST_RE.match(parts.netloc)
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise InputError("endpoint must be https://<host>[:port] with no path, query or credentials")
    return f"https://{parts.netloc}"


def parse_bool(name, value):
    if value not in ("true", "false"):
        raise InputError(f"{name} must be true or false: {value!r}")
    return value == "true"


def resolve_package_dir(path, workspace):
    """The package directory, relative to the workspace and inside it."""
    if not path or os.path.isabs(path) or ".." in path.replace("\\", "/").split("/"):
        raise InputError(f"path must be relative to the workspace and hold no '..': {path!r}")
    workspace = os.path.realpath(workspace)
    resolved = os.path.realpath(os.path.join(workspace, path))
    if resolved != workspace and not resolved.startswith(workspace + os.sep):
        raise InputError(f"path leaves the workspace through a link: {path!r}")
    if not os.path.isdir(resolved):
        raise InputError(f"path is not a directory: {path!r}")
    return resolved


# --------------------------------------------------------------------------
# Package validation
# --------------------------------------------------------------------------


def read_manifest(root, package_name, version):
    """Reads manifest.json; returns (bytes, version). version may be empty."""
    path = os.path.join(root, MANIFEST)
    info = os.lstat(path) if os.path.lexists(path) else None
    if info is None or not stat.S_ISREG(info.st_mode):
        raise InputError(f"{MANIFEST} is missing from the package directory (or is not a regular file)")
    if info.st_size > MANIFEST_MAX_BYTES:
        raise InputError(f"{MANIFEST} is larger than {MANIFEST_MAX_BYTES} bytes")
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise InputError(f"{MANIFEST} is not valid UTF-8 JSON: {error}") from None
    if not isinstance(manifest, dict):
        raise InputError(f"{MANIFEST} must hold a JSON object")
    declared = manifest.get("package")
    if declared is not None and declared != package_name:
        raise InputError(f"{MANIFEST} names package {declared!r}, not package-name {package_name!r}")
    latest = manifest.get("latest")
    if latest is not None and not isinstance(latest, str):
        raise InputError(f"{MANIFEST}: latest must be a string")
    if version and latest is not None and latest != version:
        raise InputError(f"{MANIFEST}: latest is {latest!r}, not version {version!r}")
    resolved = version or latest
    if not resolved:
        raise InputError(f"version is empty and {MANIFEST} has no latest: pass the input version")
    resolved = validate_version(resolved, "version" if version else f"{MANIFEST} latest")
    _check_versions_field(manifest.get("versions"), resolved)
    return data, resolved


def _check_versions_field(versions, version):
    """A versions field (object keyed by version, or list) must name the version."""
    if versions is None:
        return
    if not isinstance(versions, (dict, list)):
        raise InputError(f"{MANIFEST}: versions must be an object or a list")
    if version not in versions:
        raise InputError(f"{MANIFEST}: versions does not name the version {version!r}")


def _check_name(name, where):
    if name in ("", ".", "..") or CONTROL_RE.search(name):
        raise InputError(f"unsafe file name in {where}: {name!r}")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        raise InputError(f"file name is not valid UTF-8 in {where}") from None


def _entries(directory):
    with os.scandir(directory) as scan:
        return sorted(scan, key=lambda entry: entry.name)


def _walk_files(directory, relative):
    """Yields (absolute path, relative path) of every regular file; refuses links."""
    for entry in _entries(directory):
        where = relative or "."
        _check_name(entry.name, where)
        child = f"{relative}/{entry.name}" if relative else entry.name
        if entry.is_symlink():
            raise InputError(f"symbolic link in the package: {child}")
        if entry.is_dir(follow_symlinks=False):
            yield from _walk_files(entry.path, child)
        elif entry.is_file(follow_symlinks=False):
            yield entry.path, child
        else:
            raise InputError(f"not a regular file in the package: {child}")


def _check_top_level(root, kinds, version):
    """Only manifest.json and <kind>/ directories; each kind holds <version>/ only."""
    present = []
    for entry in _entries(root):
        if entry.name == MANIFEST:
            continue
        if entry.name not in kinds or entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            allowed = ", ".join(f"{kind}/" for kind in kinds)
            raise InputError(f"unexpected entry at the package root: {entry.name!r} (allowed: {MANIFEST}, {allowed})")
        children = _entries(entry.path)
        names = [child.name for child in children]
        if names != [version]:
            raise InputError(
                f"{entry.name}/ must hold exactly one directory named after the version {version!r}; found {names}"
            )
        if children[0].is_symlink() or not children[0].is_dir(follow_symlinks=False):
            raise InputError(f"{entry.name}/{version} must be a directory, not a link or a file")
        present.append(entry.name)
    if not present:
        raise InputError(f"the package holds no kind directory ({', '.join(kinds)})")
    return present


def _object_key(package_name, relative):
    key = f"{package_name}/{relative}"
    if len(key.encode("utf-8")) > KEY_MAX_BYTES:
        raise InputError(f"object key longer than {KEY_MAX_BYTES} bytes: {key[:80]}...")
    return key


def content_type(name):
    return CONTENT_TYPES.get(os.path.splitext(name)[1].lower(), DEFAULT_CONTENT_TYPE)


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_plan(config):
    """Validates the package and returns the plan (no network)."""
    root = config["root"]
    package_name = config["package_name"]
    manifest_bytes, version = read_manifest(root, package_name, config["version"])
    present = _check_top_level(root, config["kinds"], version)
    real_root = os.path.realpath(root)
    objects = []
    for kind in present:
        for path, relative in _walk_files(os.path.join(root, kind, version), f"{kind}/{version}"):
            if not os.path.realpath(path).startswith(real_root + os.sep):
                raise InputError(f"file resolves outside the package directory: {relative}")
            size = os.lstat(path).st_size
            if size > OBJECT_MAX_BYTES:
                raise InputError(f"file larger than {OBJECT_MAX_BYTES} bytes: {relative}")
            objects.append(
                {
                    "key": _object_key(package_name, relative),
                    "path": path,
                    "sha256": _file_sha256(path),
                    "content_type": content_type(relative),
                }
            )
    if not objects:
        raise InputError(f"the package holds no file under any <kind>/{version}/ directory")
    manifest = None
    if config["write_manifest"]:
        manifest = {
            "key": f"{package_name}/{MANIFEST}",
            "path": os.path.join(root, MANIFEST),
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "content_type": content_type(MANIFEST),
        }
    return {
        "endpoint": config["endpoint"],
        "bucket": config["bucket"],
        "package_name": package_name,
        "version": version,
        # Every allowed kind, present or not: a published version stays closed
        # to any later run, whatever kinds that run carries.
        "prefixes": [f"{package_name}/{kind}/{version}/" for kind in config["kinds"]],
        "objects": objects,
        "manifest": manifest,
    }


def payload_lines(plan):
    """Canonical listing: "<sha256>  <key>" per object, manifest (when written) included, sorted by key."""
    entries = plan["objects"] + ([plan["manifest"]] if plan["manifest"] else [])
    return "".join(f"{item['sha256']}  {item['key']}\n" for item in sorted(entries, key=lambda item: item["key"]))


def payload_sha256(plan):
    return hashlib.sha256(payload_lines(plan).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------


def _read_checked(item):
    """Reads a planned file and refuses it when it changed since the plan."""
    with open(item["path"], "rb") as handle:
        data = handle.read()
    if hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise InputError(f"file changed after validation: {item['key']}")
    return data


def upload(plan, client, log=print):
    """Publishes the plan. Nothing is written when a version prefix is taken."""
    for prefix in plan["prefixes"]:
        if client.prefix_exists(prefix):
            raise S3.AlreadyExists(
                f"version {plan['version']} is already published under {prefix}; "
                "published versions are immutable: publish a new version"
            )
    log(f"No object under {', '.join(plan['prefixes'])}: publishing {len(plan['objects'])} object(s).")
    _put_objects(plan["objects"], client, log)
    manifest = plan["manifest"]
    if not manifest:
        log(f"Wrote the {len(plan['objects'])} version object(s); manifest.json not written (write-manifest: false).")
        return
    client.put(manifest["key"], _read_checked(manifest), manifest["content_type"])
    log(f"Wrote {manifest['key']} after the {len(plan['objects'])} version object(s).")


def _put_objects(objects, client, log):
    """Conditional writes in parallel; the first failure stops every write not yet started."""

    def put(item):
        client.put_if_absent(item["key"], _read_checked(item), item["content_type"])

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=UPLOAD_WORKERS)
    try:
        futures = [pool.submit(put, item) for item in objects]
        concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_EXCEPTION)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    cancelled = sum(1 for future in futures if future.cancelled())
    errors = [future.exception() for future in futures if not future.cancelled() and future.exception()]
    if not errors:
        return
    for error in errors[1:]:
        log(f"::error::{error}")
    if cancelled:
        log(f"Stopped after the first failed write: {cancelled} object(s) not sent.")
    raise errors[0]


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def write_outputs(outputs, path):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def parse_config(args, workspace):
    dry_run = parse_bool("dry-run", args.dry_run)
    if not dry_run and not parse_bool("has-credentials", args.has_credentials):
        raise InputError(
            "access-key-id or secret-access-key is empty: pass both secrets "
            "(for example from CLOUDFLARE_R2_DOCS_PUBLISH_ACCESS_KEY_ID and "
            "CLOUDFLARE_R2_DOCS_PUBLISH_SECRET_ACCESS_KEY) or set dry-run: true"
        )
    if args.out_dir and os.path.exists(os.path.join(args.out_dir, "plan.json")):
        raise InputError("out-dir must not hold a previous plan")
    return {
        "root": resolve_package_dir(args.path, workspace),
        "package_name": validate_package_name(args.package_name),
        "version": validate_version(args.version) if args.version else "",
        "kinds": parse_kinds(args.kinds),
        "bucket": validate_bucket(args.bucket),
        "endpoint": validate_endpoint(args.endpoint, dry_run),
        "write_manifest": parse_bool("write-manifest", args.write_manifest),
        "dry_run": dry_run,
    }


def command_plan(args):
    config = parse_config(args, os.environ.get("GITHUB_WORKSPACE") or os.getcwd())
    plan = build_plan(config)
    os.makedirs(args.out_dir, exist_ok=True)
    plan_file = os.path.join(args.out_dir, "plan.json")
    with open(plan_file, "w", encoding="utf-8") as handle:
        json.dump(plan, handle)
    outputs = {
        "version": plan["version"],
        "objects": str(len(plan["objects"])),
        "payload-sha256": payload_sha256(plan),
        "plan-file": plan_file,
    }
    write_outputs(outputs, os.environ.get("GITHUB_OUTPUT"))
    print("::group::Object keys")
    print(payload_lines(plan), end="")
    print("::endgroup::")
    manifest = plan["manifest"]["key"] if plan["manifest"] else "no manifest.json (write-manifest: false)"
    print(f"Planned {outputs['objects']} version object(s) and {manifest} for version {plan['version']}.")
    print(f"payload-sha256: {outputs['payload-sha256']}")
    if config["dry_run"]:
        print("Dry run: nothing is sent.")
    return 0


def command_upload(args):
    access_key = os.environ.get("S3_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("S3_SECRET_ACCESS_KEY", "")
    if not access_key or not secret_key:
        raise InputError("S3_ACCESS_KEY_ID or S3_SECRET_ACCESS_KEY is empty")
    with open(args.plan_file, encoding="utf-8") as handle:
        plan = json.load(handle)
    client = S3.Client(plan["endpoint"], plan["bucket"], access_key, secret_key, region=REGION)
    upload(plan, client)
    print(f"Published {plan['package_name']} {plan['version']}.")
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--path", default="")
    plan.add_argument("--package-name", default="")
    plan.add_argument("--version", default="")
    plan.add_argument("--kinds", default="")
    plan.add_argument("--bucket", default="")
    plan.add_argument("--endpoint", default="")
    plan.add_argument("--write-manifest", default="true")
    plan.add_argument("--dry-run", default="false")
    plan.add_argument("--has-credentials", default="false")
    plan.add_argument("--out-dir", required=True)
    upload_parser = commands.add_parser("upload")
    upload_parser.add_argument("--plan-file", required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return command_plan(args) if args.command == "plan" else command_upload(args)
    except (InputError, S3.S3Error) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
