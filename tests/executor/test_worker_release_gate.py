"""Output-release gate in ExecutorWorker.process_job (unit, no database).

Real-data output and error text from a run is held for review via
complete_job_with_held_output, never written to execution_output, never put
into the public ATL log, and never logged (coordinator logs or job_logs).
A failure before real data was requested is platform text and is shown as is
(release_status not_required).
"""
import hashlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from shared.base_worker import BaseWorker
from shared.db import HELD_ERROR_MESSAGE
from workers.executor.clients import EnclaveClient, EnclaveClientTDX, MiddlewareClient, ProxyClient
from workers.executor.executor import SecureExecutor
from workers.executor.interfaces import IMiddlewareClient, MiddlewareRequest, MiddlewareResponse
from workers.executor.models import ExecutionResult, JobStatus
from workers.executor.tdx_verifier import TdxVerificationResult
from workers.executor.worker import ExecutorWorker, UNSTORED_OUTPUT_MESSAGE, _attested_output_hash

JOB_ID = 'JOB-RG-1'
PROJECT_ID = 'eba92dec-17a1-4a47-816e-77087ceb9531'
SUBMITTER_SUB = '5d0b0a8e-3c1f-4b7a-9e2d-6f4a1c8b2e90'
MARKER = 'MARKER-7f3a9c2d-real-data-row-count-4217'
AUTHENTICITY_CHECKS = ['syntax_valid', 'certificate_chain_valid', 'signature_valid', 'pcr_verified']


class WriteError(Exception):
    """A failing database write; like a SQLAlchemy error, its text may embed the held content."""


class HoldError(Exception):
    """A failing write of the held error text."""


class FakeMiddlewareClient(IMiddlewareClient):
    """The data middleware boundary: returns the scripted response, or raises."""

    def __init__(self):
        self.response = MiddlewareResponse(success=True, encrypted_csv='ZW5jcnlwdGVkLWNzdg==', mode='legacy')
        self.raises = None
        self.requests = []

    def fetch_encrypted_csv(self, request, mode='legacy'):
        self.requests.append(request)
        if self.raises:
            raise self.raises
        return self.response

    def health_check(self):
        return True

    @property
    def is_enabled(self):
        return True


def _sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _job(**overrides):
    job = {
        'job_id': JOB_ID,
        'workspace_id': 'ws-001',
        'user_id': 'user-001',
        'researcher_nonce': None,
        'commit_sha': 'abc123',
        'github_repo': 'alice/analysis',
        'github_branch': 'main',
        'commit_message': 'run',
        'commit_author': 'alice',
        'created_at': '2026-10-14T00:00:00',
    }
    job.update(overrides)
    return job


def _result(status, **fields):
    return ExecutionResult(job_id=JOB_ID, status=status, execution_time=1.5, **fields)


def _receipt(output_hash, **checks):
    """A Nitro verification receipt; every check passes unless overridden."""
    all_checks = dict.fromkeys(AUTHENTICITY_CHECKS + ['output_verified'], True)
    all_checks.update(checks)
    return json.dumps({
        'valid': all(all_checks.values()), 'checks': all_checks, 'pcrs': {'pcr0': 'aa'}, 'output_hash': output_hash,
    })


def _make_worker(executor, storage_path, atl_client=None):
    # Bypass __init__: it validates the environment and touches the database.
    worker = ExecutorWorker.__new__(ExecutorWorker)
    worker._settings = MagicMock()
    worker._settings.storage.shared_storage_path = str(storage_path)
    worker._executor = executor
    worker._atl_client = atl_client
    return worker


def _with_poll_loop(worker):
    # The BaseWorker state _poll_cycle uses, without installing signal handlers.
    with patch('shared.base_worker.signal'):
        BaseWorker.__init__(worker, 'ExecutorWorker')
    return worker


def _assert_never_logged(caplog, marker=MARKER):
    formatter = logging.Formatter('%(name)s %(levelname)s %(message)s')
    for record in caplog.records:
        assert marker not in formatter.format(record), f"logged by {record.name}: {record.getMessage()}"


def _all_calls(mock):
    return repr(mock.mock_calls)


@pytest.fixture
def storage(tmp_path):
    (tmp_path / 'repositories' / JOB_ID).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def repo():
    with patch('workers.executor.worker.job_repository') as mock:
        yield mock


@pytest.fixture
def executor():
    ex = MagicMock()
    ex.is_ready = True
    return ex


@pytest.fixture
def atl():
    client = MagicMock()
    client.submit_la_entry.return_value = (True, {'leaf_index': 3})
    return client


