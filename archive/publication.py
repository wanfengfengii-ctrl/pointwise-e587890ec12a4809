import hashlib
from archive.staging import canonical_json
from archive.catalog import Catalog


class BatchConflict(ValueError):
    pass


def batch_fingerprint(rows):
    manifest = [{"record_id": row.record_id, "warc": row.warc_fields,
                 "raw_digest": row.ref.digest, "raw_size": row.ref.size,
                 "evidence": row.evidence_json, "text_hex": row.text_bytes.hex()} for row in rows]
    return hashlib.sha256(canonical_json({"version": 1, "records": manifest}).encode("ascii")).hexdigest()


class Publisher:
    def __init__(self, database, bodies, fault=None):
        self.database = database
        self.bodies = bodies
        self.catalog = Catalog(database)
        self.fault = fault or (lambda event: None)

    def publish(self, staged, receipt=None):
        rows = staged.validate()
        fingerprint = batch_fingerprint(rows)
        if receipt is not None:
            if set(receipt) != {"job_id", "request_digest", "record_id"} or len(rows) != 1 or rows[0].record_id != receipt["record_id"]:
                raise ValueError("invalid capture receipt")
            if any(not isinstance(value, str) or not value for value in receipt.values()):
                raise ValueError("invalid capture receipt field")
        for row in rows:
            self.bodies.install(row.candidate, row.ref)
        with self.database.transaction() as connection:
            old = connection.execute("SELECT fingerprint FROM batches WHERE batch_id=?", (staged.batch_id,)).fetchone()
            if old is not None and old[0] != fingerprint:
                raise BatchConflict("batch ID already has different content")
            if old is None:
                connection.execute("INSERT INTO batches VALUES (?,?)", (staged.batch_id, fingerprint))
                for ordinal, row in enumerate(rows):
                    connection.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?)", (
                        staged.batch_id, ordinal, row.record_id, canonical_json(row.warc_fields),
                        row.ref.digest, row.ref.size, row.evidence_json, row.text_bytes))
            if receipt is not None:
                expected = (receipt["job_id"], receipt["request_digest"], staged.batch_id, receipt["record_id"])
                existing = connection.execute("SELECT job_id,request_digest,batch_id,record_id FROM receipts WHERE job_id=?", (receipt["job_id"],)).fetchone()
                if existing is not None and tuple(existing) != expected:
                    raise BatchConflict("capture receipt conflicts")
                if existing is None:
                    connection.execute("INSERT INTO receipts VALUES (?,?,?,?)", expected)
            self.fault("before_commit")
        staged.state = "published"
        self.fault("after_commit")
        return self.catalog.open_batch(staged.batch_id)
