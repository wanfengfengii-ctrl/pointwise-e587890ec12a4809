from dataclasses import dataclass
from warc.writer import write_response_record


@dataclass(frozen=True)
class ExportResult:
    batch_id: str
    records: int
    bytes_written: int


class ExportService:
    def __init__(self, catalog, verifier):
        self.catalog = catalog
        self.verifier = verifier

    def export_batch(self, batch_id, sink):
        snapshot = self.catalog.open_batch(batch_id)
        written = 0
        for stored in snapshot.records:
            verified = self.verifier.verify_record(batch_id, stored.record_id)
            written += write_response_record(verified.record, sink)
        return ExportResult(batch_id, len(snapshot.records), written)
