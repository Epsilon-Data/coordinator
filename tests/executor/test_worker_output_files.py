"""Output files in ExecutorWorker.process_job and the enclave clients (unit, no database).

The worker verifies the enclave's files against the response and the attestation
user_data, writes their bytes to the store and holds their rows with the result.
Any mismatch fails the job with a held error, and nothing is stored.
"""
import base64
import hashlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.db import HELD_ERROR_MESSAGE
from shared.output_manifest import build_manifest, file_type, manifest_sha256
from workers.executor.clients import EnclaveClient, EnclaveClientTDX
from workers.executor.executor import SecureExecutor
from workers.executor.models import ExecutionResult, JobStatus
from workers.executor.output_files import LocalOutputFileStore
from workers.executor.settings import OutputFilesConfig
from workers.executor.tdx_verifier import TdxVerificationResult
from workers.executor.worker import ExecutorWorker

JOB_ID = 'JOB-FILES-1'
OUTPUT = 'count=7\n'
MARKER = b'MARKER-d8b4f1a6-real-data-cell-value-9031'
CHART = b'\x89PNG\r\n\x1a\n' + MARKER
TABLE = b'group,n\na,' + MARKER + b'\n'
ATTESTATION = {'attestation': {'attestation_document': 'ZG9j'}}
EMPTY_MANIFEST_SHA256 = '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _entry(name, content):
    return {
        'name': name, 'size': len(content), 'sha256': _sha256(content), 'type': file_type(name),
        'content_b64': base64.b64encode(content).decode(),
    }


def _files(*entries, manifest_hash=None):
    entries = list(entries)
    if manifest_hash is None:
        manifest_hash = manifest_sha256(build_manifest(entries))
    return {'files': entries, 'files_manifest_sha256': manifest_hash}


GOOD = _files(_entry('table.csv', TABLE), _entry('chart.png', CHART))
GOOD_MANIFEST = GOOD['files_manifest_sha256']


def _receipt(**fields):
    """A Nitro verification receipt of an authentic attestation of OUTPUT."""
    checks = dict.fromkeys(['syntax_valid', 'certificate_chain_valid', 'signature_valid', 'pcr_verified',
                            'output_verified'], True)
    return json.dumps({'valid': True, 'checks': checks, 'pcrs': {'pcr0': 'aa'},
                       'output_hash': _sha256(OUTPUT.encode()), **fields})


def _job():
    return {
        'job_id': JOB_ID, 'workspace_id': 'ws-001', 'user_id': 'user-001', 'researcher_nonce': None,
        'commit_sha': 'abc123', 'github_repo': 'alice/analysis', 'github_branch': 'main',
        'commit_message': 'run', 'commit_author': 'alice', 'created_at': '2026-10-18T00:00:00',
    }


@pytest.fixture
def storage(tmp_path):
    (tmp_path / 'repositories' / JOB_ID).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def store_dir(tmp_path):
    return tmp_path / 'output-files'


@pytest.fixture
def executor():
    ex = MagicMock()
    ex.is_ready = True
    return ex


@pytest.fixture
def atl():
    client = MagicMock()
    client.submit_la_entry.return_value = (True, {'leaf_index': 3})
    client.submit_ha_entry.return_value = None
    return client


@pytest.fixture
def worker(executor, storage, store_dir, atl):
    # Bypass __init__: it validates the environment and touches the database.
    worker = ExecutorWorker.__new__(ExecutorWorker)
    worker._settings = MagicMock()
    worker._settings.storage.shared_storage_path = str(storage)
    worker._settings.output_files = OutputFilesConfig(store='local', directory=str(store_dir))
    worker._output_file_store = LocalOutputFileStore(str(store_dir))
    worker._executor = executor
    worker._atl_client = atl
    return worker


@pytest.fixture
def repo():
    with patch('workers.executor.worker.job_repository') as mock:
        yield mock


@pytest.fixture(autouse=True)
def capture_all_logs(caplog):
    caplog.set_level(logging.DEBUG)


