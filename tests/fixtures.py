import gzip
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from archive.database import ArchiveDatabase
from archive.bodies import BodyStore
from archive.catalog import Catalog
from archive.publication import Publisher
from warc.importer import ResponseImporter
from review.verify import VerificationService
from review.replay import ReplayService
from review.export import ExportService
from capture.store import JobStore
from capture.jobs import JobService
from capture.schedule import CaptureRunner

DATE = "2026-01-02T03:04:05Z"
TEXT = "研究资料\n甲乙\n"


def http(body=b"hello", status=200, extra=(), chunked=False, trailer=False):
    fields = [("Content-Type", "text/plain; charset=UTF-8")] + list(extra)
    if chunked:
        fields.append(("Transfer-Encoding", "chunked"))
        pieces = [body[i:i + 7] for i in range(0, len(body), 7)]
        wire = b"".join(format(len(piece), "x").encode() + b"\r\n" + piece + b"\r\n" for piece in pieces)
        wire += b"0\r\n" + (b"X-Archive: complete\r\n" if trailer else b"") + b"\r\n"
    else:
        fields.append(("Content-Length", str(len(body))))
        wire = body
    return ("HTTP/1.1 " + str(status) + " Result\r\n").encode() + b"".join((k + ": " + v + "\r\n").encode() for k, v in fields) + b"\r\n" + wire


def packet(raw, rid="<urn:test:a>", target="https://example.test/a", fields=None):
    if fields is None:
        fields = [
            ("WARC-Type", "response"), ("WARC-Record-ID", rid),
            ("WARC-Target-URI", target), ("WARC-Date", DATE),
            ("Content-Type", "application/http; msgtype=response"),
            ("Content-Length", str(len(raw))),
        ]
    return b"WARC/1.1\r\n" + b"".join((k + ": " + v + "\r\n").encode() for k, v in fields) + b"\r\n" + raw + b"\r\n\r\n"


def split_bytes(data):
    for value in data:
        yield b""
        yield bytes([value])
    yield b""


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Stack:
    def __init__(self, root, fault=None):
        self.root = Path(root)
        self.db = ArchiveDatabase(self.root)
        self.bodies = BodyStore(self.root)
        self.catalog = Catalog(self.db)
        self.publisher = Publisher(self.db, self.bodies, fault=fault)
        self.importer = ResponseImporter(self.publisher)
        self.verifier = VerificationService(self.catalog, self.bodies)
        self.replay = ReplayService(self.verifier)
        self.exporter = ExportService(self.catalog, self.verifier)

    def packed(self, batch="base", raw=None, rid="<urn:test:a>"):
        raw = http() if raw is None else raw
        return self.importer.import_responses([packet(raw, rid)], batch)


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="capture-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stack = Stack(self.root / "archive")

    def jobs(self):
        store = JobStore(self.root / "tasks.sqlite3", clock=lambda: DATE)
        return store, JobService(store), CaptureRunner(store, self.stack.publisher)


class ShortSink:
    def __init__(self, limit=3, stop=None, explode=False):
        self.output = bytearray()
        self.limit = limit
        self.stop = stop
        self.explode = explode

    def write(self, data):
        if self.stop is not None and len(self.output) >= self.stop:
            if self.explode:
                raise OSError("sink interrupted")
            return 0
        size = min(self.limit, len(data))
        if self.stop is not None:
            size = min(size, self.stop - len(self.output))
        self.output.extend(data[:size])
        return size


def scenario(case_id, input_value, expected, actual, downstream_expected, downstream_actual, code_paths, oracle):
    print("POINTWISE_SCENARIO " + json.dumps({
        "case_id": case_id, "input": input_value, "expected": expected, "actual": actual,
        "downstream_expected": downstream_expected, "downstream_actual": downstream_actual,
        "code_paths": code_paths, "oracle": oracle}, ensure_ascii=True, sort_keys=True))
