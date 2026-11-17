import gzip
from httpdata.pipeline import interpret_response
from archive.catalog import BatchNotFound
from warc.headers import WarcHeaders
from tests.fixtures import BaseTest, TEXT, DATE, http, packet, split_bytes, digest, scenario


class TestImport(BaseTest):
    def test_chunked_gzip_split(self):
        plain = TEXT.encode("utf-8")
        compressed = gzip.compress(plain, mtime=0)
        raw = http(compressed, 203, (("Content-Encoding", "gzip"), ("Date", "Fri, 02 Jan 2026 03:04:05 GMT")), True, True)
        batch = self.stack.importer.import_responses(split_bytes(packet(raw)), "gzip")
        interpreted = interpret_response(raw)
        self.assertEqual(interpreted.transfer_body, compressed)
        self.assertEqual(interpreted.body, plain)
        self.assertEqual(interpreted.text, TEXT)
        self.assertEqual(interpreted.evidence()["transfer_digest"], digest(compressed))
        result = self.stack.replay.replay("gzip", batch.record_ids[0])
        self.assertEqual((result.status, result.text, result.body_digest), (203, TEXT, digest(plain)))
        checked = self.stack.verifier.verify_record("gzip", batch.record_ids[0])
        self.assertEqual((checked.raw_length, checked.raw_digest), (len(raw), digest(raw)))
        self.assertEqual(checked.record.raw, raw)
        scenario("v3.layer_order", {"body_hex": plain.hex(), "gzip_bytes": len(compressed), "status": 203},
                 {"text": TEXT, "transfer_digest": digest(compressed)}, {"text": interpreted.text, "transfer_digest": interpreted.evidence()["transfer_digest"]},
                 {"status": 203, "text": TEXT}, {"status": result.status, "text": result.text},
                 ["warc/stream.py", "warc/importer.py", "httpdata/transfer.py", "httpdata/content.py", "archive/publication.py", "review/verify.py"],
                 "literal UTF-8 text; independent stdlib gzip mtime=0; hand-built chunk lengths and trailer")

    def test_truncated_batch(self):
        raw = http("旧资料".encode())
        old = self.stack.packed("old", raw)
        original = self.stack.verifier.verify_record("old", old.record_ids[0]).record.raw
        broken = packet(raw) + packet(http(b"late"), "<urn:test:last>")[:-7]
        with self.assertRaises(ValueError):
            self.stack.importer.import_responses([broken], "new")
        with self.assertRaises(BatchNotFound):
            self.stack.catalog.open_batch("new")
        self.assertEqual(self.stack.verifier.verify_record("old", old.record_ids[0]).record.raw, original)
        self.assertEqual(list(self.stack.bodies.staging.iterdir()), [])

    def test_invalid_encodings(self):
        compressed = gzip.compress(b"valid", mtime=0)
        corrupt = compressed[:-8] + bytes([compressed[-8] ^ 1]) + compressed[-7:]
        invalid = [
            http(b"3\r\nab\r\n0\r\n\r\n").replace(b"Content-Length: 12", b"Transfer-Encoding: chunked"),
            http(compressed[:-3], extra=(("Content-Encoding", "gzip"),)),
            http(corrupt, extra=(("Content-Encoding", "gzip"),)),
            http(b"", extra=(("Content-Encoding", "gzip"),)),
            http(b"\xc3"),
            http(b"valid", extra=(("Content-Encoding", "br"),)),
            http(b"valid").replace(b"charset=UTF-8", b"charset=latin-1"),
        ]
        old = self.stack.packed("preserved")
        for index, raw in enumerate(invalid):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.stack.importer.import_responses([packet(http(), "<urn:test:prefix>"), packet(raw)], "bad-" + str(index))
                with self.assertRaises(BatchNotFound):
                    self.stack.catalog.open_batch("bad-" + str(index))
                self.assertTrue(self.stack.verifier.verify_record("preserved", old.record_ids[0]).consistent)
        good = self.stack.importer.import_responses([packet(http(compressed, extra=(("Content-Encoding", "gzip"),)))], "valid")
        self.assertEqual(self.stack.replay.replay("valid", good.record_ids[0]).text, "valid")

    def test_boundary_matrix(self):
        raws = [http("中文\n".encode()), http(b"", 204)]
        data = packet(raws[0], "<urn:test:first>") + packet(raws[1], "<urn:test:empty>")
        variants = ([data], split_bytes(data), [data[:19], b"", data[19:83], data[83:]])
        fingerprints = []
        for index, chunks in enumerate(variants):
            batch = self.stack.importer.import_responses(chunks, "split-" + str(index))
            self.assertEqual(batch.record_ids, ("<urn:test:first>", "<urn:test:empty>"))
            for rid, raw in zip(batch.record_ids, raws):
                self.assertEqual(self.stack.verifier.verify_record(batch.batch_id, rid).record.raw, raw)
            fingerprints.append(batch.fingerprint)
        self.assertEqual(len(set(fingerprints)), 1)

    def test_response_identity(self):
        raw = http()
        fields = list(WarcHeaders((
            ("WARC-Type", "response"), ("WARC-Record-ID", "<urn:test:a>"),
            ("WARC-Target-URI", "https://example.test/a"), ("WARC-Date", DATE),
            ("Content-Type", "application/http; msgtype=response"), ("Content-Length", str(len(raw))))).fields)
        cases = [b"", packet(raw) + packet(raw), packet(raw, fields=fields[1:]),
                 packet(raw, fields=fields + [fields[1]]),
                 packet(raw).replace(b"WARC-Type: response", b"WARC-Type: revisit"),
                 packet(raw, fields=fields + [("WARC-Refers-To", "<urn:test:target>")]),
                 packet(raw).replace(str(len(raw)).encode() + b"\r\n\r\nHTTP", b"-1\r\n\r\nHTTP"),
                 packet(raw) + b"x", packet(raw)[:-1],
                 packet(raw).replace(b"WARC/1.1", b"WARC/1.0")]
        for index, data in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.stack.importer.import_responses([data], "bad-" + str(index))
                with self.assertRaises(BatchNotFound):
                    self.stack.catalog.open_batch("bad-" + str(index))
        self.stack.packed("good")

    def test_framing(self):
        valid = [http(b""), http(b"abc"), http(b"abc", chunked=True, trailer=True),
                 http(b"abc").replace(b"Content-Length: 3", b"Content-Length: 3\r\nContent-Length: 3")]
        for raw in valid:
            self.assertEqual(interpret_response(raw).body, b"" if raw == valid[0] else b"abc")
        invalid = [
            b"HTTP/1.1 200 OK\r\n\r\n",
            http(b"abc").replace(b"Content-Length: 3", b"Content-Length: 2"),
            http(b"abc").replace(b"Content-Length: 3", b"Content-Length: 3\r\nContent-Length: 4"),
            http(b"abc").replace(b"Content-Length: 3", b"Content-Length: 3\r\nTransfer-Encoding: chunked"),
            http(b"abc").replace(b"Content-Length: 3", b"Transfer-Encoding: compress"),
            http(b"abc", chunked=True)[:-1],
            http(b"abc", chunked=True).replace(b"3\r\nabc", b"Z\r\nabc"),
            http(b"abc", chunked=True).replace(b"abc\r\n", b"abcXX"),
            http(b"abc", chunked=True) + b"x",
            http(b"abc").replace(b"Content-Type:", b" Content-Type:"),
        ]
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    self.stack.importer.import_responses([packet(raw)], "framing-bad")
        with self.assertRaises(BatchNotFound):
            self.stack.catalog.open_batch("framing-bad")
