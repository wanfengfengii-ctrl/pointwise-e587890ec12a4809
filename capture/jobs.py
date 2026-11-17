import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class CaptureRequest:
    job_id: str
    target: str
    method: str = "GET"
    headers: tuple = ()
    body: bytes = b""


def normalize_request(request):
    if not isinstance(request, CaptureRequest) or not isinstance(request.job_id, str) or not request.job_id:
        raise ValueError("stable job ID required")
    if any(ord(c) < 32 for c in request.job_id):
        raise ValueError("invalid job ID")
    if not isinstance(request.target, str) or not request.target.isascii() or any(c.isspace() for c in request.target):
        raise ValueError("ASCII target URI required")
    target = urlsplit(request.target)
    if target.scheme not in ("http", "https") or not target.netloc:
        raise ValueError("HTTP target required")
    if not isinstance(request.method, str) or not re.fullmatch(r"[A-Za-z]+", request.method):
        raise ValueError("invalid method")
    if not isinstance(request.body, bytes):
        raise ValueError("request body must be bytes")
    headers = []
    for pair in request.headers:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError("invalid request field")
        name, value = pair
        if not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise ValueError("invalid request header name")
        if not isinstance(value, str) or any(ord(c) < 32 and c != "\t" or ord(c) > 126 for c in value):
            raise ValueError("invalid request header value")
        headers.append((name.lower(), value.strip(" \t")))
    return CaptureRequest(request.job_id, request.target, request.method.upper(), tuple(headers), request.body)


def request_document(request):
    request = normalize_request(request)
    return {"target": request.target, "method": request.method, "headers": request.headers,
            "body_hex": request.body.hex(), "body_digest": hashlib.sha256(request.body).hexdigest()}


def request_fingerprint(request):
    payload = json.dumps(request_document(request), sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


class JobService:
    def __init__(self, store):
        self.store = store

    def submit(self, requests):
        prepared = []
        for request in requests:
            request = normalize_request(request)
            document = json.dumps(request_document(request), sort_keys=True, separators=(",", ":"))
            prepared.append((request.job_id, request_fingerprint(request), document))
        return self.store.accept(prepared)
