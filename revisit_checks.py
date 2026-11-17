"""Independent WARC bytes and public business checks; no revisit implementation."""
import argparse
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tests.fixtures import Stack, http, packet, split_bytes
from archive.catalog import BatchNotFound
from archive.publication import BatchConflict
from review.verify import RecordVerificationError

PROFILE = "http://netpreserve.org/warc/1.1/revisit/identical-payload-digest"
TEXT = "研究资料\n甲乙\n"
DATE = "2026-01-02T03:04:05Z"


def rid(name):
    return "<urn:revisit:" + name + ">"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def revisit(name, target, payload, status=203, encoding="gzip",
            expected_digest=None, declared_length=None, profile=PROFILE):
    headers = [
        ("Content-Type", "text/plain; charset=UTF-8"),
        ("Content-Encoding", encoding),
        ("Content-Length", str(len(payload) if declared_length is None else declared_length)),
        ("X-Capture", name),
    ]
    raw = ("HTTP/1.1 " + str(status) + " Revisit\r\n").encode("ascii")
    raw += b"".join((k + ": " + v + "\r\n").encode("ascii") for k, v in headers)
    raw += b"\r\n"
    fields = [
        ("WARC-Type", "revisit"), ("WARC-Record-ID", rid(name)),
        ("WARC-Target-URI", "https://example.test/" + name), ("WARC-Date", DATE),
        ("Content-Type", "application/http; msgtype=response"),
        ("Content-Length", str(len(raw))),
        ("WARC-Refers-To", rid(target)), ("WARC-Profile", profile),
        ("WARC-Payload-Digest", "sha256:" + (digest(payload) if expected_digest is None else expected_digest)),
    ]
    return packet(raw, fields=fields), raw


def joint_fixture(target="B"):
    payload = gzip.compress(TEXT.encode("utf-8"), mtime=0)
    raw = http(payload, 200, (("Content-Encoding", "gzip"), ("X-Capture", "entity")), True, True)
    s_packet, s_raw = revisit("S", "R", payload, status=202)
    r_packet, r_raw = revisit("R", target, payload)
    data = s_packet + r_packet + packet(raw, rid("A")) + packet(raw, rid("B"))
    return {
        "data": data, "payload": payload,
        "raws": {"S": s_raw, "R": r_raw, "A": raw, "B": raw},
        "chains": {"S": (rid("S"), rid("R"), rid(target)),
                   "R": (rid("R"), rid(target)), "A": (rid("A"),), "B": (rid("B"),)},
        "statuses": {"S": 202, "R": 203, "A": 200, "B": 200},
    }


def selfcheck():
    from warc.stream import ChunkReader
    from httpdata.pipeline import interpret_response
    from httpdata.response import parse_response
    fixture = joint_fixture()
    reader = ChunkReader(split_bytes(fixture["data"]))
    names = iter(("S", "R", "A", "B"))
    while not reader.at_eof():
        name = next(names)
        assert reader.read_line() == b"WARC/1.1"
        fields = {}
        while True:
            line = reader.read_line()
            if not line:
                break
            key, value = line.decode("ascii").split(": ", 1)
            fields[key] = value
        raw = reader.read_block(int(fields["Content-Length"]))
        assert raw == fixture["raws"][name]
        assert fields["WARC-Record-ID"] == rid(name)
        assert reader.read_exact(4) == b"\r\n\r\n"
        if name in ("S", "R"):
            parsed = parse_response(raw)
            assert parsed.wire_body == b""
            assert int(dict(parsed.headers)["Content-Length"]) == len(fixture["payload"])
            assert fields["WARC-Payload-Digest"] == "sha256:" + digest(fixture["payload"])
        else:
            value = interpret_response(raw)
            assert value.transfer_body == fixture["payload"]
            assert value.text == TEXT
    assert next(names, None) is None
    print("REVISIT_FIXTURE_OK")


def check_joint(stack, fixture):
    rows = []
    for name in ("S", "R", "A", "B"):
        result = stack.replay.replay("joint", rid(name))
        verified = stack.verifier.verify_record("joint", rid(name))
        assert result.status == fixture["statuses"][name]
        assert result.text == TEXT and result.body == TEXT.encode("utf-8")
        assert result.source_chain == fixture["chains"][name]
        assert verified.source_chain == fixture["chains"][name]
        assert verified.consistent is True
        assert verified.record.raw == fixture["raws"][name]
        assert verified.raw_length == len(fixture["raws"][name])
        assert verified.raw_digest == digest(fixture["raws"][name])
        assert result.raw_digest == verified.raw_digest
        assert result.transfer_digest == digest(fixture["payload"])
        assert result.body_digest == digest(TEXT.encode("utf-8"))
        if name in ("R", "S"):
            assert ("X-Capture", name) in result.headers
            assert not any(k.lower() == "transfer-encoding" for k, _ in result.headers)
        rows.append({"id": rid(name), "status": result.status, "text": result.text,
                     "source_chain": result.source_chain, "transfer_digest": result.transfer_digest})
    return rows


