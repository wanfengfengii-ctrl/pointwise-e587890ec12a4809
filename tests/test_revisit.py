import gzip
import io
from archive.catalog import BatchNotFound, RecordNotFound
from archive.publication import BatchConflict
from review.verify import RecordVerificationError
from tests.fixtures import BaseTest, Stack, TEXT, DATE, http, packet, split_bytes, digest

PROFILE = "http://netpreserve.org/warc/1.1/revisit/identical-payload-digest"
PAYLOAD = gzip.compress(TEXT.encode("utf-8"), mtime=0)


def response_packet(raw, rid, digest_value=None, extra=()):
    fields = [
        ("WARC-Type", "response"), ("WARC-Record-ID", rid),
        ("WARC-Target-URI", "https://example.test/a"), ("WARC-Date", DATE),
        ("Content-Type", "application/http; msgtype=response"),
        ("Content-Length", str(len(raw))),
    ]
    if digest_value is not None:
        fields.append(("WARC-Payload-Digest", "sha256:" + digest_value))
    return packet(raw, fields=fields + list(extra))


def revisit_head(payload_length, status=203, extra=(), encoding="gzip"):
    fields = [("Content-Type", "text/plain; charset=UTF-8")]
    if encoding is not None:
        fields.append(("Content-Encoding", encoding))
    fields.append(("Content-Length", str(payload_length)))
    fields.extend(extra)
    return ("HTTP/1.1 " + str(status) + " Revisit\r\n").encode("ascii") + b"".join(
        (k + ": " + v + "\r\n").encode("ascii") for k, v in fields) + b"\r\n"


def revisit_packet(rid, target, raw, payload, extra=(), digest_value=None, profile=PROFILE, drop=()):
    fields = [
        ("WARC-Type", "revisit"), ("WARC-Record-ID", rid),
        ("WARC-Target-URI", "https://example.test/r"), ("WARC-Date", DATE),
        ("Content-Type", "application/http; msgtype=response"),
        ("Content-Length", str(len(raw))),
        ("WARC-Refers-To", target), ("WARC-Profile", profile),
        ("WARC-Payload-Digest", "sha256:" + (digest(payload) if digest_value is None else digest_value)),
    ]
    fields = [field for field in fields if field[0] not in drop]
    return packet(raw, fields=fields + list(extra))