def _run(worker, executor, output_files, receipt=None, attestation=ATTESTATION):
    executor.execute.return_value = ExecutionResult(
        job_id=JOB_ID, status=JobStatus.SUCCESS, execution_time=1.0, output=OUTPUT,
        attestation=attestation, output_files=output_files,
    )
    with patch.object(ExecutorWorker, '_verify_attestation', return_value=receipt), \
            patch.object(ExecutorWorker, '_submit_to_atl', return_value=None):
        return worker.process_job(_job())


def _stored(store_dir):
    return sorted(p.name for p in store_dir.rglob('*') if p.is_file()) if store_dir.exists() else []


def _assert_failed_with(repo, message_start):
    call_ = repo.complete_job_with_held_output.call_args
    assert call_.kwargs['status'] == 'failed'
    assert call_.kwargs['kind'] == 'error'
    assert call_.kwargs['content'].startswith(message_start)
    assert call_.kwargs['error_message'] == HELD_ERROR_MESSAGE
    assert 'files' not in call_.kwargs


def _assert_never_logged(caplog):
    for record in caplog.records:
        assert MARKER.decode() not in record.getMessage()


class TestAcceptedFiles:
    def test_good_files_are_stored_and_held_with_the_result(self, worker, executor, repo, store_dir, caplog):
        assert _run(worker, executor, GOOD, _receipt(files_manifest_sha256=GOOD_MANIFEST)) is True

        call_ = repo.complete_job_with_held_output.call_args
        assert call_.kwargs['kind'] == 'result'
        assert call_.kwargs['files_manifest_sha256'] == GOOD_MANIFEST
        assert call_.kwargs['files'] == [
            {'name': 'chart.png', 'size': len(CHART), 'content_type': 'image/png', 'sha256': _sha256(CHART),
             'storage_key': f'jobs/{JOB_ID}/{_sha256(CHART)}'},
            {'name': 'table.csv', 'size': len(TABLE), 'content_type': 'text/csv', 'sha256': _sha256(TABLE),
             'storage_key': f'jobs/{JOB_ID}/{_sha256(TABLE)}'},
        ]
        assert (store_dir / 'jobs' / JOB_ID / _sha256(CHART)).read_bytes() == CHART
        assert (store_dir / 'jobs' / JOB_ID / _sha256(TABLE)).read_bytes() == TABLE
        _assert_never_logged(caplog)

    def test_an_old_enclave_without_files_holds_no_manifest(self, worker, executor, repo, store_dir):
        assert _run(worker, executor, None, _receipt()) is True

        call_ = repo.complete_job_with_held_output.call_args
        assert (call_.kwargs['files'], call_.kwargs['files_manifest_sha256']) == ([], None)
        assert _stored(store_dir) == []

    def test_a_run_without_files_holds_the_empty_manifest_even_without_a_store(self, worker, executor, repo):
        worker._output_file_store = None
        empty = _files()

        assert _run(worker, executor, empty, _receipt(files_manifest_sha256=EMPTY_MANIFEST_SHA256)) is True

        call_ = repo.complete_job_with_held_output.call_args
        assert (call_.kwargs['files'], call_.kwargs['files_manifest_sha256']) == ([], EMPTY_MANIFEST_SHA256)

    def test_without_an_attestation_the_response_hash_is_checked(self, worker, executor, repo):
        assert _run(worker, executor, GOOD, None, attestation=None) is True
        assert repo.complete_job_with_held_output.call_args.kwargs['files_manifest_sha256'] == GOOD_MANIFEST