class RevisitContract(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="revisit-check-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stack = Stack(self.root / "archive")

    def test_forward_chain_and_fixed_identity(self):
        fixture = joint_fixture()
        first = self.stack.importer.import_capture(split_bytes(fixture["data"]), "joint")
        self.assertEqual(first.record_ids, tuple(rid(n) for n in ("S", "R", "A", "B")))
        second = Stack(self.stack.root).importer.import_capture([fixture["data"]], "joint")
        self.assertEqual(first.fingerprint, second.fingerprint)
        check_joint(Stack(self.stack.root), fixture)
        with self.assertRaises(BatchConflict):
            self.stack.importer.import_capture([joint_fixture("A")["data"]], "joint")
        self.assertEqual(Stack(self.stack.root).catalog.open_batch("joint").fingerprint, first.fingerprint)
        check_joint(Stack(self.stack.root), fixture)

    def test_batch_rejection_preserves_old(self):
        old = self.stack.packed("old")
        payload = gzip.compress(TEXT.encode("utf-8"), mtime=0)
        base = packet(http(payload, extra=(("Content-Encoding", "gzip"),), chunked=True), rid("B"))
        cycle1, _ = revisit("R", "S", payload)
        cycle2, _ = revisit("S", "R", payload)
        good, _ = revisit("R", "B", payload)
        cases = [
            revisit("R", "absent", payload)[0] + base,
            cycle1 + cycle2 + base,
            good + good + base,
            revisit("R", "B", payload, expected_digest="0" * 64)[0] + base,
            revisit("R", "B", payload, declared_length=len(payload) + 1)[0] + base,
            revisit("R", "B", payload, profile="https://example.test/unsupported")[0] + base,
            revisit("R", "B", payload, encoding="identity")[0] + base,
            (good + base)[:-1],
            good.replace(b"charset=UTF-8", b"charset=ascii") + base,
            revisit("R", "B", b"plain", encoding="gzip")[0] + packet(http(b"plain"), rid("B")),
            revisit("R", "B", payload)[0] + base.replace(b"WARC-Type: response", b"WARC-Type: metadata"),
        ]
        for index, data in enumerate(cases):
            batch = "bad-" + str(index)
            with self.subTest(case=index):
                with self.assertRaises(ValueError):
                    self.stack.importer.import_capture(split_bytes(data), batch)
                with self.assertRaises(BatchNotFound):
                    Stack(self.stack.root).catalog.open_batch(batch)
                self.assertEqual(self.stack.catalog.open_batch("old"), old)
                self.assertEqual(self.stack.replay.replay("old", old.record_ids[0]).text, "hello")
                self.assertEqual(list(self.stack.bodies.staging.iterdir()), [])

    def test_source_and_publication_failure(self):
        fixture = joint_fixture()
        def broken():
            yield fixture["data"]
            raise OSError("source interrupted")
        with self.assertRaises(OSError):
            self.stack.importer.import_capture(broken(), "source-fail")
        with self.assertRaises(BatchNotFound):
            self.stack.catalog.open_batch("source-fail")
        def fail(event):
            if event == "before_commit":
                raise RuntimeError("catalog unavailable")
        self.stack.publisher.fault = fail
        with self.assertRaises(RuntimeError):
            self.stack.importer.import_capture([fixture["data"]], "commit-fail")
        with self.assertRaises(BatchNotFound):
            Stack(self.stack.root).catalog.open_batch("commit-fail")
        self.assertEqual(list(self.stack.bodies.staging.iterdir()), [])

    def test_original_dependency_is_verified(self):
        fixture = joint_fixture()
        self.stack.importer.import_capture([fixture["data"]], "joint")
        check_joint(Stack(self.stack.root), fixture)
        stored = self.stack.catalog.get_record("joint", rid("B"))
        path = self.stack.bodies.object_path(stored.ref)
        original = path.read_bytes()
        for mutation in ("change", "missing"):
            with self.subTest(mutation=mutation):
                if mutation == "change":
                    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
                else:
                    path.unlink()
                fresh = Stack(self.stack.root)
                for action in (fresh.verifier.verify_record, fresh.replay.replay):
                    with self.assertRaises(RecordVerificationError):
                        action("joint", rid("S"))
                path.write_bytes(original)


def demo():
    fixture = joint_fixture()
    with tempfile.TemporaryDirectory(prefix="revisit-demo-") as directory:
        stack = Stack(Path(directory) / "archive")
        stack.importer.import_capture(split_bytes(fixture["data"]), "joint")
        rows = check_joint(Stack(stack.root), fixture)
        print(json.dumps({"batch": "joint", "records": rows}, ensure_ascii=False, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--selfcheck", action="store_true")
    modes.add_argument("--verify", action="store_true")
    modes.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.selfcheck:
        selfcheck()
        return 0
    if args.demo:
        demo()
        return 0
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RevisitContract)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
