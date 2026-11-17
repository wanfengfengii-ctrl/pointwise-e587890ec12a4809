import io
import sqlite3
from archive.catalog import BatchNotFound, RecordNotFound
from review.verify import RecordVerificationError
from warc.writer import WarcWriteError
from tests.fixtures import BaseTest, Stack, ShortSink, TEXT, http, packet, digest, scenario


class TestReview(BaseTest):
    def test_export_reimport(self):
        raws = [http(TEXT.encode(), 203), http(b"", 204), http(TEXT.encode(), 203)]
        ids = ["<urn:test:first>", "<urn:test:empty>", "<urn:test:same-bytes>"]
        data = b"".join(packet(raw, rid) for raw, rid in zip(raws, ids))
        original = self.stack.importer.import_responses([data], "source")
        fresh = Stack(self.stack.root)
        output = io.BytesIO()
        result = fresh.exporter.export_batch("source", output)
        self.assertEqual((result.records, result.bytes_written), (3, len(output.getvalue())))
        independent = Stack(self.root / "independent")
        imported = independent.importer.import_responses([output.getvalue()], "copy")
        self.assertEqual(imported.record_ids, tuple(ids))
        self.assertEqual(imported.fingerprint, original.fingerprint)
        for rid, raw in zip(ids, raws):
            before = fresh.replay.replay("source", rid)
            after = independent.replay.replay("copy", rid)
            self.assertEqual((before.status, before.headers, before.body, before.text, before.raw_digest),
                             (after.status, after.headers, after.body, after.text, after.raw_digest))
            self.assertEqual(independent.verifier.verify_record("copy", rid).record.raw, raw)
        self.assertEqual(len(list(independent.bodies.objects.iterdir())), 2)
        scenario("v7.roundtrip_bytes", {"ids": ids, "raw_digests": [digest(raw) for raw in raws]},
                 ids, list(imported.record_ids), [TEXT, "", TEXT],
                 [independent.replay.replay("copy", rid).text for rid in ids],
                 ["review/export.py", "review/verify.py", "warc/writer.py", "warc/importer.py", "archive/catalog.py"],
                 "literal original HTTP bytes, record order and text; independent archive reimport")

    def test_detect_corrupt_source(self):
        for mode in ("change", "truncate", "remove"):
            with self.subTest(mode=mode):
                stack = Stack(self.root / mode)
                batch = stack.packed()
                ref = batch.records[0].ref
                path = stack.bodies.object_path(ref)
                self.assertTrue(stack.verifier.verify_record("base", batch.record_ids[0]).consistent)
                raw = path.read_bytes()
                if mode == "change":
                    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
                elif mode == "truncate":
                    path.write_bytes(raw[:-1])
                else:
                    path.unlink()
                fresh = Stack(stack.root)
                for action in (fresh.verifier.verify_record, fresh.replay.replay):
                    with self.assertRaises(RecordVerificationError):
                        action("base", batch.record_ids[0])
                with self.assertRaises(RecordVerificationError):
                    fresh.exporter.export_batch("base", io.BytesIO())
                self.assertEqual(fresh.catalog.get_record("base", batch.record_ids[0]).text_bytes, b"hello")

    def test_fixed_snapshot(self):
        original = self.stack.packed("A", http(b"first"))
        self.stack.packed("B", http(b"second"))
        self.assertEqual(original.record_ids, ("<urn:test:a>",))
        self.assertEqual(original, self.stack.catalog.open_batch("A"))
        self.assertEqual(self.stack.replay.replay("A", "<urn:test:a>").text, "first")
        self.assertEqual(self.stack.replay.replay("B", "<urn:test:a>").text, "second")
        with self.assertRaises(BatchNotFound):
            self.stack.replay.replay("missing", "<urn:test:a>")
        with self.assertRaises(RecordNotFound):
            self.stack.replay.replay("A", "<urn:test:unknown>")

    def test_sink_failure(self):
        original = self.stack.packed()
        ordinary = io.BytesIO()
        self.stack.exporter.export_batch("base", ordinary)
        for limit in (1, 3, 7):
            sink = ShortSink(limit)
            result = self.stack.exporter.export_batch("base", sink)
            self.assertEqual(bytes(sink.output), ordinary.getvalue())
            self.assertEqual(result.bytes_written, len(ordinary.getvalue()))
            independent = Stack(self.root / ("sink-" + str(limit)))
            imported = independent.importer.import_responses([bytes(sink.output)], "copy")
            self.assertEqual(independent.replay.replay("copy", imported.record_ids[0]).text, "hello")
        for explode in (False, True):
            sink = ShortSink(stop=31, explode=explode)
            with self.assertRaises(OSError):
                self.stack.exporter.export_batch("base", sink)
            self.assertEqual(len(sink.output), 31)
            self.assertEqual(self.stack.catalog.open_batch("base"), original)
            self.assertTrue(self.stack.verifier.verify_record("base", original.record_ids[0]).consistent)

    def test_stored_text_consistency(self):
        batch = self.stack.packed()
        with sqlite3.connect(self.stack.db.path) as connection:
            connection.execute("UPDATE records SET text_bytes=? WHERE batch_id='base'", (b"other",))
        with self.assertRaises(RecordVerificationError):
            self.stack.verifier.verify_record("base", batch.record_ids[0])
        with self.assertRaises(RecordVerificationError):
            self.stack.replay.replay("base", batch.record_ids[0])
