import sqlite3
from archive.publication import BatchConflict
from archive.database import ArchiveDatabase, ArchiveVersionError
from archive.bodies import ObjectIntegrityError
from archive.catalog import BatchNotFound
from capture.store import JobStore
from capture.jobs import JobService, CaptureRequest
from tests.fixtures import BaseTest, Stack, http, packet, split_bytes


class TestPublication(BaseTest):
    def test_reimport_identity(self):
        data = packet(http(b"original"))
        first = self.stack.importer.import_responses([data], "stable")
        second = self.stack.importer.import_responses(split_bytes(data), "stable")
        self.assertEqual(first, second)
        with self.assertRaises(BatchConflict):
            self.stack.importer.import_responses([packet(http(b"changed"))], "stable")
        self.assertEqual(self.stack.catalog.open_batch("stable"), first)
        self.assertEqual(self.stack.replay.replay("stable", first.record_ids[0]).text, "original")

    def test_content_identity(self):
        raw = http(b"same")
        a = packet(raw, "<urn:test:a>")
        b = packet(raw, "<urn:test:b>")
        first = self.stack.importer.import_responses([a + b], "identity")
        self.assertEqual(len(first.records), 2)
        self.assertEqual(len(list(self.stack.bodies.objects.iterdir())), 1)
        variants = [
            b + a,
            packet(raw, "<urn:test:c>") + b,
            packet(raw.replace(b"200", b"203", 1), "<urn:test:a>") + b,
            a.replace(b"2026-01-02", b"2026-01-03", 1) + b,
            a.replace(b"Content-Type: application/http;", b"WARC-IP-Address: 192.0.2.5\r\nContent-Type: application/http;", 1) + b,
        ]
        for data in variants:
            with self.assertRaises(BatchConflict):
                self.stack.importer.import_responses([data], "identity")
            self.assertEqual(self.stack.catalog.open_batch("identity"), first)
        self.assertEqual(self.stack.importer.import_responses(split_bytes(a + b), "identity"), first)

    def test_rollback_all(self):
        old = self.stack.packed("old")
        shared = old.records[0].ref
        data = packet(http(), "<urn:test:shared>") + packet(http(b"second"), "<urn:test:second>")
        def fail(event):
            if event == "before_commit":
                raise RuntimeError("injected catalog failure")
        self.stack.publisher.fault = fail
        with self.assertRaises(RuntimeError):
            self.stack.importer.import_responses([data], "failed")
        reopened = Stack(self.stack.root)
        with self.assertRaises(BatchNotFound):
            reopened.catalog.open_batch("failed")
        self.assertEqual(reopened.catalog.open_batch("old"), old)
        self.assertEqual(reopened.bodies.read_verified(shared), http())
        self.assertTrue(reopened.verifier.verify_record("old", old.record_ids[0]).consistent)
        self.assertEqual(list(reopened.bodies.staging.iterdir()), [])

    def test_initialization(self):
        batch = self.stack.packed()
        store, service, _ = self.jobs()
        original = service.submit([CaptureRequest("user-added", "https://example.test/user")])[0]
        for _ in range(2):
            self.stack.db.initialize()
            store.initialize()
        self.assertEqual(Stack(self.stack.root).catalog.open_batch("base"), batch)
        self.assertEqual(JobStore(store.path).get("user-added"), original)
        with sqlite3.connect(self.stack.db.path) as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaises(ArchiveVersionError):
            ArchiveDatabase(self.stack.root)
        with sqlite3.connect(self.stack.db.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM batches").fetchone()[0], 1)
        with sqlite3.connect(store.path) as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaises(ValueError):
            JobStore(store.path)
        with sqlite3.connect(store.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)

    def test_existing_object_checked(self):
        batch = self.stack.packed()
        path = self.stack.bodies.object_path(batch.records[0].ref)
        path.write_bytes(b"damaged")
        with self.assertRaises(ObjectIntegrityError):
            self.stack.packed("new")
        with self.assertRaises(BatchNotFound):
            self.stack.catalog.open_batch("new")
        self.assertEqual(self.stack.catalog.open_batch("base"), batch)
