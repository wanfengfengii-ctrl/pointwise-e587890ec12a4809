import re
from dataclasses import dataclass

TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class HttpResponseError(ValueError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: tuple
    wire_body: bytes


def parse_field(line):
    if b":" not in line or line[:1] in (b" ", b"\t"):
        raise HttpResponseError("invalid or folded HTTP field")
    name, value = line.split(b":", 1)
    try:
        name = name.decode("ascii")
        value = value.decode("latin-1").strip(" \t")
    except UnicodeError as exc:
        raise HttpResponseError("invalid HTTP field name") from exc
    if not TOKEN.fullmatch(name) or any(ord(c) < 32 and c != "\t" or ord(c) == 127 for c in value):
        raise HttpResponseError("invalid HTTP field")
    return name, value


def header_values(headers, name):
    return tuple(v for k, v in headers if k.lower() == name.lower())


def body_framing(headers):
    lengths = header_values(headers, "content-length")
    transfers = header_values(headers, "transfer-encoding")
    if lengths and transfers:
        raise HttpResponseError("ambiguous HTTP boundary")
    if transfers:
        if len(transfers) != 1 or transfers[0].lower() != "chunked":
            raise HttpResponseError("unsupported transfer encoding")
        return "chunked", None
    values = [part.strip() for field in lengths for part in field.split(",")]
    if not values or any(not re.fullmatch(r"[0-9]+", v) for v in values):
        raise HttpResponseError("explicit body length required")
    sizes = {int(v) for v in values}
    if len(sizes) != 1:
        raise HttpResponseError("conflicting HTTP lengths")
    return "length", sizes.pop()


def parse_response(raw):
    if not isinstance(raw, bytes):
        raise HttpResponseError("HTTP response must be bytes")
    head, separator, body = raw.partition(b"\r\n\r\n")
    if not separator:
        raise HttpResponseError("incomplete HTTP headers")
    lines = head.split(b"\r\n")
    match = re.fullmatch(rb"HTTP/1\.[01] ([2-5][0-9]{2}) ([\x20-\x7e]*)", lines[0])
    if not match:
        raise HttpResponseError("invalid final status line")
    headers = tuple(parse_field(line) for line in lines[1:])
    body_framing(headers)
    return HttpResponse(int(match[1]), headers, body)