class TestRejectedFiles:
    def test_tampered_bytes_fail_the_job_and_store_nothing(self, worker, executor, repo, atl, store_dir, caplog):
        tampered = json.loads(json.dumps(GOOD))
        tampered['files'][0]['content_b64'] = base64.b64encode(TABLE + b'x').decode()

        assert _run(worker, executor, tampered, _receipt(files_manifest_sha256=GOOD_MANIFEST)) is False

        _assert_failed_with(repo, 'Output files rejected: table.csv: size does not match')
        assert _stored(store_dir) == []
        atl.submit_la_entry.assert_called_once_with(job_id=JOB_ID, error_class='failed', error_detail='')
        _assert_never_logged(caplog)

    def test_a_manifest_hash_differing_from_the_response_fails(self, worker, executor, repo, store_dir):
        files = dict(GOOD, files_manifest_sha256='0' * 64)

        assert _run(worker, executor, files, _receipt(files_manifest_sha256=GOOD_MANIFEST)) is False

        _assert_failed_with(repo, 'Output files rejected: the files manifest hash does not match the enclave response')
        assert _stored(store_dir) == []

    def test_a_manifest_hash_differing_from_the_attestation_fails(self, worker, executor, repo, store_dir):
        assert _run(worker, executor, GOOD, _receipt(files_manifest_sha256='1' * 64)) is False

        _assert_failed_with(repo, 'Output files rejected: the files manifest hash does not match the attestation')
        assert _stored(store_dir) == []

    def test_an_attestation_without_the_manifest_hash_fails(self, worker, executor, repo, store_dir):
        assert _run(worker, executor, GOOD, _receipt()) is False

        _assert_failed_with(repo, 'Output files rejected: the attestation does not carry a files manifest hash')
        assert _stored(store_dir) == []

    def test_an_unverifiable_attestation_fails(self, worker, executor, repo):
        assert _run(worker, executor, GOOD, json.dumps({'valid': False, 'error': 'boom'})) is False
        _assert_failed_with(repo, 'Output files rejected: the attestation does not carry')

    def test_files_without_a_configured_store_fail_closed(self, worker, executor, repo):
        worker._output_file_store = None

        assert _run(worker, executor, GOOD, _receipt(files_manifest_sha256=GOOD_MANIFEST)) is False

        _assert_failed_with(repo, 'Output file store is not configured')
        assert repo.complete_job_with_held_output.call_args.kwargs['content'] == 'Output file store is not configured'

    def test_a_store_failure_holds_the_error(self, worker, executor, repo, caplog):
        worker._output_file_store = MagicMock()
        worker._output_file_store.put.side_effect = OSError('bucket unreachable')

        assert _run(worker, executor, GOOD, _receipt(files_manifest_sha256=GOOD_MANIFEST)) is False

        call_ = repo.complete_job_with_held_output.call_args
        assert (call_.kwargs['kind'], call_.kwargs['content']) == ('error', 'bucket unreachable')
        _assert_never_logged(caplog)


class TestVerificationReceipt:
    def test_nitro_receipt_carries_the_user_data_manifest_hash(self, worker):
        vr = SimpleNamespace(
            valid=True, syntax_valid=True, certificate_chain_valid=True, aws_signature_valid=True,
            pcr_verified=True, output_verified=True, pcr0='aa', pcr1='bb', pcr2='cc',
            output_hash='ab' * 32, module_id='i-0abc', timestamp=None, error=None, timing={},
            user_data={'output_hash': 'ab' * 32, 'files_manifest_sha256': GOOD_MANIFEST},
        )
        with patch('workers.executor.worker.verify_attestation', return_value=vr):
            receipt = worker._verify_attestation(JOB_ID, ATTESTATION, MagicMock(), expected_output='out')
        assert json.loads(receipt)['files_manifest_sha256'] == GOOD_MANIFEST

    def test_an_end_to_end_nitro_run_checks_the_user_data(self, worker, executor, repo):
        vr = SimpleNamespace(
            valid=True, syntax_valid=True, certificate_chain_valid=True, aws_signature_valid=True,
            pcr_verified=True, output_verified=True, pcr0='aa', pcr1='bb', pcr2='cc',
            output_hash=_sha256(OUTPUT.encode()), module_id='i-0abc', timestamp=None, error=None, timing={},
            user_data={'output_hash': _sha256(OUTPUT.encode()), 'files_manifest_sha256': '2' * 64},
        )
        executor.execute.return_value = ExecutionResult(
            job_id=JOB_ID, status=JobStatus.SUCCESS, execution_time=1.0, output=OUTPUT,
            attestation=ATTESTATION, output_files=GOOD,
        )
        with patch('workers.executor.worker.verify_attestation', return_value=vr), \
                patch.object(ExecutorWorker, '_submit_to_atl', return_value=None):
            assert worker.process_job(_job()) is False
        _assert_failed_with(repo, 'Output files rejected: the files manifest hash does not match the attestation')

    def test_tdx_receipt_carries_the_bound_manifest_hash(self, worker):
        attestation = {'attestation': {'backend': 'tdx', 'attestation_document': 'ZG9j'}}
        tdx_result = TdxVerificationResult(
            valid=True, syntax_valid=True, certificate_chain_valid=True, signature_valid=True,
            pcr_verified=True, output_verified=True, measurements={'mrtd': 'aa'}, output_hash=_sha256(b'out'),
            files_manifest_sha256=GOOD_MANIFEST,
        )
        with patch('workers.executor.worker.verify_tdx_attestation', return_value=tdx_result):
            receipt = worker._verify_attestation(JOB_ID, attestation, MagicMock(), expected_output='out')
        assert json.loads(receipt)['files_manifest_sha256'] == GOOD_MANIFEST


