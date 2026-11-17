from warc.stream import ChunkReader, WarcStreamError
from warc.headers import parse_warc_headers
from warc.record import ResponseRecord, RecordIdentitySet
from httpdata.pipeline import interpret_response
from archive.staging import StagedBatch


class ResponseImporter:
    def __init__(self, publisher):
        self.publisher = publisher

    def import_responses(self, chunks, batch_id):
        reader = ChunkReader(chunks)
        identities = RecordIdentitySet()
        with StagedBatch(self.publisher.bodies, batch_id) as staged:
            while not reader.at_eof():
                if reader.read_line() != b"WARC/1.1":
                    raise WarcStreamError("expected WARC/1.1")
                lines = []
                while True:
                    line = reader.read_line()
                    if not line:
                        break
                    lines.append(line)
                headers = parse_warc_headers(lines)
                identities.add(headers.one("WARC-Record-ID"))
                raw = reader.read_block(headers.content_length)
                if reader.read_exact(4) != b"\r\n\r\n":
                    raise WarcStreamError("invalid record separator")
                record = ResponseRecord(headers, raw)
                staged.add_response(record, interpret_response(raw))
            return self.publisher.publish(staged)
