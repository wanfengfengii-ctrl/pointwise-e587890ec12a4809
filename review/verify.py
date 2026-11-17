from dataclasses import dataclass
from archive.staging import canonical_json
from warc.record import record_from_fields
from warc.capture import interpret_capture_records
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
    source_chain: tuple = ()


class VerificationService:
    def __init__(self, catalog, bodies):
        self.catalog = catalog
        self.bodies = bodies

    def verify_record(self, batch_id, record_id):
        snapshot = self.catalog.open_batch(batch_id)
        stored = snapshot.get_record(record_id)
        try:
            chain = [stored]
            seen = {stored.record_id}
            while chain[-1].refers_to is not None:
                member = snapshot.get_record(chain[-1].refers_to)
                if member.record_id in seen:
                    raise ValueError("stored relations form a cycle")
                seen.add(member.record_id)
                chain.append(member)
            records = []
            raws = []
            for link in chain:
                raw = self.bodies.read_verified(link.ref)
                record = record_from_fields(link.warc_fields, raw)
                if record.record_id != link.record_id:
                    raise ValueError("record identity mismatch")
                if record.refers_to != link.refers_to:
                    raise ValueError("stored relation differs from record")
                records.append(record)
                raws.append(raw)
            interpreted = interpret_capture_records(records)[0]
            if canonical_json(interpreted.evidence()) != stored.evidence_json:
                raise ValueError("source interpretation differs from catalog")
            view = compare_text(stored.text_bytes, stored.evidence, interpreted.text)
            return VerifiedRecord(batch_id, records[0], interpreted, view, len(raws[0]),
                                  stored.ref.digest, True,
                                  tuple(link.record_id for link in chain))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise RecordVerificationError("record evidence failed: " + record_id) from exc
