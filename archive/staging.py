import tempfile
from dataclasses import dataclass
from warc.record import RecordIdentitySet, ResponseRecord
from archive.bodies import BodyRef
from httpdata.pipeline import interpret_response, interpretation_fingerprint


@dataclass(frozen=True)
class StagedResponse:
    record_id: str
    warc_fields: tuple
    ref: BodyRef
    candidate: object
    evidence_json: str
    text_bytes: bytes


def canonical_json(value):
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class StagedBatch:
    def __init__(self, bodies, batch_id):
        if not isinstance(batch_id, str) or not batch_id or any(ord(c) < 32 for c in batch_id):
            raise ValueError("invalid batch ID")
        self.batch_id = batch_id
        self.bodies = bodies
        self.rows = []
        self.identities = RecordIdentitySet()
        self.state = "staging"
        self._temporary = tempfile.TemporaryDirectory(prefix="batch-", dir=bodies.staging)

    def add_response(self, record, interpreted):
        if self.state != "staging" or not isinstance(record, ResponseRecord):
            raise ValueError("staging requires a complete response")
        if interpreted.raw != record.raw:
            raise ValueError("interpretation belongs to different source")
        actual = interpret_response(record.raw)
        if actual != interpreted:
            raise ValueError("inconsistent interpretation")
        self.identities.add(record.record_id)
        ref, candidate = self.bodies.stage_raw(record.raw, self._temporary.name)
        self.rows.append(StagedResponse(
            record.record_id, record.headers.fields, ref, candidate,
            canonical_json(actual.evidence()), actual.text.encode("utf-8")))

    def validate(self):
        if self.state != "staging" or not self.rows:
            raise ValueError("nonempty open staging required")
        for row in self.rows:
            raw = self.bodies._read(row.candidate, row.ref)
            value = interpret_response(raw)
            if canonical_json(value.evidence()) != row.evidence_json or value.text.encode("utf-8") != row.text_bytes:
                raise ValueError("staged evidence changed")
            from warc.headers import WarcHeaders
            ResponseRecord(WarcHeaders(row.warc_fields), raw)
        return tuple(self.rows)

    def abort(self):
        if self.state == "staging":
            self.state = "aborted"
        self._temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.abort()