@pytest.fixture
def worker(executor, storage, atl):
    return _make_worker(executor, storage, atl_client=atl)


@pytest.fixture(autouse=True)
def capture_all_logs(caplog):
    caplog.set_level(logging.DEBUG)


@pytest.fixture
def job_logs():
    with patch('shared.job_logger.job_repository') as mock:
        mock.add_detailed_log.return_value = 'log-id'
        yield mock


@pytest.fixture
def enclave():
    client = MagicMock()
    client.get_public_key.return_value = ('pubkey-pem', 'session_123')
    client.encrypt_zip_data.return_value = 'encrypted_zip_base64'
    client.health_check.return_value = True
    client.enclave_cid = 1
    return client


@pytest.fixture
def middleware():
    return FakeMiddlewareClient()


@pytest.fixture
def proxy():
    return MagicMock()


@pytest.fixture
def secure_worker(storage, enclave, middleware, proxy, atl, job_logs, repo):
    """Real SecureExecutor + JobLogger; only the enclave, the data clients, the access check and DB writes are faked."""
    build = storage / 'repositories' / JOB_ID / 'build'
    (build / 'generated').mkdir(parents=True)
    (build / 'build.yml').write_text(
        'analysis:\n'
        '  script_file: main.py\n'
        'datasets:\n'
        f'  - dataset_id: {PROJECT_ID}\n'
        '    archetype_id: arch-001\n'
    )
    (build / 'main.py').write_text('print("hello")')

    # Both modules use the one shared job_repository, as in production
    repo.get_submitter_sub.return_value = SUBMITTER_SUB
    access_client = MagicMock()
    access_client.check_project_access.return_value = True
    settings = MagicMock()
    settings.middleware.use_direct_db = False
    with patch('workers.executor.executor.ZipService') as zip_service:
        zip_service.return_value.zip_directory.return_value = MagicMock(
            data=b'zip', files_count=1, compressed_size=3,
        )
        secure_executor = SecureExecutor(
            enclave_client=enclave, settings=settings, middleware_client=middleware, proxy_client=proxy,
            access_client=access_client,
        )
    with patch('workers.executor.executor.job_repository', repo):
        yield _make_worker(secure_executor, storage, atl_client=atl)


class TestSuccess:
    def test_output_is_held_and_never_passed_as_execution_result(self, worker, executor, repo, caplog):
        output = f'rows={MARKER}\n'
        executor.execute.return_value = _result(
            JobStatus.SUCCESS, output=output, step_timing={'total_ms': 12.5},
        )

        assert worker.process_job(_job()) is True

        repo.complete_job_with_held_output.assert_called_once()
        call_ = repo.complete_job_with_held_output.call_args
        assert call_.args == (JOB_ID,)
        assert call_.kwargs['status'] == 'success'
        assert call_.kwargs['kind'] == 'result'
        assert call_.kwargs['content'] == output
        assert call_.kwargs['execution_metrics'] == {'total_ms': 12.5}
        assert call_.kwargs['attested_output_hash'] is None
        for forbidden in ('execution_result', 'execution_output', 'execution_result_path', 'ai_result_path'):
            assert forbidden not in call_.kwargs
        # The only plain status write is the claim; it carries no output.
        assert [c.kwargs for c in repo.update_job_status.call_args_list] == [
            {'job_id': JOB_ID, 'status': 'executing'}
        ]
        _assert_never_logged(caplog)

    def test_verifies_attestation_against_output_and_holds_attested_hash(self, worker, executor, repo):
        output = f'mean={MARKER}'
        executor.execute.return_value = _result(
            JobStatus.SUCCESS, output=output, attestation={'attestation': {'attestation_document': 'ZG9j'}},
        )
        receipt = _receipt(_sha256(output).upper())

        with patch.object(ExecutorWorker, '_verify_attestation', return_value=receipt) as verify:
            assert worker.process_job(_job()) is True

        assert verify.call_args.kwargs['expected_output'] == output
        call_ = repo.complete_job_with_held_output.call_args
        assert call_.kwargs['attested_output_hash'] == _sha256(output)
        assert call_.kwargs['verification_receipt'] == receipt
        assert call_.kwargs['enclave_pcr0'] == 'aa'

    def test_attested_hash_mismatch_logs_hashes_only_and_still_holds(self, worker, executor, repo, caplog):
        output = f'mean={MARKER}'
        other_hash = 'b' * 64
        executor.execute.return_value = _result(
            JobStatus.SUCCESS, output=output, attestation={'attestation': {'attestation_document': 'ZG9j'}},
        )
        receipt = _receipt(other_hash, output_verified=False)

        with patch.object(ExecutorWorker, '_verify_attestation', return_value=receipt):
            assert worker.process_job(_job()) is True

        call_ = repo.complete_job_with_held_output.call_args
        assert call_.kwargs['kind'] == 'result'
        assert call_.kwargs['content'] == output
        assert call_.kwargs['attested_output_hash'] == other_hash
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(other_hash in w and _sha256(output) in w for w in warnings)
        _assert_never_logged(caplog)

    def test_no_attested_hash_is_held_from_an_unauthenticated_attestation(self, worker, executor, repo):
        output = 'mean=4.2'
        executor.execute.return_value = _result(
            JobStatus.SUCCESS, output=output, attestation={'attestation': {'attestation_document': 'ZG9j'}},
        )
        receipt = _receipt(_sha256(output), certificate_chain_valid=False)

        with patch.object(ExecutorWorker, '_verify_attestation', return_value=receipt):
            assert worker.process_job(_job()) is True

        call_ = repo.complete_job_with_held_output.call_args
        assert call_.kwargs['attested_output_hash'] is None
        assert call_.kwargs['verification_receipt'] == receipt

    def test_ai_decision_does_not_gate_execution(self, worker, executor, repo):
        executor.execute.return_value = _result(JobStatus.SUCCESS, output='42')
        job = _job(
            ai_decision='ai_rejected',
            validation_status='0.1',
            execution_metrics=json.dumps({'ai_decision': 'ai_rejected'}),
        )

        assert worker.process_job(job) is True

        executor.execute.assert_called_once()
        assert repo.complete_job_with_held_output.call_args.kwargs['status'] == 'success'


