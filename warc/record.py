from dataclasses import dataclass
from warc.headers import WarcHeaders, validate_response_headers, validate_capture_headers, WarcHeaderError


@dataclass(frozen=True)
class ResponseRecord:
    headers: WarcHeaders
    raw: bytes

    def __post_init__(self):
        validate_response_headers(self.headers)
        if not isinstance(self.raw, bytes) or self.headers.content_length != len(self.raw):
            raise WarcHeaderError("record length differs from HTTP bytes")

    @property
    def record_id(self):
        return self.headers.one("WARC-Record-ID")

    @property
    def refers_to(self):
        return None

    @property
    def is_revisit(self):
        return False


@dataclass(frozen=True)
class CaptureRecord:
    headers: WarcHeaders
    raw: bytes

    def __post_init__(self):
        validate_capture_headers(self.headers)
        if not isinstance(self.raw, bytes) or self.headers.content_length != len(self.raw):
            raise WarcHeaderError("record length differs from HTTP bytes")

    @property
    def record_id(self):
        return self.headers.one("WARC-Record-ID")

    @property
    def refers_to(self):
        values = self.headers.values("WARC-Refers-To")
        return values[0] if values else None

    @property
    def is_revisit(self):
        return self.headers.one("WARC-Type") == "revisit"


def record_from_fields(warc_fields, raw):
    headers = WarcHeaders(warc_fields)
    if (headers.values("WARC-Type") == ("response",)
            and not headers.values("WARC-Refers-To")
            and not headers.values("WARC-Profile")
            and not headers.values("WARC-Payload-Digest")):
        return ResponseRecord(headers, raw)
    return CaptureRecord(headers, raw)


class RecordIdentitySet:
    def __init__(self):
        self._ids = set()

    def add(self, record_id):
        if record_id in self._ids:
            raise WarcHeaderError("duplicate Record-ID")
        self._ids.add(record_id)


def build_response_record(record_id, target, captured_at, raw):
    headers = WarcHeaders((
        ("WARC-Type", "response"), ("WARC-Record-ID", record_id),
        ("WARC-Target-URI", target), ("WARC-Date", captured_at),
        ("Content-Type", "application/http; msgtype=response"),
        ("Content-Length", str(len(raw))),
    ))
    return ResponseRecord(headers, raw)