class TestEnclaveClients:
    RESPONSE = {'success': True, 'output': OUTPUT, 'attestation': {'attestation': {}}, **GOOD}

    def test_nitro_client_returns_the_files(self):
        client = EnclaveClient(enclave_cid=16)
        with patch.object(client, '_send_to_enclave', return_value=json.loads(json.dumps(self.RESPONSE))):
            assert client.send_encrypted_data_to_enclave('session', 'zip')[3] == GOOD
            assert client.send_encrypted_data_with_db_fetch('session', 'zip', 'creds', 'SELECT 1')[3] == GOOD

    def test_tdx_client_returns_the_files(self):
        client = EnclaveClientTDX(host='127.0.0.1', port=1)
        with patch.object(client, '_send_to_td', return_value=json.loads(json.dumps(self.RESPONSE))):
            assert client.send_encrypted_data_to_enclave('session', 'zip')[3] == GOOD

    def test_a_response_without_files_returns_none(self):
        client = EnclaveClient(enclave_cid=16)
        response = {'success': True, 'output': OUTPUT, 'attestation': {'attestation': {}}}
        with patch.object(client, '_send_to_enclave', return_value=response):
            assert client.send_encrypted_data_to_enclave('session', 'zip') == (True, OUTPUT, {'attestation': {}}, None)

    def test_files_without_a_manifest_hash_are_passed_on_for_rejection(self):
        client = EnclaveClient(enclave_cid=16)
        response = {'success': True, 'output': OUTPUT, 'attestation': None, 'files': []}
        with patch.object(client, '_send_to_enclave', return_value=response):
            assert client.send_encrypted_data_to_enclave('session', 'zip')[3] == {
                'files': [], 'files_manifest_sha256': None,
            }


class TestExecutorResult:
    @pytest.fixture
    def secure_executor(self):
        executor = SecureExecutor.__new__(SecureExecutor)
        executor._active_jobs = {}
        executor._enclave_client = MagicMock(enclave_cid=1)
        executor._log = MagicMock()
        return executor

    def test_files_travel_with_a_success(self, secure_executor):
        result = secure_executor._create_result(JOB_ID, True, OUTPUT, 1.0, ATTESTATION, {}, GOOD)
        assert result.output_files == GOOD
        assert 'output_files' not in result.to_dict()

    def test_a_failure_carries_no_files(self, secure_executor):
        assert secure_executor._create_result(JOB_ID, False, 'boom', 1.0, None, {}, GOOD).output_files is None

    def test_the_enclave_step_returns_the_files(self, secure_executor):
        secure_executor._enclave_client.send_encrypted_data_to_enclave.return_value = (True, OUTPUT, None, GOOD)
        assert secure_executor._step_execute_in_enclave(JOB_ID, 'session', 'zip', None)[4] == GOOD