class TestFailure:
    def test_failure_holds_full_error_and_keeps_it_out_of_the_atl(self, worker, executor, repo, atl, caplog):
        error = f'Bundle execution failed:\nSTDOUT: {MARKER}\nSTDERR: Traceback (most recent call last): boom'
        executor.execute.return_value = _result(JobStatus.FAILED, error=error)

        assert worker.process_job(_job()) is False

        atl.submit_la_entry.assert_called_once_with(
            job_id=JOB_ID, error_class='failed', error_detail='',
        )
        assert MARKER not in _all_calls(atl)
        repo.complete_job_with_held_output.assert_called_once_with(
            JOB_ID, status='failed', kind='error', content=error, error_message=HELD_ERROR_MESSAGE,
        )
        assert MARKER not in repr(repo.update_job_status.call_args_list)
        _assert_never_logged(caplog)

    def test_timeout_uses_the_timeout_error_class(self, worker, executor, repo, atl):
        executor.execute.return_value = _result(JobStatus.TIMEOUT, error=f'timed out after {MARKER}')

        assert worker.process_job(_job()) is False

        atl.submit_la_entry.assert_called_once_with(
            job_id=JOB_ID, error_class='timeout', error_detail='',
        )
        assert repo.complete_job_with_held_output.call_args.kwargs['error_message'] == HELD_ERROR_MESSAGE

    def test_failure_without_error_text_holds_an_empty_error(self, worker, executor, repo):
        executor.execute.return_value = _result(JobStatus.FAILED, error=None)

        assert worker.process_job(_job()) is False

        assert repo.complete_job_with_held_output.call_args.kwargs['content'] == ''

    def test_rejected_build_is_not_held(self, worker, executor, repo, atl):
        error = 'Build validation failed: build.yml not found'
        executor.execute.return_value = _result(JobStatus.REJECTED, error=error)

        assert worker.process_job(_job()) is False

        repo.update_job_status.assert_called_with(
            job_id=JOB_ID, status='rejected', error_message=error, release_status='not_required',
        )
        repo.complete_job_with_held_output.assert_not_called()
        atl.submit_la_entry.assert_not_called()


