"""Signed records of every run, bound to the job and the execution attempt (unit, no database).

The worker records an attempt id before dispatch; the executor sends it and the platform
job id to the enclave, which signs them. A failed run returns a signed record too. The
verification receipt records the signed outcome and identifiers and checks them against
this job, this attempt and, for a failed run, the error text.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.db import HELD_ERROR_MESSAGE
from workers.executor.attempts import (
    AttemptBinding,
    check_attempt_binding,
    enclave_identifier,
    error_text_hash,
    new_attempt_id,
)
from workers.executor.clients import EnclaveClient, EnclaveClientTDX
from workers.executor.constants import EnclaveOperations
from workers.executor.executor import SecureExecutor
from workers.executor.models import ExecutionResult, JobStatus
from workers.executor.tdx_verifier import TdxVerificationResult
from workers.executor.worker import ExecutorWorker, _attested_output_hash

JOB_ID = 'JOB-SR-1'
ATTEMPT = 'a1' * 16
OTHER_ATTEMPT = 'b2' * 16
ERROR = 'Execution failed: Script failed (exit code 3)\nSTDOUT: \nSTDERR: KeyError'
EMPTY_OUTPUT_HASH = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
ATTESTATION = {'attestation': {'attestation_document': 'ZG9j'}}
TDX_ATTESTATION = {'attestation': {'backend': 'tdx', 'attestation_document': 'ZG9j'}}


def _nitro_result(user_data, **overrides):
    """What epsilon_verifier.verify_attestation returns; every check passes unless overridden."""
    fields = dict(
        valid=True, syntax_valid=True, certificate_chain_valid=True, aws_signature_valid=True,
        pcr_verified=True, output_verified=True, pcr0='aa', pcr1='bb', pcr2='cc',
        output_hash=user_data.get('output_hash'), module_id='i-0abc', timestamp=None, error=None, timing={},
        user_data=user_data,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _failed_user_data(**overrides):
    user_data = {
        'job_id': 'rsa-session-0a1b2c3d', 'output_hash': EMPTY_OUTPUT_HASH, 'outcome': 'failed', 'exit_code': 3,
        'error_hash': error_text_hash(ERROR), 'platform_job_id': JOB_ID, 'attempt_id': ATTEMPT,
    }
    user_data.update(overrides)
    return user_data


def _job():
    return {
        'job_id': JOB_ID, 'workspace_id': 'ws-001', 'user_id': 'user-001', 'researcher_nonce': None,
        'commit_sha': 'abc123', 'github_repo': 'alice/analysis', 'github_branch': 'main',
        'commit_message': 'run', 'commit_author': 'alice', 'created_at': '2026-11-12T00:00:00',
    }


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
def worker(executor, tmp_path):
    (tmp_path / 'repositories' / JOB_ID).mkdir(parents=True)
    # Bypass __init__: it validates the environment and touches the database.
    worker = ExecutorWorker.__new__(ExecutorWorker)
    worker._settings = MagicMock()
    worker._settings.storage.shared_storage_path = str(tmp_path)
    worker._executor = executor
    worker._atl_client = None
    return worker


def _receipt(worker, user_data, expected_attempt_id=ATTEMPT, error_text=None, expected_output='', **overrides):
    with patch('workers.executor.worker.verify_attestation', return_value=_nitro_result(user_data, **overrides)):
        return json.loads(worker._verify_attestation(
            JOB_ID, ATTESTATION, MagicMock(), expected_output=expected_output,
            expected_attempt_id=expected_attempt_id, error_text=error_text,
        ))


class TestAttemptBinding:
    def test_attempt_ids_are_unpredictable_and_accepted_by_the_enclave(self):
        first, second = new_attempt_id(), new_attempt_id()
        assert first != second and len(first) == 32
        assert enclave_identifier(first) == first

    @pytest.mark.parametrize('value', [None, '', 'a b', 'job/1', 'x' * 65, 7])
    def test_an_identifier_the_enclave_would_refuse_is_not_sent(self, value):
        assert enclave_identifier(value) is None

    @pytest.mark.parametrize('signed, expected_attempt, binding', [
        ({'attempt_id': ATTEMPT, 'platform_job_id': JOB_ID}, ATTEMPT, AttemptBinding.BOUND),
        ({'attempt_id': ATTEMPT}, ATTEMPT, AttemptBinding.BOUND),
        ({'attempt_id': OTHER_ATTEMPT, 'platform_job_id': JOB_ID}, ATTEMPT, AttemptBinding.MISMATCH),
        ({'attempt_id': ATTEMPT, 'platform_job_id': 'JOB-OTHER'}, ATTEMPT, AttemptBinding.MISMATCH),
        ({'platform_job_id': JOB_ID}, ATTEMPT, AttemptBinding.UNBOUND_LEGACY),
        ({'attempt_id': ATTEMPT, 'platform_job_id': JOB_ID}, None, AttemptBinding.NO_EXPECTED_ATTEMPT),
    ])
    def test_signed_identifiers_are_compared_with_the_job_and_its_attempt(self, signed, expected_attempt, binding):
        assert check_attempt_binding(signed, JOB_ID, expected_attempt) == binding


class TestEnclaveRequest:
    @pytest.fixture
    def nitro(self):
        return EnclaveClient(enclave_cid=16)

    @pytest.fixture
    def tdx(self):
        return EnclaveClientTDX(host='127.0.0.1', port=5005)

    def test_nitro_sends_the_identifiers_with_both_operations(self, nitro):
        with patch.object(nitro, '_send_to_enclave', return_value={'success': True, 'output': ''}) as send:
            nitro.send_encrypted_data_to_enclave('s', 'zip', 'csv', platform_job_id=JOB_ID, attempt_id=ATTEMPT)
            nitro.send_encrypted_data_with_db_fetch('s', 'zip', 'creds', 'SELECT 1', platform_job_id=JOB_ID, attempt_id=ATTEMPT)

        script, db_fetch = (c.args[0] for c in send.call_args_list)
        assert (script['operation'], db_fetch['operation']) == (
            EnclaveOperations.EXECUTE_SCRIPT, EnclaveOperations.EXECUTE_DB_FETCH,
        )
        for request in (script, db_fetch):
            assert (request['platform_job_id'], request['attempt_id']) == (JOB_ID, ATTEMPT)

    def test_identifiers_that_are_not_given_are_left_out_of_the_request(self, nitro):
        with patch.object(nitro, '_send_to_enclave', return_value={'success': True, 'output': ''}) as send:
            nitro.send_encrypted_data_to_enclave('s', 'zip')

        assert 'platform_job_id' not in send.call_args.args[0] and 'attempt_id' not in send.call_args.args[0]

    def test_nitro_keeps_the_signed_record_of_a_failed_run(self, nitro):
        response = {
            'success': False, 'error': ERROR, 'outcome': 'failed', 'exit_code': 3,
            'attestation': {'attestation': {'attestation_document': 'ZG9j'}}, 'timing': {'script_execution_ms': 5},
        }
        with patch.object(nitro, '_send_to_enclave', return_value=response):
            success, error, attestation, files = nitro.send_encrypted_data_to_enclave('s', 'zip')

        assert (success, error, files) == (False, ERROR, None)
        assert attestation['attestation'] == {'attestation_document': 'ZG9j'}
        assert attestation['timing'] == {'script_execution_ms': 5}

    def test_a_failed_run_from_an_enclave_that_signs_none_has_no_record(self, nitro):
        with patch.object(nitro, '_send_to_enclave', return_value={'success': False, 'error': ERROR}):
            assert nitro.send_encrypted_data_with_db_fetch('s', 'zip', 'creds', 'SELECT 1') == (False, ERROR, None, None)

    def test_tdx_sends_the_identifiers_and_keeps_the_record_of_a_failed_run(self, tdx):
        response = {'success': False, 'error': ERROR, 'attestation': dict(TDX_ATTESTATION)}
        with patch.object(tdx, '_send_to_td', return_value=response) as send:
            success, error, attestation, _ = tdx.send_encrypted_data_to_enclave(
                's', 'zip', platform_job_id=JOB_ID, attempt_id=ATTEMPT,
            )

        assert (send.call_args.args[0]['platform_job_id'], send.call_args.args[0]['attempt_id']) == (JOB_ID, ATTEMPT)
        assert (success, error) == (False, ERROR)
        assert attestation['attestation']['backend'] == 'tdx'


class TestExecutorPassesIdentifiers:
    @pytest.fixture
    def secure_executor(self):
        enclave = MagicMock()
        enclave.send_encrypted_data_to_enclave.return_value = (True, 'out', None, None)
        executor = SecureExecutor(enclave_client=enclave, settings=MagicMock(), middleware_client=MagicMock())
        return executor, enclave

    def test_step_passes_the_identifiers_to_the_enclave_client(self, secure_executor):
        executor, enclave = secure_executor

        executor._step_execute_in_enclave(JOB_ID, 's', 'zip', None, platform_job_id=JOB_ID, attempt_id=ATTEMPT)

        kwargs = enclave.send_encrypted_data_to_enclave.call_args.kwargs
        assert (kwargs['platform_job_id'], kwargs['attempt_id']) == (JOB_ID, ATTEMPT)


class TestReceipt:
    def test_failed_run_bound_to_this_attempt_is_valid(self, worker):
        receipt = _receipt(worker, _failed_user_data(), error_text=ERROR)

        assert receipt['valid'] is True and receipt['error'] is None
        assert (receipt['outcome'], receipt['exit_code']) == ('failed', 3)
        assert (receipt['platform_job_id'], receipt['attempt_id']) == (JOB_ID, ATTEMPT)
        assert receipt['expected_attempt_id'] == ATTEMPT
        assert receipt['attempt_binding'] == 'bound'
        assert receipt['error_hash_verified'] is True

    def test_the_empty_output_of_a_failed_run_is_what_the_verifier_checks(self, worker):
        with patch('workers.executor.worker.verify_attestation', return_value=_nitro_result(_failed_user_data())) as verify:
            worker._verify_attestation(JOB_ID, ATTESTATION, MagicMock(), expected_output='', error_text=ERROR)

        assert verify.call_args.kwargs['expected_output'] == ''

    def test_a_record_of_another_error_text_is_not_valid(self, worker):
        receipt = _receipt(worker, _failed_user_data(), error_text=ERROR + ' altered')

        assert receipt['error_hash_verified'] is False
        assert (receipt['valid'], receipt['error']) == (False, 'Error hash mismatch')

    def test_a_failed_run_without_a_signed_error_hash_is_not_valid(self, worker):
        user_data = _failed_user_data()
        del user_data['error_hash']

        assert _receipt(worker, user_data, error_text=ERROR)['valid'] is False

    @pytest.mark.parametrize('overrides', [{'attempt_id': OTHER_ATTEMPT}, {'platform_job_id': 'JOB-OTHER'}])
    def test_a_record_of_another_attempt_or_job_is_not_valid(self, worker, overrides):
        receipt = _receipt(worker, _failed_user_data(**overrides), error_text=ERROR)

        assert receipt['attempt_binding'] == 'mismatch'
        assert (receipt['valid'], receipt['error']) == (False, 'Attempt binding mismatch')

    def test_a_successful_run_of_another_attempt_attests_nothing_about_the_output(self, worker):
        user_data = {'output_hash': 'ab' * 32, 'outcome': 'succeeded', 'exit_code': 0,
                     'platform_job_id': JOB_ID, 'attempt_id': OTHER_ATTEMPT}
        with patch('workers.executor.worker.verify_attestation', return_value=_nitro_result(user_data)):
            mismatched = worker._verify_attestation(JOB_ID, ATTESTATION, MagicMock(), expected_attempt_id=ATTEMPT)
            bound = worker._verify_attestation(JOB_ID, ATTESTATION, MagicMock(), expected_attempt_id=OTHER_ATTEMPT)

        assert _attested_output_hash(mismatched) is None
        assert _attested_output_hash(bound) == 'ab' * 32
        assert json.loads(bound)['error_hash_verified'] is None

    def test_a_record_from_an_older_enclave_is_unbound_and_stays_valid(self, worker):
        receipt = _receipt(worker, {'output_hash': 'ab' * 32}, expected_output='out')

        assert receipt['attempt_binding'] == 'unbound_legacy'
        assert receipt['valid'] is True
        assert (receipt['outcome'], receipt['attempt_id']) == (None, None)

    @pytest.mark.parametrize('check', ['syntax_valid', 'certificate_chain_valid', 'aws_signature_valid', 'pcr_verified'])
    def test_nothing_is_taken_from_a_record_that_is_not_authentic(self, worker, check):
        receipt = _receipt(worker, _failed_user_data(), error_text=ERROR, valid=False, **{check: False})

        for name in ('outcome', 'exit_code', 'error_hash', 'platform_job_id', 'attempt_id', 'attempt_binding'):
            assert receipt[name] is None
        assert receipt['valid'] is False

    @pytest.mark.parametrize('user_data', [
        {'outcome': 'anything', 'exit_code': True, 'error_hash': 'zz', 'attempt_id': 5, 'platform_job_id': ['x']},
    ])
    def test_malformed_signed_values_are_dropped(self, worker, user_data):
        receipt = _receipt(worker, user_data)

        for name in ('outcome', 'exit_code', 'error_hash', 'platform_job_id', 'attempt_id'):
            assert receipt[name] is None
        assert receipt['attempt_binding'] == 'unbound_legacy'

    def test_tdx_receipt_records_the_bound_fields(self, worker):
        tdx_result = TdxVerificationResult(
            valid=True, syntax_valid=True, certificate_chain_valid=True, signature_valid=True,
            pcr_verified=True, output_verified=True, measurements={'mrtd': 'aa'}, output_hash=EMPTY_OUTPUT_HASH,
            execution_fields={'outcome': 'timed_out', 'error_hash': error_text_hash(ERROR),
                              'platform_job_id': JOB_ID, 'attempt_id': ATTEMPT},
        )
        with patch('workers.executor.worker.verify_tdx_attestation', return_value=tdx_result):
            receipt = json.loads(worker._verify_attestation(
                JOB_ID, TDX_ATTESTATION, MagicMock(), expected_output='', expected_attempt_id=ATTEMPT, error_text=ERROR,
            ))

        assert (receipt['outcome'], receipt['attempt_binding'], receipt['error_hash_verified']) == ('timed_out', 'bound', True)
        assert receipt['valid'] is True


class TestProcessJob:
    def test_the_attempt_is_recorded_before_dispatch_and_sent_with_the_request(self, worker, executor, repo):
        order = MagicMock()
        order.attach_mock(repo.record_execution_attempt, 'record')
        order.attach_mock(executor.execute, 'execute')
        executor.execute.return_value = ExecutionResult(job_id=JOB_ID, status=JobStatus.SUCCESS, execution_time=1, output='out')

        assert worker.process_job(_job()) is True

        assert [c[0] for c in order.mock_calls] == ['record', 'execute']
        recorded_job, recorded_attempt = repo.record_execution_attempt.call_args.args
        assert recorded_job == JOB_ID and enclave_identifier(recorded_attempt) == recorded_attempt
        assert executor.execute.call_args.args[0].attempt_id == recorded_attempt

    def test_each_run_of_a_job_is_a_new_attempt(self, worker, executor, repo):
        executor.execute.return_value = ExecutionResult(job_id=JOB_ID, status=JobStatus.SUCCESS, execution_time=1, output='out')

        worker.process_job(_job())
        worker.process_job(_job())

        first, second = (c.args[1] for c in repo.record_execution_attempt.call_args_list)
        assert first != second

    def test_a_failed_run_keeps_its_signed_record_with_the_held_error(self, worker, executor, repo):
        executor.execute.return_value = ExecutionResult(
            job_id=JOB_ID, status=JobStatus.FAILED, execution_time=1, error=ERROR, attestation=ATTESTATION,
        )
        with patch('workers.executor.worker.verify_attestation') as verify, \
                patch('workers.executor.worker.new_attempt_id', return_value=ATTEMPT), \
                patch.dict('os.environ', {'ENCLAVE_VERSION': '1.7.0'}):
            verify.return_value = _nitro_result(_failed_user_data())
            assert worker.process_job(_job()) is False

        assert verify.call_args.kwargs['expected_output'] == ''
        held = repo.complete_job_with_held_output.call_args
        assert held.args == (JOB_ID,)
        assert (held.kwargs['status'], held.kwargs['kind'], held.kwargs['content']) == ('failed', 'error', ERROR)
        assert held.kwargs['error_message'] == HELD_ERROR_MESSAGE
        assert held.kwargs['attestation'] == ATTESTATION
        assert (held.kwargs['analysis_outcome'], held.kwargs['attempt_binding']) == ('failed', 'bound')
        assert (held.kwargs['enclave_version'], held.kwargs['enclave_pcr0']) == ('1.7.0', 'aa')
        receipt = json.loads(held.kwargs['verification_receipt'])
        assert (receipt['valid'], receipt['exit_code'], receipt['error_hash_verified']) == (True, 3, True)
        # The script chooses its exit code, so it is held with the receipt, not written to a job column
        assert 'exit_code' not in held.kwargs
