from capture.fetch import fetch_response, SourceReadError
from capture.retry import RetryPolicy
from warc.record import build_response_record
from httpdata.pipeline import interpret_response
from archive.staging import StagedBatch
from review.verify import VerificationService


class CaptureRunner:
    def __init__(self, store, publisher, policy=None, fault=None):
        self.store = store
        self.publisher = publisher
        self.policy = policy or RetryPolicy()
        self.fault = fault or (lambda event: None)

    def _receipt(self, job):
        receipt = self.publisher.catalog.find_capture_receipt(job.job_id)
        if receipt is None:
            return None
        expected = {"job_id": job.job_id, "request_digest": job.request_digest,
                    "batch_id": job.batch_id, "record_id": job.record_id}
        if receipt != expected:
            raise ValueError("published receipt does not match request")
        VerificationService(self.publisher.catalog, self.publisher.bodies).verify_record(job.batch_id, job.record_id)
        return receipt

    def reconcile(self):
        results = []
        for job in self.store.interrupted_jobs():
            receipt = self._receipt(job)
            if receipt is not None:
                results.append(self.store.finish_success(job.job_id, receipt))
            else:
                state = self.policy.after_interrupted_run(job.attempts)
                results.append(self.store.finish_failure(job.job_id, state, "interrupted before publication"))
        return results

    def run(self, job_ids, source):
        results = []
        for job_id in job_ids:
            job = self.store.claim(job_id, self.policy.max_attempts)
            if job is None:
                results.append(self.store.get(job_id))
                continue
            try:
                fetched = fetch_response(source, job.request(), self.publisher.bodies.staging)
            except SourceReadError as exc:
                state = self.policy.after_source_failure(job.attempts)
                results.append(self.store.finish_failure(job_id, state, exc))
                continue
            try:
                interpreted = interpret_response(fetched.raw)
                record = build_response_record(job.record_id, job.request().target, job.captured_at, fetched.raw)
            except ValueError as exc:
                state = self.policy.after_interpretation_failure(job.attempts)
                results.append(self.store.finish_failure(job_id, state, exc))
                continue
            with StagedBatch(self.publisher.bodies, job.batch_id) as staged:
                staged.add_response(record, interpreted)
                receipt = {"job_id": job_id, "request_digest": job.request_digest, "record_id": job.record_id}
                self.publisher.publish(staged, receipt)
            self.fault("after_publish")
            fixed = self._receipt(job)
            if fixed is None:
                raise ValueError("publication did not provide a receipt")
            results.append(self.store.finish_success(job_id, fixed))
        return results