class TestRealDataContact:
    """From the middleware request on, failure text is held; before it, it is platform text."""

    @pytest.mark.parametrize('how', ['error_response', 'exception'])
    def test_middleware_failure_text_is_held_not_logged(
        self, how, secure_worker, middleware, enclave, repo, atl, job_logs, caplog,
    ):
        text = f'execute query: pq: invalid input syntax for type integer: "{MARKER}"'
        if how == 'error_response':
            middleware.response = MiddlewareResponse(success=False, error=text)
        else:
            middleware.raises = RuntimeError(text)

        assert secure_worker.process_job(_job()) is False

        assert len(middleware.requests) == 1
        enclave.send_encrypted_data_to_enclave.assert_not_called()
        held = repo.complete_job_with_held_output.call_args
        assert held.args == (JOB_ID,)
        assert (held.kwargs['status'], held.kwargs['kind']) == ('failed', 'error')
        assert held.kwargs['error_message'] == HELD_ERROR_MESSAGE
        assert text in held.kwargs['content']
        assert [c.kwargs for c in repo.update_job_status.call_args_list] == [
            {'job_id': JOB_ID, 'status': 'executing'}
        ]
        atl.submit_la_entry.assert_called_once_with(job_id=JOB_ID, error_class='failed', error_detail='')
        assert MARKER not in _all_calls(atl)
        assert job_logs.add_detailed_log.called
        assert MARKER not in _all_calls(job_logs)
        _assert_never_logged(caplog)

    @pytest.mark.parametrize('how', ['error_response', 'exception'])
    def test_failed_job_log_write_does_not_log_the_middleware_text(
        self, how, secure_worker, middleware, repo, job_logs, caplog,
    ):
        # A DB outage while the failure is handled: the job_logs write fails inside the except block
        text = f'execute query: pq: invalid input syntax for type integer: "{MARKER}"'
        if how == 'error_response':
            middleware.response = MiddlewareResponse(success=False, error=text)
        else:
            middleware.raises = RuntimeError(text)
        job_logs.add_detailed_log.side_effect = RuntimeError('database is not accepting connections')

        assert secure_worker.process_job(_job()) is False

        assert text in repo.complete_job_with_held_output.call_args.kwargs['content']
        assert any('Failed to save log' in r.getMessage() for r in caplog.records)
        _assert_never_logged(caplog)

    @pytest.mark.parametrize('failing', ['query', 'attestation'])
    def test_proxy_failure_text_is_held_not_logged(
        self, failing, secure_worker, middleware, proxy, enclave, repo, job_logs, caplog,
    ):
        middleware.response = MiddlewareResponse(success=True, mode='proxy', proxy_info={
            'assigned_port': 10001, 'proxy_token': 't', 'session_id': 's', 'sql_query': 'SELECT age FROM patients',
        })
        if failing == 'query':
            enclave._send_to_enclave.return_value = {'attestation': {'attestation_document': 'ZG9j'}}
            proxy.fetch_encrypted_csv.side_effect = RuntimeError(f'[PROXY] Query failed: {MARKER}')
        else:
            enclave._send_to_enclave.side_effect = RuntimeError(f'attestation request failed near {MARKER}')

        assert secure_worker.process_job(_job()) is False

        enclave.send_encrypted_data_to_enclave.assert_not_called()
        held = repo.complete_job_with_held_output.call_args
        assert (held.kwargs['kind'], held.kwargs['error_message']) == ('error', HELD_ERROR_MESSAGE)
        if failing == 'query':
            assert MARKER in held.kwargs['content']
        assert MARKER not in _all_calls(job_logs)
        _assert_never_logged(caplog)

    def test_failure_before_real_data_is_requested_shows_the_platform_error(
        self, secure_worker, middleware, enclave, repo, atl,
    ):
        enclave.get_public_key.side_effect = ConnectionRefusedError('vsock connect refused (cid 16)')

        assert secure_worker.process_job(_job()) is False

        assert middleware.requests == []
        atl.submit_la_entry.assert_called_once_with(job_id=JOB_ID, error_class='failed', error_detail='')
        repo.update_job_status.assert_called_with(
            job_id=JOB_ID, status='failed', error_message='vsock connect refused (cid 16)',
            release_status='not_required',
        )
        repo.complete_job_with_held_output.assert_not_called()

    @pytest.mark.parametrize('release_status', ['pending', 'released', 'withheld'])
    def test_rerun_failing_before_real_data_keeps_the_held_output(
        self, release_status, secure_worker, middleware, enclave, repo, atl,
    ):
        enclave.get_public_key.side_effect = ConnectionRefusedError('vsock connect refused (cid 16)')

        assert secure_worker.process_job(_job(release_status=release_status)) is False

        repo.update_job_status.assert_called_with(job_id=JOB_ID, status='failed')
        repo.complete_job_with_held_output.assert_not_called()
        atl.submit_la_entry.assert_called_once_with(job_id=JOB_ID, error_class='failed', error_detail='')

    def test_build_validation_rejection_is_not_held(self, secure_worker, storage, middleware, enclave, repo, atl):
        (storage / 'repositories' / JOB_ID / 'build' / 'build.yml').unlink()

        assert secure_worker.process_job(_job()) is False

        assert middleware.requests == []
        enclave.get_public_key.assert_not_called()
        call_ = repo.update_job_status.call_args
        assert call_.kwargs['status'] == 'rejected'
        assert call_.kwargs['release_status'] == 'not_required'
        assert call_.kwargs['error_message'].startswith('Build validation failed: build.yml not found')
        repo.complete_job_with_held_output.assert_not_called()
        atl.submit_la_entry.assert_not_called()


