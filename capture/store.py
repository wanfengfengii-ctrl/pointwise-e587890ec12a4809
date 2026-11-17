import sqlite3
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass


@dataclass(frozen=True)
class CaptureJob:
    job_id: str
    request_digest: str
    request_json: str
    state: str
    attempts: int
    record_id: str
    batch_id: str
    captured_at: str
    error: str

    def request(self):
        from capture.jobs import CaptureRequest
        value = json.loads(self.request_json)
        return CaptureRequest(self.job_id, value["target"], value["method"],
                              tuple(tuple(pair) for pair in value["headers"]), bytes.fromhex(value["body_hex"]))


class JobStore:
    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.clock = clock or (lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("incompatible task database version")
            if version == 0 and connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
                raise ValueError("unversioned nonempty task database")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, request_json TEXT NOT NULL,
                state TEXT NOT NULL, attempts INTEGER NOT NULL, record_id TEXT NOT NULL UNIQUE,
                batch_id TEXT NOT NULL UNIQUE, captured_at TEXT NOT NULL, error TEXT NOT NULL)""")
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _job(row):
        return CaptureJob(**dict(row))

    def get(self, job_id):
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            return self._job(row)
        finally:
            connection.close()

    def accept(self, prepared):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for job_id, digest, document in prepared:
                old = connection.execute("SELECT request_digest,request_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
                if old is not None:
                    if tuple(old) != (digest, document):
                        raise ValueError("job ID has a different request")
                    continue
                stable = uuid.uuid5(uuid.NAMESPACE_URL, job_id + ":" + digest)
                connection.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)", (
                    job_id, digest, document, "pending", 0, "<urn:uuid:" + str(stable) + ">",
                    "capture-" + str(stable), self.clock(), ""))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return [self.get(job_id) for job_id, _, _ in prepared]

    def claim(self, job_id, max_attempts):
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("positive attempt budget required")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] not in ("pending", "retryable"):
                connection.commit()
                return None
            if row["attempts"] >= max_attempts:
                connection.execute("UPDATE jobs SET state='failed',error='attempt budget exhausted' WHERE job_id=?", (job_id,))
                connection.commit()
                return None
            connection.execute("UPDATE jobs SET state='running',attempts=attempts+1,error='' WHERE job_id=?", (job_id,))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get(job_id)

    def finish_success(self, job_id, receipt):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            expected = {"job_id": job_id, "request_digest": row["request_digest"],
                        "batch_id": row["batch_id"], "record_id": row["record_id"]}
            if receipt != expected or row["state"] not in ("running", "succeeded"):
                raise ValueError("success requires matching published receipt")
            connection.execute("UPDATE jobs SET state='succeeded',error='' WHERE job_id=?", (job_id,))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get(job_id)

    def finish_failure(self, job_id, state, error):
        if state not in ("retryable", "failed"):
            raise ValueError("invalid failure state")
        connection = self._connect()
        try:
            with connection:
                result = connection.execute("UPDATE jobs SET state=?,error=? WHERE job_id=? AND state='running'", (state, str(error), job_id))
                if result.rowcount != 1:
                    raise ValueError("task is not running")
        finally:
            connection.close()
        return self.get(job_id)

    def interrupted_jobs(self):
        connection = self._connect()
        try:
            return tuple(self._job(row) for row in connection.execute("SELECT * FROM jobs WHERE state='running' ORDER BY job_id"))
        finally:
            connection.close()