class TestRevisit(BaseTest):
    def entity(self, rid="<urn:test:e>", payload=PAYLOAD):
        return packet(http(payload, 200, (("Content-Encoding", "gzip"),), True, True), rid)

    def test_response_payload_digest(self):
        raw = http(PAYLOAD, 203, (("Content-Encoding", "gzip"),), chunked=True)
        batch = self.stack.importer.import_capture(
            [response_packet(raw, "<urn:test:d>", digest(PAYLOAD))], "digest-ok")
        self.assertEqual(batch.record_ids, ("<urn:test:d>",))
        result = self.stack.replay.replay("digest-ok", "<urn:test:d>")
        self.assertEqual((result.status, result.text), (203, TEXT))
        self.assertEqual(result.source_chain, ("<urn:test:d>",))
        self.assertEqual(result.transfer_digest, digest(PAYLOAD))
        bad_values = ["0" * 64, digest(PAYLOAD).upper(), digest(PAYLOAD)[:-1]]
        for index, value in enumerate(bad_values):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.stack.importer.import_capture(
                        [response_packet(raw, "<urn:test:bad>", value)], "digest-bad-" + str(index))
                with self.assertRaises(BatchNotFound):
                    self.stack.catalog.open_batch("digest-bad-" + str(index))
        with self.assertRaises(ValueError):
            self.stack.importer.import_capture(
                [response_packet(raw, "<urn:test:dup>", digest(PAYLOAD),
                                 extra=(("WARC-Payload-Digest", "sha256:" + digest(PAYLOAD)),))], "digest-dup")
        with self.assertRaises(BatchNotFound):
            self.stack.catalog.open_batch("digest-dup")

    def test_revisit_inherits_entity_body(self):
        entity_raw = http(PAYLOAD, 200, (("Content-Encoding", "gzip"), ("X-Capture", "entity")), True, True)
        head = revisit_head(len(PAYLOAD), 203, extra=(("X-Capture", "revisit"),))
        data = revisit_packet("<urn:test:r>", "<urn:test:e>", head, PAYLOAD) + packet(entity_raw, "<urn:test:e>")
        batch = self.stack.importer.import_capture(split_bytes(data), "chain")
        self.assertEqual(batch.record_ids, ("<urn:test:r>", "<urn:test:e>"))
        fresh = Stack(self.stack.root)
        result = fresh.replay.replay("chain", "<urn:test:r>")
        self.assertEqual(result.status, 203)
        self.assertIn(("X-Capture", "revisit"), result.headers)
        self.assertNotIn(("X-Capture", "entity"), result.headers)
        self.assertEqual((result.body, result.text), (TEXT.encode("utf-8"), TEXT))
        self.assertEqual(result.transfer_digest, digest(PAYLOAD))
        self.assertEqual(result.body_digest, digest(TEXT.encode("utf-8")))
        self.assertEqual(result.raw_digest, digest(head))
        self.assertEqual(result.source_chain, ("<urn:test:r>", "<urn:test:e>"))
        verified = fresh.verifier.verify_record("chain", "<urn:test:r>")
        self.assertEqual(verified.source_chain, ("<urn:test:r>", "<urn:test:e>"))
        self.assertTrue(verified.consistent)
        self.assertEqual(verified.record.raw, head)
        self.assertEqual((verified.raw_length, verified.raw_digest), (len(head), digest(head)))
        entity = fresh.replay.replay("chain", "<urn:test:e>")
        self.assertEqual((entity.status, entity.source_chain), (200, ("<urn:test:e>",)))

    def test_multihop_and_forward_chain(self):
        z = packet(http(PAYLOAD, 200, (("Content-Encoding", "gzip"),), True), "<urn:test:z>")
        data = (revisit_packet("<urn:test:x>", "<urn:test:y>", revisit_head(len(PAYLOAD), 204), PAYLOAD)
                + revisit_packet("<urn:test:y>", "<urn:test:z>", revisit_head(len(PAYLOAD), 202), PAYLOAD) + z)
        batch = self.stack.importer.import_capture(split_bytes(data), "multi")
        self.assertEqual(batch.record_ids, ("<urn:test:x>", "<urn:test:y>", "<urn:test:z>"))
        fresh = Stack(self.stack.root)
        expected = {"<urn:test:x>": ("<urn:test:x>", "<urn:test:y>", "<urn:test:z>"),
                    "<urn:test:y>": ("<urn:test:y>", "<urn:test:z>"),
                    "<urn:test:z>": ("<urn:test:z>",)}
        for rid, chain in expected.items():
            result = fresh.replay.replay("multi", rid)
            self.assertEqual(result.source_chain, chain)
            self.assertEqual(result.text, TEXT)
            self.assertEqual(result.transfer_digest, digest(PAYLOAD))
        self.assertEqual(fresh.replay.replay("multi", "<urn:test:x>").status, 204)
        self.assertEqual(fresh.replay.replay("multi", "<urn:test:y>").status, 202)

    def test_revisit_http_boundaries(self):
        entity = self.entity()
        base = len(PAYLOAD)
        te_only = (b"HTTP/1.1 203 Revisit\r\nContent-Type: text/plain; charset=UTF-8\r\n"
                   b"Transfer-Encoding: chunked\r\n\r\n")
        cases = [
            te_only,
            revisit_head(base) + b"extra",
            revisit_head(base, extra=(("Content-Length", str(base + 1)),)),
            revisit_head(base + 1),
            revisit_head(base, status=101),
            revisit_head(base, extra=(("Content-Encoding", "gzip"),)),
        ]
        for index, raw in enumerate(cases):
            with self.subTest(index=index):
                data = revisit_packet("<urn:test:r>", "<urn:test:e>", raw, PAYLOAD) + entity
                with self.assertRaises(ValueError):
                    self.stack.importer.import_capture([data], "http-bad-" + str(index))
                with self.assertRaises(BatchNotFound):
                    self.stack.catalog.open_batch("http-bad-" + str(index))
        raw = revisit_head(base, extra=(("Content-Length", str(base)),))
        data = revisit_packet("<urn:test:r>", "<urn:test:e>", raw, PAYLOAD) + entity
        self.stack.importer.import_capture([data], "http-ok")
        self.assertEqual(self.stack.replay.replay("http-ok", "<urn:test:r>").text, TEXT)

    def test_revisit_warc_rejections(self):
        entity = self.entity()
        head = revisit_head(len(PAYLOAD))
        cases = [
            response_packet(http(b"hello"), "<urn:test:a>", extra=(("WARC-Refers-To", "<urn:test:e>"),)),
            response_packet(http(b"hello"), "<urn:test:a>", extra=(("WARC-Profile", PROFILE),)),
            revisit_packet("<urn:test:r>", "<urn:test:e>", head, PAYLOAD, drop=("WARC-Payload-Digest",)),
            revisit_packet("<urn:test:r>", "<urn:test:e>", head, PAYLOAD, drop=("WARC-Refers-To",)),
            revisit_packet("<urn:test:r>", "<urn:test:e>", head, PAYLOAD, drop=("WARC-Profile",)),
            revisit_packet("<urn:test:r>", "<urn:test:e>", head, PAYLOAD,
                           extra=(("WARC-Refers-To", "<urn:test:e>"),)),
            revisit_packet("<urn:test:r>", "<urn:test:e>", head, PAYLOAD, profile="http://example.test/other"),
            revisit_packet("<urn:test:r>", "<urn:test:r>", head, PAYLOAD),
            revisit_packet("<urn:test:r>", "urn:test:e", head, PAYLOAD),
            revisit_packet("<urn:test:r>", "<urn:test:absent>", head, PAYLOAD),
            revisit_packet("<urn:test:r>", "<urn:test:e>", head, PAYLOAD).replace(
                b"WARC-Type: revisit", b"WARC-Type: Revisit"),
        ]
        for index, data in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.stack.importer.import_capture([data + entity], "warc-bad-" + str(index))
                with self.assertRaises(BatchNotFound):
                    self.stack.catalog.open_batch("warc-bad-" + str(index))

    def test_identity_conflicts(self):
        entity = self.entity()
        head = revisit_head(len(PAYLOAD))
        data = revisit_packet("<urn:test:r>", "<urn:test:e>", head, PAYLOAD) + entity
        first = self.stack.importer.import_capture(split_bytes(data), "fixed")
        second = Stack(self.stack.root).importer.import_capture([data], "fixed")
        self.assertEqual(first, second)
        reordered = entity + revisit_packet("<urn:test:r>", "<urn:test:e>", head, PAYLOAD)
        changed_date = data.replace(b"2026-01-02", b"2026-01-03")
        other = packet(http(PAYLOAD, 200, (("Content-Encoding", "gzip"),), True, True), "<urn:test:other>")
        changed_target = revisit_packet("<urn:test:r>", "<urn:test:other>", head, PAYLOAD) + other
        for index, variant in enumerate((reordered, changed_date, changed_target)):
            with self.subTest(index=index):
                with self.assertRaises(BatchConflict):
                    self.stack.importer.import_capture([variant], "fixed")
                self.assertEqual(Stack(self.stack.root).catalog.open_batch("fixed"), first)

    def test_same_bytes_distinct_identity(self):
        raw = http(PAYLOAD, 200, (("Content-Encoding", "gzip"),), True)
        head = revisit_head(len(PAYLOAD))
        data = (packet(raw, "<urn:test:a>")
                + revisit_packet("<urn:test:r>", "<urn:test:b>", head, PAYLOAD)
                + packet(raw, "<urn:test:b>"))
        batch = self.stack.importer.import_capture([data], "distinct")
        self.assertEqual(batch.record_ids, ("<urn:test:a>", "<urn:test:r>", "<urn:test:b>"))
        result = Stack(self.stack.root).replay.replay("distinct", "<urn:test:r>")
        self.assertEqual(result.source_chain, ("<urn:test:r>", "<urn:test:b>"))
        self.assertEqual(len(list(self.stack.bodies.objects.iterdir())), 2)

    def test_export_not_extended(self):
        head = revisit_head(len(PAYLOAD))
        data = revisit_packet("<urn:test:r>", "<urn:test:e>", head, PAYLOAD) + self.entity()
        self.stack.importer.import_capture([data], "no-export")
        with self.assertRaises(ValueError):
            self.stack.exporter.export_batch("no-export", io.BytesIO())
        self.assertTrue(self.stack.verifier.verify_record("no-export", "<urn:test:r>").consistent)
        v1 = self.stack.packed("v1")
        output = io.BytesIO()
        self.stack.exporter.export_batch("v1", output)
        self.assertEqual(len(output.getvalue()), len(packet(http(), "<urn:test:a>")))
        self.assertEqual(self.stack.replay.replay("v1", v1.record_ids[0]).text, "hello")

    def test_intermediate_evidence_verified(self):
        z = packet(http(PAYLOAD, 200, (("Content-Encoding", "gzip"),), True), "<urn:test:z>")
        data = (revisit_packet("<urn:test:x>", "<urn:test:y>", revisit_head(len(PAYLOAD), 203), PAYLOAD)
                + revisit_packet("<urn:test:y>", "<urn:test:z>", revisit_head(len(PAYLOAD), 202), PAYLOAD) + z)
        self.stack.importer.import_capture([data], "deep")
        stored = self.stack.catalog.get_record("deep", "<urn:test:y>")
        path = self.stack.bodies.object_path(stored.ref)
        original = path.read_bytes()
        try:
            path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            fresh = Stack(self.stack.root)
            for action in (fresh.verifier.verify_record, fresh.replay.replay):
                with self.assertRaises(RecordVerificationError):
                    action("deep", "<urn:test:x>")
            with self.assertRaises(RecordVerificationError):
                fresh.verifier.verify_record("deep", "<urn:test:y>")
            self.assertTrue(fresh.verifier.verify_record("deep", "<urn:test:z>").consistent)
        finally:
            path.write_bytes(original)
        self.assertTrue(Stack(self.stack.root).verifier.verify_record("deep", "<urn:test:x>").consistent)

    def test_unknown_lookups(self):
        self.stack.importer.import_capture([self.entity()], "known")
        for action in (self.stack.replay.replay, self.stack.verifier.verify_record):
            with self.assertRaises(BatchNotFound):
                action("missing", "<urn:test:e>")
            with self.assertRaises(RecordNotFound):
                action("known", "<urn:test:absent>")

    def test_empty_and_entry_separation(self):
        with self.assertRaises(ValueError):
            self.stack.importer.import_capture([], "empty")
        with self.assertRaises(BatchNotFound):
            self.stack.catalog.open_batch("empty")
        raw = http(b"hello")
        with self.assertRaises(ValueError):
            self.stack.importer.import_responses([response_packet(raw, "<urn:test:a>", digest(raw))], "v1-digest")
        head = revisit_head(len(PAYLOAD))
        with self.assertRaises(ValueError):
            self.stack.importer.import_responses(
                [revisit_packet("<urn:test:r>", "<urn:test:e>", head, PAYLOAD) + self.entity()], "v1-revisit")
        for batch in ("v1-digest", "v1-revisit"):
            with self.assertRaises(BatchNotFound):
                self.stack.catalog.open_batch(batch)
        batch = self.stack.importer.import_capture([packet(http(b"plain"), "<urn:test:p>")], "capture-response")
        self.assertEqual(batch.record_ids, ("<urn:test:p>",))
        result = self.stack.replay.replay("capture-response", "<urn:test:p>")
        self.assertEqual((result.text, result.source_chain), ("plain", ("<urn:test:p>",)))

    def test_v1_results_preserved(self):
        old = self.stack.packed("old")
        self.stack.importer.import_capture([self.entity()], "capture")
        fresh = Stack(self.stack.root)
        self.assertEqual(fresh.catalog.open_batch("old"), old)
        result = fresh.replay.replay("old", old.record_ids[0])
        self.assertEqual((result.text, result.source_chain), ("hello", (old.record_ids[0],)))
        self.assertEqual(fresh.importer.import_responses([packet(http(), "<urn:test:a>")], "old"), old)
        head = revisit_head(len(PAYLOAD))
        bad = revisit_packet("<urn:test:x>", "<urn:test:absent>", head, PAYLOAD)
        with self.assertRaises(ValueError):
            self.stack.importer.import_capture([bad + self.entity()], "kept-later")
        with self.assertRaises(BatchNotFound):
            self.stack.catalog.open_batch("kept-later")
        self.assertEqual(Stack(self.stack.root).catalog.open_batch("capture").record_ids, ("<urn:test:e>",))
        self.assertEqual(list(self.stack.bodies.staging.iterdir()), [])