class TestUnexpectedException:
    def test_exception_before_dispatch_is_not_required_with_the_raw_error(self, executor, repo, atl, tmp_path):
        worker = _make_worker(executor, tmp_path, atl_client=atl)  # no repository on disk

        assert worker.process_job(_job()) is False

        executor.execute.assert_not_called()
        atl.submit_la_entry.assert_called_once_with(
            job_id=JOB_ID, error_class='FileNotFoundError', error_detail='',
        )
        call_ = repo.update_job_status.call_args
        assert call_.kwargs['status'] == 'failed'
        assert call_.kwargs['release_status'] == 'not_required'
        assert call_.kwargs['error_message'].startswith('Repository not found')
        repo.complete_job_with_held_output.assert_not_called()

    def test_rerun_exception_before_dispatch_keeps_the_held_output(self, executor, repo, atl, tmp_path):
        worker = _make_worker(executor, tmp_path, atl_client=atl)  # no repository on disk

        assert worker.process_job(_job(release_status='pending')) is False

        repo.update_job_status.assert_called_once_with(job_id=JOB_ID, status='failed')
        repo.complete_job_with_held_output.assert_not_called()

    def test_exception_after_dispatch_is_held(self, worker, executor, repo, atl, caplog):
        executor.execute.side_effect = RuntimeError(f'decode failed near {MARKER}')

        assert worker.process_job(_job()) is False

        atl.submit_la_entry.assert_called_once_with(job_id=JOB_ID, error_class='RuntimeError', error_detail='')
        repo.complete_job_with_held_output.assert_called_once_with(
            JOB_ID, status='failed', kind='error',
            content=f'decode failed near {MARKER}', error_message=HELD_ERROR_MESSAGE,
        )
        assert MARKER not in repr(repo.update_job_status.call_args_list)
        _assert_never_logged(caplog)

    def test_exception_after_completion_leaves_the_job_unchanged(self, worker, executor, repo, atl):
        executor.execute.return_value = _result(JobStatus.SUCCESS, output='42')
        log = MagicMock()

        def info(message, *args, **kwargs):
            if message.startswith('[SUCCESS]'):
                raise RuntimeError(f'late failure {MARKER}')
        log.info.side_effect = info

        with patch('workers.executor.worker.get_logger', return_value=log):
            assert worker.process_job(_job()) is False

        repo.complete_job_with_held_output.assert_called_once()
        assert [c.kwargs['status'] for c in repo.update_job_status.call_args_list] == ['executing']
        atl.submit_la_entry.assert_not_called()
        assert MARKER not in repr(log.error.call_args_list)


