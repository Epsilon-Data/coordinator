"""Project routing and the project access check in SecureExecutor + ExecutorWorker (unit, no database).

Right after build validation the executor records the job's project, reads the
submitter's Keycloak sub and asks the Epsilon API whether the submitter may analyse
the project. A refusal there is REJECTED and a failure is FAILED, both with release
status not_required, and neither may touch the enclave, the data middleware, a proxy
or the ATL. The proxy-mode guard stops a job whose middleware answer names another
project before the proxy is asked for data.
"""
import logging
from unittest.mock import MagicMock, call, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from shared.db import ProjectChangedError
from workers.executor.clients import EnclaveClientLocal
from workers.executor.exceptions import ProjectAccessError
from workers.executor.executor import SecureExecutor
from workers.executor.interfaces import MiddlewareResponse
from workers.executor.worker import ExecutorWorker

JOB_ID = 'JOB-PA-1'
PROJECT_ID = 'eba92dec-17a1-4a47-816e-77087ceb9531'
OTHER_PROJECT_ID = 'a7702e2d-69dd-4543-b3b6-a6df21d287c9'
SUBMITTER_SUB = '5d0b0a8e-3c1f-4b7a-9e2d-6f4a1c8b2e90'
ARCHETYPE_ID = 'KYZ0RWPE6zwE'

BUILD_YML = (
    'analysis:\n'
    '  script_file: main.py\n'
    'datasets:\n'
    '  - dataset_id: {dataset_id}\n'
    f'    archetype_id: {ARCHETYPE_ID}\n'
)


def _job(**overrides):
    job = {
        'job_id': JOB_ID,
        'workspace_id': 'ws-001',
        'user_id': '00000000-0000-0000-0000-0000000000a1',
        # A researcher nonce keeps the job compliant, so Step 4b submits the ATL Commitment
        'researcher_nonce': '11' * 16,
        'commit_sha': 'abc123',
        'github_repo': 'alice/analysis',
        'github_branch': 'main',
        'commit_message': 'run',
        'commit_author': 'alice',
        'created_at': '2026-10-16T00:00:00',
    }
    job.update(overrides)
    return job


@pytest.fixture
def storage(tmp_path):
    build = tmp_path / 'repositories' / JOB_ID / 'build'
    (build / 'generated').mkdir(parents=True)
    (build / 'build.yml').write_text(BUILD_YML.format(dataset_id=PROJECT_ID))
    (build / 'main.py').write_text('print("hello")')
    return tmp_path


@pytest.fixture
def repo():
    """The one shared job_repository, as the worker and the executor use it in production."""
    mock = MagicMock()
    mock.get_submitter_sub.return_value = SUBMITTER_SUB
    with patch('workers.executor.worker.job_repository', mock), \
            patch('workers.executor.executor.job_repository', mock):
        yield mock


@pytest.fixture(autouse=True)
def job_logs():
    with patch('shared.job_logger.job_repository') as mock:
        mock.add_detailed_log.return_value = 'log-id'
        yield mock


@pytest.fixture
def enclave():
    client = MagicMock()
    client.get_public_key.return_value = ('pubkey-pem', 'session_123')
    client.encrypt_zip_data.return_value = 'encrypted_zip_base64'
    client.send_encrypted_data_to_enclave.return_value = (True, 'count=7\n', None)
    client.health_check.return_value = True
    client.enclave_cid = 16
    return client


@pytest.fixture
def middleware():
    client = MagicMock()
    client.fetch_encrypted_csv.return_value = MiddlewareResponse(
        success=True, encrypted_csv='ZW5jcnlwdGVkLWNzdg==', mode='legacy',
    )
    return client


@pytest.fixture
def proxy():
    return MagicMock()


@pytest.fixture
def atl():
    """ATL online, with a real key so Step 4b can sign the JAC."""
    client = MagicMock()
    client._private_key = Ed25519PrivateKey.generate()
    client.fetch_sth.return_value = {'tree_size': 1000, 'root_hash': b'\x01' * 32, 'timestamp': 1700000000}
    client.derive_nonce.return_value = b'\xAA' * 32
    client.sign_and_submit_commitment.return_value = (
        b'\xCC' * 32, {'leaf_index': 7, 'tree_size': 1001, 'root_hash': b'\x02' * 32},
    )
    return client


