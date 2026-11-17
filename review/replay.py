from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayResult:
    batch_id: str
    record_id: str
    status: int
    headers: tuple
    body: bytes
    text: str
    raw_digest: str
    transfer_digest: str
    body_digest: str
    source_chain: tuple = ()


class ReplayService:
    def __init__(self, verifier):
        self.verifier = verifier

    def replay(self, batch_id, record_id):
        verified = self.verifier.verify_record(batch_id, record_id)
        value = verified.interpreted
        evidence = value.evidence()
        return ReplayResult(batch_id, record_id, value.status, value.headers, value.body,
                            verified.text_view.text, verified.raw_digest,
                            evidence["transfer_digest"], evidence["body_digest"],
                            verified.source_chain)