class TestHeldWriteFailures:
    """The DB write that holds the output fails: retry, then hold the error, then a placeholder."""

    def test_success_write_failing_once_is_retried_with_the_same_arguments(self, worker, executor, repo, caplog):
        output = f'rows={MARKER}\n'
        executor.execute.return_value = _result(
            JobStatus.SUCCESS, output=output, step_timing={'total_ms': 3.0},
            attestation={'attestation': {'attestation_document': 'ZG9j'}},
        )
        receipt = _receipt(_sha256(output))
        repo.complete_job_with_held_output.side_effect = [WriteError(f'INSERT failed: {MARKER}'), {}]

        with patch.object(ExecutorWorker, '_verify_attestation', return_value=receipt):
            assert worker.process_job(_job()) is True

        first, retry = repo.complete_job_with_held_output.call_args_list
        assert retry == first
        assert (retry.kwargs['kind'], retry.kwargs['content']) == ('result', output)
        assert retry.kwargs['attested_output_hash'] == _sha256(output)
        assert retry.kwargs['verification_receipt'] == receipt
        assert [c.kwargs['status'] for c in repo.update_job_status.call_args_list] == ['executing']
        _assert_never_logged(caplog)

    def test_success_write_failing_twice_holds_the_second_error(self, worker, executor, repo, caplog):
        executor.execute.return_value = _result(JobStatus.SUCCESS, output=f'rows={MARKER}\n')
        repo.complete_job_with_held_output.side_effect = [
            WriteError(f'first: {MARKER}'), WriteError(f'second: {MARKER}'), {},
        ]

        assert worker.process_job(_job()) is False

        calls = repo.complete_job_with_held_output.call_args_list
        assert [c.kwargs['kind'] for c in calls] == ['result', 'result', 'error']
        assert calls[2] == call(
            JOB_ID, status='failed', kind='error', content=f'second: {MARKER}', error_message=HELD_ERROR_MESSAGE,
        )
        assert [c.kwargs['status'] for c in repo.update_job_status.call_args_list] == ['executing']
        _assert_never_logged(caplog)

    @pytest.mark.parametrize('origin', ['success_write', 'exception_after_dispatch'])
    def test_text_that_cannot_be_stored_is_held_as_a_placeholder(self, origin, worker, executor, repo, caplog):
        if origin == 'success_write':
            executor.execute.return_value = _result(JobStatus.SUCCESS, output=f'rows={MARKER}\n')
            failures = [WriteError(MARKER), WriteError(MARKER), HoldError(MARKER)]
        else:
            executor.execute.side_effect = RuntimeError(f'decode failed near {MARKER}')
            failures = [HoldError(MARKER)]
        repo.complete_job_with_held_output.side_effect = failures + [{}]

        assert worker.process_job(_job()) is False

        placeholder = repo.complete_job_with_held_output.call_args_list[-1]
        assert placeholder == call(
            JOB_ID, status='failed', kind='error',
            content=UNSTORED_OUTPUT_MESSAGE.format(error_type='HoldError'), error_message=HELD_ERROR_MESSAGE,
        )
        assert [c.kwargs['status'] for c in repo.update_job_status.call_args_list] == ['executing']
        _assert_never_logged(caplog)

    def test_nothing_escapes_when_no_write_succeeds(self, worker, executor, repo, caplog):
        executor.execute.return_value = _result(JobStatus.SUCCESS, output=f'rows={MARKER}\n')
        repo.complete_job_with_held_output.side_effect = WriteError(f"[parameters: {{'content': '{MARKER}'}}]")

        assert worker.process_job(_job()) is False
        # result, its retry, the error, the placeholder; the job is left 'executing'
        assert len(repo.complete_job_with_held_output.call_args_list) == 4
        assert [c.kwargs['status'] for c in repo.update_job_status.call_args_list] == ['executing']

        _with_poll_loop(worker)
        with patch.object(worker, 'fetch_jobs', return_value=[_job()]), \
                patch.object(worker, '_update_job_failed') as update_job_failed:
            assert worker._poll_cycle() == 0

        update_job_failed.assert_not_called()
        assert worker.stats['jobs_failed'] == 1
        assert [r.getMessage() for r in caplog.records if r.exc_info] == []
        _assert_never_logged(caplog)


