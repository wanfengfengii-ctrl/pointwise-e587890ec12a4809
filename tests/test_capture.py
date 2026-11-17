from dataclasses import replace
from capture.jobs import CaptureRequest
from capture.schedule import CaptureRunner
from capture.retry import RetryPolicy
from capture.store import JobStore
from archive.catalog import BatchNotFound
from tests.fixtures import BaseTest, DATE, Stack, http, scenario


class TestDailyCapture(BaseTest):
    def test_success(self):
        store, service, runner = self.jobs()
        requests = [CaptureRequest("a", "https://example.test/a"), CaptureRequest("b", "https://example.test/b")]
        service.submit(requests)
        calls = []
        def source(request):
            calls.append(request.job_id)
            return http(("资料" + request.job_id).encode(), 203)
        jobs = runner.run(["a", "b"], source)
        self.assertEqual([j.state for j in jobs], ["succeeded", "succeeded"])
        self.assertEqual(calls, ["a", "b"])
        for job in jobs:
            replay = self.stack.replay.replay(job.batch_id, job.record_id)
            self.assertEqual((replay.status, replay.text), (203, "资料" + job.job_id))
            self.assertEqual(len(self.stack.catalog.open_batch(job.batch_id).records), 1)
        scenario("v1", {"jobs": ["a", "b"]}, ["succeeded", "succeeded"], [j.state for j in jobs],
                 ["资料a", "资料b"], [self.stack.replay.replay(j.batch_id, j.record_id).text for j in jobs],
                 ["capture/jobs.py", "capture/schedule.py", "archive/publication.py", "review/replay.py"],
                 "literal Unicode source values and two distinct fixed single-record batches")

    def test_retry_after_partial_source(self):
        store, service, runner = self.jobs()
        service.submit([CaptureRequest("retry", "https://example.test/retry")])
        calls = []
        def source(request):
            calls.append(request.job_id)
            if len(calls) == 1:
                def interrupted():
                    yield b"HTTP/1.1 200 "
                    raise OSError("source disconnected")
                return interrupted()
            return [http(b"recovered")[:12], b"", http(b"recovered")[12:]]
        first = runner.run(["retry"], source)[0]
        self.assertEqual((first.state, first.attempts), ("retryable", 1))
        with self.assertRaises(BatchNotFound):
            self.stack.catalog.open_batch(first.batch_id)
        second = runner.run(["retry"], source)[0]
        self.assertEqual((second.state, second.attempts), ("succeeded", 2))
        self.assertEqual(self.stack.replay.replay(second.batch_id, second.record_id).text, "recovered")
        self.assertEqual(len(self.stack.catalog.open_batch(second.batch_id).records), 1)

    def test_identity_guard(self):
        store, service, runner = self.jobs()
        request = CaptureRequest("stable", "https://example.test/", "post", (("X-Mode", "A"),), b"one")
        first = service.submit([request])[0]
        self.assertEqual(service.submit([replace(request, method="POST")])[0], first)
        for other in (replace(request, body=b"two"), replace(request, headers=(("X-Mode", "B"),))):
            with self.assertRaises(ValueError):
                service.submit([CaptureRequest("new", "https://example.test/new"), other])
            self.assertEqual(store.get("stable"), first)
            with self.assertRaises(KeyError):
                store.get("new")
        reopened = JobStore(store.path)
        self.assertEqual(reopened.get("stable"), first)
        self.assertEqual(reopened.get("stable").request().body, b"one")

    def test_claim_once_and_restart(self):
        store, service, runner = self.jobs()
        service.submit([CaptureRequest("a", "https://example.test/a")])
        claimed = store.claim("a", 3)
        self.assertEqual(claimed.attempts, 1)
        def forbidden(request):
            self.fail("running or succeeded task called source")
        self.assertEqual(runner.run(["a"], forbidden)[0].state, "running")
        recovered = CaptureRunner(JobStore(store.path), self.stack.publisher)
        self.assertEqual(recovered.reconcile()[0].state, "retryable")
        job = recovered.run(["a"], lambda request: http())[0]
        self.assertEqual((job.state, job.attempts), ("succeeded", 2))
        self.assertEqual(recovered.run(["a"], forbidden)[0], job)
        self.assertEqual(JobStore(store.path).get("a"), job)

    def test_retry_budget_and_invalid_capture(self):
        store, service, _ = self.jobs()
        runner = CaptureRunner(store, self.stack.publisher, RetryPolicy(2))
        service.submit([CaptureRequest("a", "https://example.test/a"), CaptureRequest("bad", "https://example.test/b")])
        def broken(request):
            raise OSError("offline")
        self.assertEqual(runner.run(["a"], broken)[0].state, "retryable")
        failed = runner.run(["a"], broken)[0]
        self.assertEqual((failed.state, failed.attempts), ("failed", 2))
        self.assertEqual(runner.run(["a"], lambda request: self.fail("budget bypass"))[0], failed)
        bad = runner.run(["bad"], lambda request: http(b"\xff"))[0]
        self.assertEqual(bad.state, "failed")
        for job in (failed, bad):
            with self.assertRaises(BatchNotFound):
                self.stack.catalog.open_batch(job.batch_id)

    def test_empty_response(self):
        store, service, runner = self.jobs()
        service.submit([CaptureRequest("empty", "https://example.test/empty")])
        job = runner.run(["empty"], lambda request: http(b""))[0]
        replay = self.stack.replay.replay(job.batch_id, job.record_id)
        self.assertEqual((replay.body, replay.text), (b"", ""))

    def test_publish_before_success(self):
        store, service, runner = self.jobs()
        service.submit([CaptureRequest("before", "https://example.test/before")])
        def before(event):
            if event == "before_commit":
                raise RuntimeError("commit unavailable")
        self.stack.publisher.fault = before
        with self.assertRaises(RuntimeError):
            runner.run(["before"], lambda request: http())
        job = store.get("before")
        self.assertEqual(job.state, "running")
        with self.assertRaises(BatchNotFound):
            self.stack.catalog.open_batch(job.batch_id)
        self.assertIsNone(self.stack.catalog.find_capture_receipt("before"))
        self.stack.publisher.fault = lambda event: None
        self.assertEqual(runner.reconcile()[0].state, "retryable")
        self.assertEqual(runner.run(["before"], lambda request: http())[0].state, "succeeded")
        service.submit([CaptureRequest("after", "https://example.test/after")])
        calls = []
        def stop(event):
            raise KeyboardInterrupt("published, task update not executed")
        interrupted = CaptureRunner(store, self.stack.publisher, fault=stop)
        def source(request):
            calls.append(request.job_id)
            return http(b"once")
        with self.assertRaises(KeyboardInterrupt):
            interrupted.run(["after"], source)
        pending = store.get("after")
        self.assertEqual(pending.state, "running")
        self.assertIsNotNone(self.stack.catalog.find_capture_receipt("after"))
        fresh = Stack(self.stack.root)
        resumed = CaptureRunner(JobStore(store.path), fresh.publisher)
        jobs = resumed.reconcile()
        self.assertEqual([(j.job_id, j.state, j.attempts) for j in jobs], [("after", "succeeded", 1)])
        resumed.run(["after"], lambda request: self.fail("published capture was fetched again"))
        self.assertEqual(calls, ["after"])
        self.assertEqual(len(fresh.catalog.open_batch(pending.batch_id).records), 1)

    def test_interrupted_exhaustion(self):
        store, service, runner = self.jobs()
        service.submit([CaptureRequest("a", "https://example.test/a")])
        store.claim("a", 1)
        resumed = CaptureRunner(JobStore(store.path), self.stack.publisher, RetryPolicy(1))
        self.assertEqual(resumed.reconcile()[0].state, "failed")
