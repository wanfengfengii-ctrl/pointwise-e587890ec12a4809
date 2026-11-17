import argparse
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
from capture.jobs import CaptureRequest, JobService
from capture.store import JobStore
from capture.schedule import CaptureRunner
from warc.importer import ResponseImporter
from review.verify import VerificationService
from review.replay import ReplayService
from review.export import ExportService


def services(root):
    database = ArchiveDatabase(root)
    bodies = BodyStore(root)
    catalog = Catalog(database)
    publisher = Publisher(database, bodies)
    verifier = VerificationService(catalog, bodies)
    return publisher, ResponseImporter(publisher), ReplayService(verifier), ExportService(catalog, verifier)


def demo():
    samples = Path(__file__).resolve().parent / "samples"
    daily = json.loads((samples / "daily.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="research-demo-") as directory:
        root = Path(directory)
        publisher, importer, replay, exporter = services(root / "archive")
        store = JobStore(root / "tasks.sqlite3", clock=lambda: daily["captured_at"])
        service = JobService(store)
        runner = CaptureRunner(store, publisher)
        requests = [CaptureRequest(row["job_id"], row["target"]) for row in daily["requests"]]
        service.submit(requests)
        inputs = {row["job_id"]: row for row in daily["requests"]}
        def source(request):
            row = inputs[request.job_id]
            body = row["text"].encode("utf-8")
            return ("HTTP/1.1 " + str(row["status"]) + " Captured\r\nContent-Type: text/plain; charset=UTF-8\r\nContent-Length: " + str(len(body)) + "\r\n\r\n").encode("ascii") + body
        jobs = runner.run([request.job_id for request in requests], source)
        if any(job.state != "succeeded" for job in jobs):
            raise AssertionError("demo capture failed")
        importer.import_responses([(samples / "partner.warc").read_bytes()], "partner")
        publisher, importer, replay, exporter = services(root / "archive")
        independent_publisher, independent_importer, independent_replay, _ = services(root / "independent")
        actual = []
        transferred = []
        for job in jobs:
            result = replay.replay(job.batch_id, job.record_id)
            expected = inputs[job.job_id]
            if (result.status, result.text) != (expected["status"], expected["text"]):
                raise AssertionError("demo replay differs from source")
            output = io.BytesIO()
            exporter.export_batch(job.batch_id, output)
            copied = independent_importer.import_responses([output.getvalue()], job.batch_id)
            check = independent_replay.replay(copied.batch_id, job.record_id)
            if (check.status, check.text, check.raw_digest) != (result.status, result.text, result.raw_digest):
                raise AssertionError("independent reimport changed evidence")
            actual.append({"status": result.status, "text": result.text, "raw_digest": result.raw_digest})
            transferred.append({"status": check.status, "text": check.text, "raw_digest": check.raw_digest})
        partner = replay.replay("partner", "<urn:sample:partner>")
        if partner.text != "hello\n":
            raise AssertionError("partner sample changed")
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v1", "input": daily,
            "expected": [{"status": row["status"], "text": row["text"]} for row in daily["requests"]],
            "actual": actual, "downstream_expected": actual, "downstream_actual": transferred,
            "code_paths": ["capture/schedule.py", "archive/publication.py", "review/verify.py", "review/export.py", "warc/importer.py"],
            "oracle": "literal sample text and status; exact raw-response digest comparison after independent reimport"
        }, ensure_ascii=True, sort_keys=True))
        print(json.dumps({"partner_text": partner.text, "partner_raw_digest": partner.raw_digest,
                          "captured_records": len(jobs), "independent_records": len(transferred)}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.demo:
        demo()
        return 0
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent / "tests"), top_level_dir=str(Path(__file__).parent))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