class TestVerificationReceipt:
    def test_nitro_receipt_records_the_attested_output_hash(self, worker):
        vr = SimpleNamespace(
            valid=True, syntax_valid=True, certificate_chain_valid=True, aws_signature_valid=True,
            pcr_verified=True, output_verified=True, pcr0='aa', pcr1='bb', pcr2='cc',
            output_hash='AB' * 32, module_id='i-0abc', timestamp=None, error=None, timing={},
            user_data={'output_hash': 'AB' * 32},
        )
        attestation = {'attestation': {'attestation_document': 'ZG9j'}}

        with patch('workers.executor.worker.verify_attestation', return_value=vr) as verify:
            receipt = worker._verify_attestation(JOB_ID, attestation, MagicMock(), expected_output='out')

        assert verify.call_args.kwargs['expected_output'] == 'out'
        assert json.loads(receipt)['output_hash'] == 'AB' * 32
        assert _attested_output_hash(receipt) == 'ab' * 32

    def test_nitro_mismatch_error_does_not_carry_the_held_output_hash(self, worker):
        vr = SimpleNamespace(
            valid=False, syntax_valid=True, certificate_chain_valid=True, aws_signature_valid=True,
            pcr_verified=True, output_verified=False, pcr0='aa', pcr1='bb', pcr2='cc',
            output_hash='ab' * 32, module_id='i-0abc', timestamp=None, timing={},
            error=f"Output hash mismatch: got {'ab' * 32}, expected {_sha256('out')}",
            user_data={'output_hash': 'ab' * 32},
        )
        attestation = {'attestation': {'attestation_document': 'ZG9j'}}

        with patch('workers.executor.worker.verify_attestation', return_value=vr):
            receipt = worker._verify_attestation(JOB_ID, attestation, MagicMock(), expected_output='out')

        assert json.loads(receipt)['error'] == 'Output hash mismatch'
        assert _sha256('out') not in receipt
        assert _attested_output_hash(receipt) == 'ab' * 32

    def test_tdx_receipt_records_the_attested_output_hash(self, worker):
        attestation = {'attestation': {'backend': 'tdx', 'attestation_document': 'ZG9j'}}
        tdx_result = TdxVerificationResult(
            valid=True, syntax_valid=True, certificate_chain_valid=True, signature_valid=True,
            pcr_verified=True, output_verified=True, measurements={'mrtd': 'aa'}, output_hash=_sha256('out'),
        )

        with patch('workers.executor.worker.verify_tdx_attestation', return_value=tdx_result) as verify:
            receipt = worker._verify_attestation(JOB_ID, attestation, MagicMock(), expected_output='out')

        assert verify.call_args.kwargs['expected_output'] == 'out'
        assert json.loads(receipt)['output_hash'] == _sha256('out')
        assert json.loads(receipt)['pcrs']['pcr0'] == 'aa'
        assert _attested_output_hash(receipt) == _sha256('out')

    @pytest.mark.parametrize('receipt', [
        None,
        '',
        'not json',
        json.dumps(['not', 'an', 'object']),
        json.dumps({'valid': False, 'error': 'boom'}),
        json.dumps({'output_hash': 'ab' * 32}),  # no checks
        _receipt('xyz'),
        _receipt('a' * 63),
    ])
    def test_no_attested_hash_without_a_well_formed_one(self, receipt):
        assert _attested_output_hash(receipt) is None

    @pytest.mark.parametrize('check', AUTHENTICITY_CHECKS)
    def test_no_attested_hash_unless_the_attestation_is_authentic(self, check):
        assert _attested_output_hash(_receipt('ab' * 32)) == 'ab' * 32
        assert _attested_output_hash(_receipt('ab' * 32, **{check: False})) is None

    def test_attested_hash_is_kept_when_only_the_output_check_fails(self):
        # A genuine output mismatch: the reviewer must still see the mismatch.
        assert _attested_output_hash(_receipt('AB' * 32, output_verified=False)) == 'ab' * 32


class TestNothingReachesJobLogs:
    """Real SecureExecutor + JobLogger; only job_repository.add_detailed_log is replaced."""

    def test_enclave_failure_text_is_held_not_logged(self, secure_worker, enclave, repo, job_logs, caplog):
        error = f'Script failed (exit code 1)\nSTDOUT: {MARKER}\nSTDERR: KeyError'
        enclave.send_encrypted_data_to_enclave.return_value = (False, error, None)

        assert secure_worker.process_job(_job()) is False

        assert job_logs.add_detailed_log.called
        assert MARKER not in _all_calls(job_logs)
        assert repo.complete_job_with_held_output.call_args.kwargs['content'] == error
        _assert_never_logged(caplog)

    def test_exception_after_dispatch_is_held_not_logged(self, secure_worker, enclave, repo, job_logs, caplog):
        enclave.send_encrypted_data_to_enclave.side_effect = ValueError(f'bad response: {MARKER}')

        assert secure_worker.process_job(_job()) is False

        assert MARKER not in _all_calls(job_logs)
        call_ = repo.complete_job_with_held_output.call_args
        assert call_.kwargs['kind'] == 'error'
        assert MARKER in call_.kwargs['content']
        assert call_.kwargs['error_message'] == HELD_ERROR_MESSAGE
        _assert_never_logged(caplog)

    def test_success_output_is_held_not_logged(self, secure_worker, enclave, repo, job_logs, caplog):
        enclave.send_encrypted_data_to_enclave.return_value = (True, f'count={MARKER}\n', None)

        assert secure_worker.process_job(_job()) is True

        assert MARKER not in _all_calls(job_logs)
        assert repo.complete_job_with_held_output.call_args.kwargs['content'] == f'count={MARKER}\n'
        _assert_never_logged(caplog)


