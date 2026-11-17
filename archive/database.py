import sqlite3
from pathlib import Path
from contextlib import contextmanager


class ArchiveVersionError(ValueError):
    pass


class ArchiveDatabase:
    VERSION = 1

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "catalog.sqlite3"
        self.initialize()

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, self.VERSION):
                raise ArchiveVersionError("incompatible archive version")
            if version == 0 and connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
                raise ArchiveVersionError("unversioned nonempty archive")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS batches (batch_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)")
            connection.execute("""CREATE TABLE IF NOT EXISTS records (
                batch_id TEXT NOT NULL REFERENCES batches(batch_id), ordinal INTEGER NOT NULL,
                record_id TEXT NOT NULL, warc_json TEXT NOT NULL,
                raw_digest TEXT NOT NULL, raw_size INTEGER NOT NULL,
                evidence_json TEXT NOT NULL, text_bytes BLOB NOT NULL,
                PRIMARY KEY(batch_id, record_id), UNIQUE(batch_id, ordinal))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS receipts (
                job_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
                batch_id TEXT NOT NULL REFERENCES batches(batch_id), record_id TEXT NOT NULL)""")
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def open_readonly(self):
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute("PRAGMA user_version").fetchone()[0] != self.VERSION:
                raise ArchiveVersionError("incompatible archive version")
            yield connection
        finally:
            connection.close()
