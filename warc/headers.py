import re
import ipaddress
from datetime import datetime
from dataclasses import dataclass
from urllib.parse import urlsplit


class WarcHeaderError(ValueError):
    pass


@dataclass(frozen=True)
class WarcHeaders:
    fields: tuple

    def values(self, name):
        return tuple(v for k, v in self.fields if k.lower() == name.lower())

    def one(self, name):
        values = self.values(name)
        if len(values) != 1:
            raise WarcHeaderError("missing or duplicate " + name)
        return values[0]

    @property
    def content_length(self):
        value = self.one("Content-Length")
        if not re.fullmatch(r"[0-9]+", value):
            raise WarcHeaderError("invalid WARC byte length")
        return int(value)


RESPONSE_FIELDS = {"warc-type", "warc-record-id", "warc-target-uri", "warc-date",
                   "content-type", "content-length", "warc-ip-address"}
CAPTURE_FIELDS = RESPONSE_FIELDS | {"warc-payload-digest", "warc-refers-to", "warc-profile"}
REVISIT_PROFILE = "http://netpreserve.org/warc/1.1/revisit/identical-payload-digest"
RECORD_ID_RULE = r"<[A-Za-z][A-Za-z0-9+.-]*:[^<>\s]+>"
PAYLOAD_DIGEST_RULE = r"sha256:[0-9a-f]{64}"


def _validate_common(headers, supported):
    for name, value in headers.fields:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9-]+", name):
            raise WarcHeaderError("invalid WARC field name")
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) > 126 for c in value):
            raise WarcHeaderError("WARC field must contain printable ASCII")
        if name.lower() not in supported:
            raise WarcHeaderError("unsupported WARC field")
    addresses = headers.values("WARC-IP-Address")
    if len(addresses) > 1:
        raise WarcHeaderError("duplicate source address")
    if addresses:
        try:
            ipaddress.ip_address(addresses[0])
        except ValueError as exc:
            raise WarcHeaderError("invalid source IP address") from exc
    if not re.fullmatch(RECORD_ID_RULE, headers.one("WARC-Record-ID")):
        raise WarcHeaderError("invalid Record-ID")
    target = headers.one("WARC-Target-URI")
    parsed = urlsplit(target)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or any(c.isspace() for c in target):
        raise WarcHeaderError("invalid target URI")
    stamp = headers.one("WARC-Date")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", stamp):
        raise WarcHeaderError("UTC date required")
    try:
        datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise WarcHeaderError("invalid capture date") from exc
    if headers.one("Content-Type").lower() != "application/http; msgtype=response":
        raise WarcHeaderError("HTTP response block type required")
    headers.content_length


def validate_response_headers(headers):
    _validate_common(headers, RESPONSE_FIELDS)
    if headers.one("WARC-Type") != "response":
        raise WarcHeaderError("only self-contained response is supported")
    return headers


def validate_capture_headers(headers):
    _validate_common(headers, CAPTURE_FIELDS)
    record_type = headers.one("WARC-Type")
    digests = headers.values("WARC-Payload-Digest")
    if len(digests) > 1:
        raise WarcHeaderError("duplicate payload digest")
    if digests and not re.fullmatch(PAYLOAD_DIGEST_RULE, digests[0]):
        raise WarcHeaderError("invalid payload digest")
    if record_type == "response":
        if headers.values("WARC-Refers-To") or headers.values("WARC-Profile"):
            raise WarcHeaderError("response must not carry reference or profile")
    elif record_type == "revisit":
        if not re.fullmatch(RECORD_ID_RULE, headers.one("WARC-Refers-To")):
            raise WarcHeaderError("invalid reference target")
        if headers.one("WARC-Profile") != REVISIT_PROFILE:
            raise WarcHeaderError("unsupported revisit profile")
        if not digests:
            raise WarcHeaderError("revisit requires a payload digest")
    else:
        raise WarcHeaderError("only response and revisit are supported")
    return headers


def _parse_field_lines(lines):
    fields = []
    for line in lines:
        try:
            name, value = line.decode("ascii").split(":", 1)
        except (UnicodeError, ValueError) as exc:
            raise WarcHeaderError("invalid WARC field") from exc
        fields.append((name, value.strip(" ")))
    return fields


def parse_warc_headers(lines):
    return validate_response_headers(WarcHeaders(tuple(_parse_field_lines(lines))))


def parse_capture_headers(lines):
    return validate_capture_headers(WarcHeaders(tuple(_parse_field_lines(lines))))


def render_warc_headers(headers, length):
    validate_response_headers(headers)
    if type(length) is not int or length < 0:
        raise WarcHeaderError("invalid output length")
    fields = [(k, str(length) if k.lower() == "content-length" else v) for k, v in headers.fields]
    return b"WARC/1.1\r\n" + b"".join((k + ": " + v + "\r\n").encode("ascii") for k, v in fields) + b"\r\n"