class TestEnclaveClientsDoNotLogTheEnclaveError:
    def test_nitro_client(self, caplog):
        client = EnclaveClient(enclave_cid=16)
        with patch.object(client, '_send_to_enclave', return_value={'success': False, 'error': MARKER}):
            assert client.send_encrypted_data_to_enclave('session', 'zip') == (False, MARKER, None)
            assert client.send_encrypted_data_with_db_fetch('session', 'zip', 'creds', 'SELECT 1') == (False, MARKER, None)
        _assert_never_logged(caplog)

    def test_tdx_client(self, caplog):
        client = EnclaveClientTDX(host='127.0.0.1', port=1)
        with patch.object(client, '_send_to_td', return_value={'success': False, 'error': MARKER}):
            assert client.send_encrypted_data_to_enclave('session', 'zip') == (False, MARKER, None)
        _assert_never_logged(caplog)


class TestDataClientsDoNotLogDataText:
    """The middleware and the data owner's proxy query the real dataset; their text never reaches the logs."""

    DB_ERROR = f'execute query: pq: invalid input syntax for type integer: "{MARKER}"'

    @staticmethod
    def _response(status_code, body=None, text=None):
        response = MagicMock()
        response.status_code = status_code
        response.text = text if text is not None else json.dumps(body)
        response.content = response.text.encode()
        response.headers = {}
        if body is None:
            response.json.side_effect = ValueError('not json')
        else:
            response.json.return_value = body
        return response

    @pytest.fixture
    def request_(self):
        return MiddlewareRequest(dataset_id='ds-001', archetype_id='arch-001', public_key='pk', job_id=JOB_ID)

    @pytest.fixture
    def middleware_client(self):
        settings = MagicMock()
        settings.middleware.timeout_seconds = 5
        return MiddlewareClient(settings, 'http://middleware.invalid')

    @pytest.fixture
    def proxy_client(self):
        settings = MagicMock(request_timeout_seconds=5, rathole_host='127.0.0.1', enabled=True)
        return ProxyClient(settings)

    def _proxy_fetch(self, proxy_client, request_):
        return proxy_client.fetch_encrypted_csv(
            request=request_, public_key='pk', attestation_doc='doc',
            proxy_info={'assigned_port': 10001, 'proxy_token': 't', 'session_id': 's', 'sql_query': 'SELECT 1'},
        )

    def test_middleware_error_body_is_returned_not_logged(self, middleware_client, request_, caplog):
        response = self._response(500, {'success': False, 'error': self.DB_ERROR})

        with patch('workers.executor.clients.requests.post', return_value=response):
            result = middleware_client.fetch_encrypted_csv(request_)

        assert (result.success, result.error) == (False, self.DB_ERROR)
        _assert_never_logged(caplog)

    def test_middleware_unexpected_error_is_returned_not_logged(self, middleware_client, request_, caplog):
        with patch('workers.executor.clients.requests.post', side_effect=RuntimeError(self.DB_ERROR)):
            result = middleware_client.fetch_encrypted_csv(request_)

        assert (result.success, result.error) == (False, self.DB_ERROR)
        assert [r.getMessage() for r in caplog.records if r.exc_info] == []
        _assert_never_logged(caplog)

    @pytest.mark.parametrize('status_code, body, text, raised', [
        (500, {'success': False, 'error': 'QUERY_FAILED', 'message': DB_ERROR}, None, 'QUERY_FAILED'),
        (500, None, f'<html>{DB_ERROR}</html>', 'Proxy returned HTTP 500'),
        (200, {'success': False, 'error': 'QUERY_FAILED', 'message': DB_ERROR}, None, 'QUERY_FAILED'),
    ])
    def test_proxy_error_keeps_its_code_and_logs_no_message(
        self, status_code, body, text, raised, proxy_client, request_, caplog,
    ):
        response = self._response(status_code, body, text)

        with patch('workers.executor.clients.requests.post', return_value=response):
            with pytest.raises(RuntimeError) as excinfo:
                self._proxy_fetch(proxy_client, request_)

        assert str(excinfo.value) == f'[PROXY] Query failed: {raised}'
        _assert_never_logged(caplog)

    def test_proxy_success_metadata_is_returned_not_logged(self, proxy_client, request_, caplog):
        metadata = {'row_count': 4217, 'query_duration_ms': 3, 'note': MARKER}
        response = self._response(200, {'success': True, 'encrypted_csv': 'ZW5j', 'metadata': metadata})

        with patch('workers.executor.clients.requests.post', return_value=response):
            result = self._proxy_fetch(proxy_client, request_)

        assert (result.encrypted_csv, result.metadata) == ('ZW5j', metadata)
        _assert_never_logged(caplog)
        assert not [r for r in caplog.records if 'row_count' in r.getMessage()]