@pytest.fixture
def access():
    client = MagicMock()
    client.check_project_access.return_value = True
    return client


@pytest.fixture
def settings():
    s = MagicMock()
    s.middleware.use_direct_db = False
    s.project_access.check = 'enforce'
    return s


@pytest.fixture
def make_worker(storage, enclave, middleware, proxy, atl, access, settings, repo):
    def make(enclave_client=enclave, access_client=access):
        with patch('workers.executor.executor.ZipService') as zip_service:
            zip_service.return_value.zip_directory.return_value = MagicMock(
                data=b'zip', files_count=1, compressed_size=3,
            )
            executor = SecureExecutor(
                enclave_client=enclave_client, settings=settings, middleware_client=middleware,
                proxy_client=proxy, access_client=access_client, atl_client=atl,
            )
        # Bypass ExecutorWorker.__init__: it validates the environment and touches the database.
        # The ATL client is wired into both, as __init__ does.
        worker = ExecutorWorker.__new__(ExecutorWorker)
        worker._settings = MagicMock()
        worker._settings.storage.shared_storage_path = str(storage)
        worker._executor = executor
        worker._atl_client = atl
        return worker
    return make


@pytest.fixture
def worker(make_worker):
    return make_worker()


RESEARCHER_ACCESS_MESSAGE = (
    f'Access to project {PROJECT_ID} could not be checked right now; run the job again later.'
)


def _assert_nothing_contacted(enclave, middleware, proxy, atl):
    # The worker's readiness probe is the only enclave call
    assert enclave.method_calls == [call.health_check()]
    assert middleware.method_calls == []
    assert proxy.method_calls == []
    atl.fetch_sth.assert_not_called()
    atl.sign_and_submit_commitment.assert_not_called()
    atl.submit_ha_entry.assert_not_called()


class TestOrder:
    def test_project_is_recorded_and_access_checked_before_anything_is_contacted(
        self, worker, repo, access, enclave, middleware, atl,
    ):
        order = []
        repo.record_job_project.side_effect = lambda *args: order.append('record')
        repo.get_submitter_sub.side_effect = lambda *args: order.append('submitter') or SUBMITTER_SUB
        access.check_project_access.side_effect = lambda *args: order.append('access') or True
        enclave.get_public_key.side_effect = lambda *args: order.append('enclave') or ('pubkey-pem', 'session_123')
        response = middleware.fetch_encrypted_csv.return_value
        middleware.fetch_encrypted_csv.side_effect = lambda *args, **kwargs: order.append('middleware') or response
        sth = atl.fetch_sth.return_value
        atl.fetch_sth.side_effect = lambda *args: order.append('atl_sth') or sth
        commitment = atl.sign_and_submit_commitment.return_value
        atl.sign_and_submit_commitment.side_effect = lambda *args, **kwargs: order.append('atl_commitment') or commitment

        assert worker.process_job(_job()) is True

        # Positive control: the ATL steps that refused jobs must never reach do run here, after the check
        assert order == ['record', 'submitter', 'access', 'enclave', 'middleware', 'atl_sth', 'atl_commitment']
        atl.fetch_sth.assert_called_once()
        atl.sign_and_submit_commitment.assert_called_once()
        repo.record_job_project.assert_called_once_with(JOB_ID, PROJECT_ID, ARCHETYPE_ID)
        repo.get_submitter_sub.assert_called_once_with(JOB_ID)
        access.check_project_access.assert_called_once_with(PROJECT_ID, SUBMITTER_SUB)
        assert middleware.fetch_encrypted_csv.call_args.args[0].dataset_id == PROJECT_ID
        assert repo.complete_job_with_held_output.call_args.kwargs['kind'] == 'result'

    def test_the_project_is_recorded_in_lower_case(self, worker, storage, repo, access):
        (storage / 'repositories' / JOB_ID / 'build' / 'build.yml').write_text(
            BUILD_YML.format(dataset_id=PROJECT_ID.upper())
        )

        assert worker.process_job(_job()) is True

        repo.record_job_project.assert_called_once_with(JOB_ID, PROJECT_ID, ARCHETYPE_ID)
        access.check_project_access.assert_called_once_with(PROJECT_ID, SUBMITTER_SUB)

    def test_the_submitter_sub_is_sent_in_lower_case(self, worker, repo, access):
        repo.get_submitter_sub.return_value = SUBMITTER_SUB.upper()

        assert worker.process_job(_job()) is True

        access.check_project_access.assert_called_once_with(PROJECT_ID, SUBMITTER_SUB)


