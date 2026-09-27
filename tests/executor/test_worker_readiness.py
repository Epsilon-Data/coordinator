"""
Tests for the executor's enclave-readiness handling.

Covers the boot race where the executor claimed a job (status 'executing')
before the enclave answered, then dropped it, leaving it stuck forever.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.base_worker import BaseWorker
from workers.executor.worker import ExecutorWorker


def make_worker(ready=True, max_attempts=3):
    """Build an ExecutorWorker without settings validation or a real enclave."""
    worker = ExecutorWorker.__new__(ExecutorWorker)
    BaseWorker.__init__(worker, "ExecutorWorker")
    worker._settings = SimpleNamespace(
        execution=SimpleNamespace(script_timeout_seconds=300),
        storage=SimpleNamespace(shared_storage_path="/shared/epsilon"),
    )
    worker._executor = MagicMock()
    type(worker._executor).is_ready = property(lambda self: worker._ready)
    worker._ready = ready
    worker._atl_client = None
    worker._init_recovery_settings()
    worker._max_execution_attempts = max_attempts
    return worker


def make_job(execution_metrics=None):
    return SimpleNamespace(
        job_id="JOB-TEST",
        status="executing",
        execution_metrics=execution_metrics,
        error_message=None,
        completed_at=None,
        updated_at=None,
    )


@pytest.fixture
def base_repo():
    with patch("shared.base_worker.job_repository") as repo:
        repo.fetch_cloned_jobs_for_execution.return_value = [{"job_id": "JOB-TEST"}]
        yield repo


@pytest.fixture(autouse=True)
def no_sweep_or_stamp():
    with patch.object(ExecutorWorker, "_recover_stale_jobs") as sweep, \
         patch.object(ExecutorWorker, "_stamp_boot_ready") as stamp:
        yield SimpleNamespace(sweep=sweep, stamp=stamp)


class TestFetchJobs:
    def test_does_not_claim_while_enclave_not_ready(self, base_repo):
        worker = make_worker(ready=False)

        assert worker.fetch_jobs() == []
        base_repo.fetch_cloned_jobs_for_execution.assert_not_called()

    def test_claims_once_enclave_ready(self, base_repo):
        worker = make_worker(ready=True)

        assert worker.fetch_jobs() == [{"job_id": "JOB-TEST"}]
        base_repo.fetch_cloned_jobs_for_execution.assert_called_once()

    def test_boot_race_job_claimed_only_after_enclave_answers(self, base_repo):
        worker = make_worker(ready=False)
        assert worker.fetch_jobs() == []

        worker._ready = True
        assert worker.fetch_jobs() == [{"job_id": "JOB-TEST"}]
        base_repo.fetch_cloned_jobs_for_execution.assert_called_once()

    def test_boot_ready_stamped_on_first_ready_only(self, base_repo, no_sweep_or_stamp):
        worker = make_worker(ready=False)
        worker.fetch_jobs()
        no_sweep_or_stamp.stamp.assert_not_called()

        worker._ready = True
        worker.fetch_jobs()
        worker.fetch_jobs()
        no_sweep_or_stamp.stamp.assert_called_once()


class TestHealthCheck:
    def test_health_check_does_not_claim_jobs(self, base_repo):
        worker = make_worker()
        with patch("workers.executor.worker.db") as db:
            worker._health_check()

        base_repo.fetch_cloned_jobs_for_execution.assert_not_called()
        db.get_session.assert_called_once()

    def test_health_check_raises_on_db_error(self, base_repo):
        worker = make_worker()
        with patch("workers.executor.worker.db") as db:
            db.get_session.side_effect = RuntimeError("db down")
            with pytest.raises(RuntimeError):
                worker._health_check()


class TestProcessJob:
    def test_releases_claimed_job_when_enclave_drops(self, tmp_path):
        worker = make_worker(ready=False)
        worker._settings.storage.shared_storage_path = str(tmp_path)
        (tmp_path / "repositories" / "JOB-TEST").mkdir(parents=True)

        with patch("workers.executor.worker.job_repository") as repo:
            assert worker.process_job({"job_id": "JOB-TEST"}) is False

        repo.update_job_status.assert_called_once_with(job_id="JOB-TEST", status="cloned")
        worker._executor.execute.assert_not_called()


class TestRequeueOrFail:
    def test_requeues_and_counts_attempt(self):
        worker = make_worker(max_attempts=3)
        job = make_job()

        assert worker._requeue_or_fail(job, "stuck") == "cloned"
        assert json.loads(job.execution_metrics)["execution_attempts"] == 1
        assert job.error_message is None

    def test_fails_after_max_attempts(self):
        worker = make_worker(max_attempts=3)
        job = make_job(json.dumps({"execution_attempts": 2, "ai_decision": "ai_rejected"}))

        assert worker._requeue_or_fail(job, "stuck") == "failed"
        assert "gave up after 3 attempts" in job.error_message
        assert job.completed_at is not None
        metrics = json.loads(job.execution_metrics)
        assert metrics["execution_attempts"] == 3
        assert metrics["ai_decision"] == "ai_rejected"

    def test_tolerates_invalid_metrics_json(self):
        worker = make_worker()
        job = make_job("not json")

        assert worker._requeue_or_fail(job, "stuck") == "cloned"
        assert json.loads(job.execution_metrics) == {"execution_attempts": 1}


class TestStaleSweep:
    def test_sweep_rate_limited(self, no_sweep_or_stamp):
        worker = make_worker()
        worker._stale_sweep_interval = 300

        with patch("workers.executor.worker.time.monotonic", side_effect=[1000.0, 1100.0, 1400.0]):
            worker._maybe_recover_stale_jobs()
            worker._maybe_recover_stale_jobs()
            worker._maybe_recover_stale_jobs()

        assert no_sweep_or_stamp.sweep.call_count == 2

    def test_sweep_runs_even_when_enclave_not_ready(self, base_repo, no_sweep_or_stamp):
        worker = make_worker(ready=False)
        worker.fetch_jobs()
        no_sweep_or_stamp.sweep.assert_called_once()
