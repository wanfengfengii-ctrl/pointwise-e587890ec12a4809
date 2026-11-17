from dataclasses import dataclass
from warc.headers import WarcHeaders, validate_response_headers, WarcHeaderError


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