class TestRejected:
    """Refusals: REJECTED, release_status not_required, the reason shown as is."""

    def _assert_rejected(self, repo, atl, message):
        call_ = repo.update_job_status.call_args
        assert call_.kwargs['status'] == 'rejected'
        assert call_.kwargs['release_status'] == 'not_required'
        assert message in call_.kwargs['error_message']
        repo.complete_job_with_held_output.assert_not_called()
        # Rejections, like build validation, write nothing to the public log
        atl.submit_la_entry.assert_not_called()

    def test_submitter_not_approved_for_the_project(self, worker, repo, access, enclave, middleware, proxy, atl):
        access.check_project_access.return_value = False

        assert worker.process_job(_job()) is False

        self._assert_rejected(
            repo, atl, f'You are not approved to analyse project {PROJECT_ID}; request access and run the job again.',
        )
        repo.record_job_project.assert_called_once_with(JOB_ID, PROJECT_ID, ARCHETYPE_ID)
        _assert_nothing_contacted(enclave, middleware, proxy, atl)

    @pytest.mark.parametrize('external_id', [None, '', 'github:4217', 'alice', SUBMITTER_SUB + '0'])
    def test_submitter_without_a_platform_identity(
        self, external_id, worker, repo, access, enclave, middleware, proxy, atl,
    ):
        repo.get_submitter_sub.return_value = external_id

        assert worker.process_job(_job()) is False

        self._assert_rejected(
            repo, atl, 'Your account is not linked to a platform identity; sign in to the Research Workspace again',
        )
        access.check_project_access.assert_not_called()
        _assert_nothing_contacted(enclave, middleware, proxy, atl)

    @pytest.mark.parametrize('build_yml, message', [
        ('analysis:\n  script_file: main.py\ndatasets: []\n', 'lists none'),
        ('analysis:\n  script_file: main.py\n', 'lists none'),
        (BUILD_YML.format(dataset_id=PROJECT_ID) + f'  - dataset_id: {OTHER_PROJECT_ID}\n    archetype_id: a2\n',
         'lists 2'),
        (BUILD_YML.format(dataset_id='ds-001'), "'dataset_id' must be the platform project id"),
        (BUILD_YML.format(dataset_id=PROJECT_ID).replace(ARCHETYPE_ID, 'a' * 256), "'archetype_id' must be text"),
    ], ids=['empty-list', 'no-datasets-key', 'two-datasets', 'not-a-uuid', 'long-archetype'])
    def test_build_validation(self, build_yml, message, worker, storage, repo, access, enclave, middleware, proxy, atl):
        (storage / 'repositories' / JOB_ID / 'build' / 'build.yml').write_text(build_yml)

        assert worker.process_job(_job()) is False

        self._assert_rejected(repo, atl, 'Build validation failed: ')
        assert message in repo.update_job_status.call_args.kwargs['error_message']
        repo.record_job_project.assert_not_called()
        repo.get_submitter_sub.assert_not_called()
        access.check_project_access.assert_not_called()
        _assert_nothing_contacted(enclave, middleware, proxy, atl)

    def test_a_rerun_that_is_refused_keeps_the_held_output(
        self, worker, repo, access, enclave, middleware, proxy, atl,
    ):
        access.check_project_access.return_value = False

        assert worker.process_job(_job(release_status='pending')) is False

        repo.update_job_status.assert_called_with(job_id=JOB_ID, status='rejected')
        repo.complete_job_with_held_output.assert_not_called()
        _assert_nothing_contacted(enclave, middleware, proxy, atl)


