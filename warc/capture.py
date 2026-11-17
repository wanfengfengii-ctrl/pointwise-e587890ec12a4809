from httpdata.pipeline import interpret_response, interpret_revisit, sha256


class CaptureChainError(ValueError):
    pass


def source_chains(records):
    refers = {}
    for record in records:
        if record.record_id in refers:
            raise CaptureChainError("duplicate Record-ID")
        refers[record.record_id] = record.refers_to
    chains = {}
    for record in records:
        chain = [record.record_id]
        seen = {record.record_id}
        current = record.record_id
        while refers[current] is not None:
            target = refers[current]
            if target not in refers:
                raise CaptureChainError("reference target is not in this batch")
            if target in seen:
                raise CaptureChainError("reference cycle in batch")
            seen.add(target)
            chain.append(target)
            current = target
        chains[record.record_id] = tuple(chain)
    return chains


def check_payload_digest(record, transfer_body):
    declared = record.headers.values("WARC-Payload-Digest")
    if declared and declared[0] != "sha256:" + sha256(transfer_body):
        raise CaptureChainError("payload digest differs from inherited body")


def interpret_capture_records(records):
    records = list(records)
    chains = source_chains(records)
    interpreted = {}
    for record in records:
        if not record.is_revisit:
            value = interpret_response(record.raw)
            check_payload_digest(record, value.transfer_body)
            interpreted[record.record_id] = value
    for record in records:
        if record.is_revisit:
            entity = interpreted[chains[record.record_id][-1]]
            value = interpret_revisit(record.raw, entity.transfer_body)
            check_payload_digest(record, value.transfer_body)
            interpreted[record.record_id] = value
    return [interpreted[record.record_id] for record in records]
