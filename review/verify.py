from dataclasses import dataclass
from httpdata.pipeline import interpret_response
from archive.staging import canonical_json
from warc.record import ResponseRecord
from warc.headers import WarcHeaders
from review.texts import compare_text


class RecordVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedRecord:
    batch_id: str
    record: object
    interpreted: object
    text_view: object
    raw_length: int
    raw_digest: str
    consistent: bool


class VerificationService:
    def __init__(self, catalog, bodies):
        self.catalog = catalog
        self.bodies = bodies

    def verify_record(self, batch_id, record_id):
        stored = self.catalog.get_record(batch_id, record_id)
        try:
            raw = self.bodies.read_verified(stored.ref)
            record = ResponseRecord(WarcHeaders(stored.warc_fields), raw)
            if record.record_id != stored.record_id:
                raise ValueError("record identity mismatch")
            interpreted = interpret_response(raw)
            if canonical_json(interpreted.evidence()) != stored.evidence_json:
                raise ValueError("source interpretation differs from catalog")
            view = compare_text(stored.text_bytes, stored.evidence, interpreted.text)
            return VerifiedRecord(batch_id, record, interpreted, view, len(raw), stored.ref.digest, True)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise RecordVerificationError("record evidence failed: " + record_id) from exc