class TestFailedBeforeRealData:
    """No decision could be made: FAILED, release_status not_required, the job can be re-run."""

    def _assert_failed(self, repo, atl, message):
        call_ = repo.update_job_status.call_args
        assert call_.kwargs['status'] == 'failed'
        assert call_.kwargs['release_status'] == 'not_required'
        assert message in call_.kwargs['error_message']
        repo.complete_job_with_held_output.assert_not_called()
        atl.submit_la_entry.assert_called_once_with(job_id=JOB_ID, error_class='failed', error_detail='')

    @pytest.mark.parametrize('reason', [
        'the Epsilon API answered HTTP 503',
        'the Epsilon API is unreachable (ConnectionError)',
        'the Epsilon API did not answer within 10s',
        'the Epsilon API answered with invalid JSON',
        'the check is not configured (EPSILON_API_URL and EPSILON_COORDINATOR_CLIENT_SECRET are required)',
    ])
    def test_access_check_error(self, reason, worker, repo, access, enclave, middleware, proxy, atl, caplog, job_logs):
        access.check_project_access.side_effect = ProjectAccessError(reason)

        with caplog.at_level(logging.DEBUG):
            assert worker.process_job(_job()) is False

        self._assert_failed(repo, atl, RESEARCHER_ACCESS_MESSAGE)
        # The reason names configuration and upstream answers: operator log only, no traceback
        assert reason not in repo.update_job_status.call_args.kwargs['error_message']
        self._assert_reason_logged_for_operators_only(caplog, job_logs, reason)
        _assert_nothing_contacted(enclave, middleware, proxy, atl)

    def test_no_access_client(self, make_worker, repo, enclave, middleware, proxy, atl, caplog, job_logs):
        worker = make_worker(access_client=None)

        with caplog.at_level(logging.DEBUG):
            assert worker.process_job(_job()) is False

        self._assert_failed(repo, atl, RESEARCHER_ACCESS_MESSAGE)
        self._assert_reason_logged_for_operators_only(caplog, job_logs, 'the check is not configured')
        _assert_nothing_contacted(enclave, middleware, proxy, atl)

    @staticmethod
    def _assert_reason_logged_for_operators_only(caplog, job_logs, reason):
        assert any(r.levelno == logging.WARNING and f'job={JOB_ID}' in r.getMessage() and reason in r.getMessage()
                   for r in caplog.records)
        assert not any(r.exc_info for r in caplog.records)
        # Nor in the job's own log, which keeps no traceback or chained cause for it
        saved = [str(c.kwargs) for c in job_logs.add_detailed_log.call_args_list]
        assert saved and not any(reason in entry for entry in saved)

    def test_project_changed_since_an_earlier_run(self, worker, repo, access, enclave, middleware, proxy, atl):
        repo.record_job_project.side_effect = ProjectChangedError('RG025')

        assert worker.process_job(_job()) is False

        self._assert_failed(repo, atl, "This job's project changed")
        assert f'now names project {PROJECT_ID} (archetype {ARCHETYPE_ID})' in repo.update_job_status.call_args.kwargs['error_message']
        repo.get_submitter_sub.assert_not_called()
        access.check_project_access.assert_not_called()
        _assert_nothing_contacted(enclave, middleware, proxy, atl)

    def test_project_could_not_be_recorded(self, worker, repo, access, enclave, middleware, proxy, atl):
        repo.record_job_project.side_effect = RuntimeError('server closed the connection unexpectedly')

        assert worker.process_job(_job()) is False

        self._assert_failed(
            repo, atl, f"The job's project {PROJECT_ID} could not be recorded (RuntimeError); run the job again.",
        )
        access.check_project_access.assert_not_called()
        _assert_nothing_contacted(enclave, middleware, proxy, atl)

    def test_submitter_could_not_be_read(self, worker, repo, access, enclave, middleware, proxy, atl):
        repo.get_submitter_sub.side_effect = RuntimeError('server closed the connection unexpectedly')

        assert worker.process_job(_job()) is False

        self._assert_failed(repo, atl, "The job's submitter could not be read (RuntimeError); run the job again.")
        access.check_project_access.assert_not_called()
        _assert_nothing_contacted(enclave, middleware, proxy, atl)


