import json
from dataclasses import dataclass
from archive.bodies import BodyRef


class BatchNotFound(KeyError):
    pass


class RecordNotFound(KeyError):
    pass


@dataclass(frozen=True)
class StoredRecord:
    record_id: str
    warc_fields: tuple
    ref: BodyRef
    evidence_json: str
    text_bytes: bytes
    refers_to: object = None

    @property
    def evidence(self):
        return json.loads(self.evidence_json)


@dataclass(frozen=True)
class BatchSnapshot:
    batch_id: str
    fingerprint: str
    records: tuple

    @property
    def record_ids(self):
        return tuple(row.record_id for row in self.records)

    def get_record(self, record_id):
        for row in self.records:
            if row.record_id == record_id:
                return row
        raise RecordNotFound(record_id)


class Catalog:
    def __init__(self, database):
        self.database = database

    def open_batch(self, batch_id):
        with self.database.open_readonly() as connection:
            connection.execute("BEGIN")
            batch = connection.execute("SELECT fingerprint FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if batch is None:
                raise BatchNotFound(batch_id)
            rows = connection.execute("SELECT * FROM records WHERE batch_id=? ORDER BY ordinal", (batch_id,)).fetchall()
            links = {row["record_id"]: row["refers_to"] for row in
                     connection.execute("SELECT record_id, refers_to FROM links WHERE batch_id=?", (batch_id,)).fetchall()}
            records = tuple(StoredRecord(
                row["record_id"], tuple(tuple(pair) for pair in json.loads(row["warc_json"])),
                BodyRef(row["raw_digest"], row["raw_size"]),
                row["evidence_json"], bytes(row["text_bytes"]),
                links.get(row["record_id"])) for row in rows)
            return BatchSnapshot(batch_id, batch[0], records)

    def get_record(self, batch_id, record_id):
        return self.open_batch(batch_id).get_record(record_id)

    def find_capture_receipt(self, job_id):
        with self.database.open_readonly() as connection:
            row = connection.execute("SELECT * FROM receipts WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row is not None else None
