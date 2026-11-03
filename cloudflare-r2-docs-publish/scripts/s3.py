"""Minimal S3 client for an S3-compatible endpoint (Cloudflare R2 and others).

Three operations, all path-style (<endpoint>/<bucket>/<key>) and signed with
AWS Signature Version 4:

- prefix_exists: ListObjectsV2 with max-keys=1, to see whether any object
  lives under a prefix;
- put_if_absent: PutObject with "If-None-Match: *", a conditional write that
  the store refuses (412) when the key already exists;
- put: an unconditional PutObject, for a mutable pointer object.

Transient failures (network errors, 429, 5xx) are retried a few times. No
error message carries a credential or a response body: only the HTTP status
and the S3 error code. Redirects are never followed: a 3xx answer is an
error, so the signed headers never reach another host or scheme.

Loaded by publish.py through its file path, never imported from sys.path.
Standard library only.
"""

import datetime
import hashlib
import hmac
import re
import time
import urllib.error
import urllib.parse
import urllib.request

SERVICE = "s3"
ALGORITHM = "AWS4-HMAC-SHA256"
UNRESERVED = "-_.~"
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 60
ERROR_CODE_RE = re.compile(rb"<Code>([A-Za-z0-9.]{1,100})</Code>")
KEY_COUNT_RE = re.compile(rb"<KeyCount>([0-9]+)</KeyCount>")
CONTENTS_RE = re.compile(rb"<Contents>")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect; urllib then raises the 3xx as an HTTPError."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


class S3Error(Exception):
    """A request that failed. The message names the operation, never a secret."""


class AlreadyExists(S3Error):
    """A conditional write found an existing object under the key."""


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def uri_encode(value, keep_slash=False):
    """Percent-encodes a value the way SigV4 canonicalizes S3 paths and queries."""
    safe = UNRESERVED + ("/" if keep_slash else "")
    return urllib.parse.quote(value, safe=safe)


def _hmac(key, message):
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret_key, date, region):
    key = _hmac(("AWS4" + secret_key).encode("utf-8"), date)
    key = _hmac(key, region)
    key = _hmac(key, SERVICE)
    return _hmac(key, "aws4_request")


def canonical_query(query):
    pairs = sorted((uri_encode(k), uri_encode(v)) for k, v in query.items())
    return "&".join(f"{k}={v}" for k, v in pairs)


def sign(method, path, query, headers, payload_hash, access_key, secret_key, region, amz_date):
    """Returns the Authorization header value.

    path is already URI-encoded. headers holds every header to sign (host,
    x-amz-date and x-amz-content-sha256 among them), with any name case.
    """
    lowered = {name.lower(): " ".join(str(value).split()) for name, value in headers.items()}
    names = sorted(lowered)
    canonical_headers = "".join(f"{name}:{lowered[name]}\n" for name in names)
    signed_headers = ";".join(names)
    canonical_request = "\n".join(
        [method, path, canonical_query(query), canonical_headers, signed_headers, payload_hash]
    )
    date = amz_date[:8]
    scope = f"{date}/{region}/{SERVICE}/aws4_request"
    string_to_sign = "\n".join([ALGORITHM, amz_date, scope, sha256_hex(canonical_request.encode("utf-8"))])
    signature = hmac.new(
        signing_key(secret_key, date, region), string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{ALGORITHM} Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"


def _error_code(body):
    match = ERROR_CODE_RE.search(body or b"")
    return match.group(1).decode("ascii") if match else "no error code"


class Client:
    """Path-style S3 client bound to one endpoint and one bucket."""

    def __init__(self, endpoint, bucket, access_key, secret_key, region="auto", sleep=time.sleep, now=None):
        parts = urllib.parse.urlsplit(endpoint)
        self.base = f"{parts.scheme}://{parts.netloc}"
        self.host = parts.netloc
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.sleep = sleep
        self.now = now or (lambda: datetime.datetime.now(datetime.timezone.utc))

    def _request(self, method, key="", query=None, body=b"", extra_headers=None):
        """Sends one signed request; returns (status, headers, body) or raises S3Error."""
        query = query or {}
        path = "/" + uri_encode(self.bucket) + ("/" + uri_encode(key, keep_slash=True) if key else "")
        url = self.base + path + ("?" + canonical_query(query) if query else "")
        payload_hash = sha256_hex(body)
        amz_date = self.now().strftime("%Y%m%dT%H%M%SZ")
        signed = {"host": self.host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        signed.update(extra_headers or {})
        headers = dict(signed)
        headers["Authorization"] = sign(
            method, path, query, signed, payload_hash, self.access_key, self.secret_key, self.region, amz_date
        )
        request = urllib.request.Request(url, data=body if method == "PUT" else None, method=method)
        for name, value in headers.items():
            if name != "host":
                request.add_header(name, value)
        try:
            with OPENER.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def _send(self, what, method, **kwargs):
        """Retries transient failures. Returns (status, headers, body, attempts)."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                status, headers, body = self._request(method, **kwargs)
            except (urllib.error.URLError, OSError) as error:
                reason = type(getattr(error, "reason", error)).__name__
                if attempt == MAX_ATTEMPTS:
                    raise S3Error(f"{what}: network error ({reason}) after {attempt} attempts") from None
            else:
                if status not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                    return status, headers, body, attempt
            self.sleep(RETRY_BASE_SECONDS * 2 ** (attempt - 1))
        raise AssertionError("unreachable")

    def prefix_exists(self, prefix):
        """True when at least one object key starts with prefix. Fails closed."""
        what = f"list {prefix}"
        query = {"list-type": "2", "prefix": prefix, "max-keys": "1"}
        status, _, body, _ = self._send(what, "GET", query=query)
        if status != 200:
            raise S3Error(f"{what}: HTTP {status} ({_error_code(body)})")
        count = KEY_COUNT_RE.search(body)
        if count:
            return int(count.group(1)) > 0
        return bool(CONTENTS_RE.search(body))

    def _etag(self, key):
        status, headers, body, _ = self._send(f"head {key}", "HEAD", key=key)
        if status != 200:
            raise S3Error(f"head {key}: HTTP {status} ({_error_code(body)})")
        return (headers.get("ETag") or "").strip('"')

    def put_if_absent(self, key, body, content_type):
        """Creates key; raises AlreadyExists when the key exists.

        When a retry follows an attempt whose outcome is unknown, a 412 may
        mean that the earlier attempt wrote the object. The object is then
        accepted only when its ETag is the MD5 of body (single-part upload).
        """
        what = f"put {key}"
        headers = {"content-type": content_type, "if-none-match": "*"}
        status, _, response, attempts = self._send(what, "PUT", key=key, body=body, extra_headers=headers)
        if status == 200:
            return
        if status == 412:
            if attempts > 1 and self._etag(key) == hashlib.md5(body, usedforsecurity=False).hexdigest():
                return
            raise AlreadyExists(f"{what}: the object already exists")
        raise S3Error(f"{what}: HTTP {status} ({_error_code(response)})")

    def put(self, key, body, content_type):
        """Writes key, replacing any object under it."""
        what = f"put {key}"
        headers = {"content-type": content_type}
        status, _, response, _ = self._send(what, "PUT", key=key, body=body, extra_headers=headers)
        if status != 200:
            raise S3Error(f"{what}: HTTP {status} ({_error_code(response)})")