class TestProxyModeGuard:
    @staticmethod
    def _proxy_response(**proxy_info):
        return MiddlewareResponse(success=True, mode='proxy', proxy_info={
            'assigned_port': 10001, 'proxy_token': 't', 'session_id': 's', 'sql_query': 'SELECT age FROM patients',
            **proxy_info,
        })

    @pytest.mark.parametrize('echo', [
        {'dataset_id': OTHER_PROJECT_ID},
        {'datasetId': OTHER_PROJECT_ID},
        {'dataset_id': 'not-a-project-id'},
        {'dataset_id': 4217},
    ], ids=['snake-case', 'camel-case', 'not-a-uuid', 'not-text'])
    def test_an_echo_naming_another_project_fails_before_the_proxy_is_asked(
        self, echo, worker, repo, middleware, proxy, enclave, atl,
    ):
        middleware.fetch_encrypted_csv.return_value = self._proxy_response(**echo)
        echoed = str(next(iter(echo.values())))

        assert worker.process_job(_job()) is False

        assert proxy.method_calls == []
        enclave._send_to_enclave.assert_not_called()
        enclave.encrypt_zip_data.assert_not_called()
        enclave.send_encrypted_data_to_enclave.assert_not_called()
        atl.fetch_sth.assert_not_called()
        atl.sign_and_submit_commitment.assert_not_called()
        # The middleware was contacted, so the failure text is held like any other after it
        held = repo.complete_job_with_held_output.call_args
        assert (held.kwargs['status'], held.kwargs['kind']) == ('failed', 'error')
        assert f'a project other than {PROJECT_ID}, the project recorded for this job' in held.kwargs['content']
        assert echoed not in held.kwargs['content']

    @pytest.mark.parametrize('echo', [
        {},
        {'dataset_id': PROJECT_ID.upper(), 'archetype_id': ARCHETYPE_ID},
        {'datasetId': PROJECT_ID, 'archetypeId': ARCHETYPE_ID},
    ], ids=['no-echo', 'same-project-upper-case', 'same-project-camel-case'])
    def test_an_echo_of_the_recorded_project_reaches_the_proxy(self, echo, worker, middleware, proxy, enclave):
        middleware.fetch_encrypted_csv.return_value = self._proxy_response(**echo)
        enclave._send_to_enclave.return_value = {'attestation': {'attestation_document': 'ZG9j'}}
        proxy.fetch_encrypted_csv.return_value = MagicMock(encrypted_csv='ZW5j', metadata={})

        assert worker.process_job(_job()) is True

        proxy.fetch_encrypted_csv.assert_called_once()
        assert proxy.fetch_encrypted_csv.call_args.kwargs['request'].dataset_id.lower() == PROJECT_ID


class TestAccessCheckDisabled:
    def test_disabled_with_the_local_enclave_skips_the_check_but_records_the_project(
        self, make_worker, settings, repo, access, enclave,
    ):
        settings.project_access.check = 'disabled'
        local = MagicMock(spec=EnclaveClientLocal)
        local.get_public_key.return_value = ('pubkey-pem', 'local_session_1')
        local.encrypt_zip_data.return_value = 'encrypted_zip_base64'
        local.send_encrypted_data_to_enclave.return_value = (True, 'count=7\n', None)
        local.health_check.return_value = True
        worker = make_worker(enclave_client=local)

        assert worker.process_job(_job()) is True

        repo.record_job_project.assert_called_once_with(JOB_ID, PROJECT_ID, ARCHETYPE_ID)
        repo.get_submitter_sub.assert_not_called()
        access.check_project_access.assert_not_called()

    def test_disabled_is_ignored_without_the_local_enclave(
        self, make_worker, settings, repo, access, enclave, middleware, proxy, atl, caplog,
    ):
        settings.project_access.check = 'disabled'
        access.check_project_access.return_value = False
        with caplog.at_level(logging.WARNING):
            worker = make_worker()

        assert worker.process_job(_job()) is False

        assert any('PROJECT_ACCESS_CHECK=disabled ignored' in r.getMessage() for r in caplog.records)
        access.check_project_access.assert_called_once_with(PROJECT_ID, SUBMITTER_SUB)
        assert repo.update_job_status.call_args.kwargs['status'] == 'rejected'
        _assert_nothing_contacted(enclave, middleware, proxy, atl)
