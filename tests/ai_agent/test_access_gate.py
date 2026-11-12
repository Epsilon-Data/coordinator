"""Access check before the AI review executes a job's code (unit, no database, no network)."""
from unittest.mock import MagicMock, patch

import pytest

from workers.ai_agent.access_gate import ReviewAccess, ReviewAccessGate
from workers.executor.exceptions import ProjectAccessError

JOB_ID = 'JOB-AG-1'
PROJECT_ID = 'eba92dec-17a1-4a47-816e-77087ceb9531'
SUBMITTER_SUB = '5D0B0A8E-3C1F-4B7A-9E2D-6F4A1C8B2E90'


@pytest.fixture
def repo(tmp_path):
    build = tmp_path / 'build'
    (build / 'generated').mkdir(parents=True)
    (build / 'build.yml').write_text(
        'analysis:\n'
        '  script_file: main.py\n'
        'datasets:\n'
        f'  - dataset_id: {PROJECT_ID}\n'
        '    archetype_id: arch-001\n'
    )
    (build / 'main.py').write_text('print("hello")')
    return str(tmp_path)


@pytest.fixture
def submitter():
    with patch('workers.ai_agent.access_gate.job_repository') as repository:
        repository.get_submitter_sub.return_value = SUBMITTER_SUB
        yield repository


@pytest.fixture
def access_client():
    client = MagicMock()
    client.check_project_access.return_value = True
    return client


class TestDecide:
    def test_an_approved_submitter_is_allowed(self, repo, submitter, access_client):
        decision = ReviewAccessGate(access_client).decide(JOB_ID, repo)

        assert decision.allowed and decision.status == ReviewAccess.ALLOWED
        # The project named by build.yml and the submitter's canonical Keycloak sub
        access_client.check_project_access.assert_called_once_with(PROJECT_ID, SUBMITTER_SUB.lower())
        submitter.get_submitter_sub.assert_called_once_with(JOB_ID)

    def test_a_submitter_who_is_not_approved_is_refused(self, repo, submitter, access_client):
        access_client.check_project_access.return_value = False

        decision = ReviewAccessGate(access_client).decide(JOB_ID, repo)

        assert not decision.allowed and decision.status == ReviewAccess.REFUSED
        assert PROJECT_ID in decision.reason

    def test_a_check_that_could_not_be_made_is_undecided_and_not_allowed(self, repo, submitter, access_client):
        access_client.check_project_access.side_effect = ProjectAccessError('the Epsilon API answered HTTP 503')

        decision = ReviewAccessGate(access_client).decide(JOB_ID, repo)

        assert not decision.allowed and decision.status == ReviewAccess.UNDECIDED
        # The upstream answer is for operators' logs, not the stored reason
        assert '503' not in decision.reason

    @pytest.mark.parametrize('sub', [None, '', 'not-a-uuid'])
    def test_a_submitter_without_a_platform_identity_is_refused_unasked(self, sub, repo, submitter, access_client):
        submitter.get_submitter_sub.return_value = sub

        assert ReviewAccessGate(access_client).decide(JOB_ID, repo).status == ReviewAccess.REFUSED
        access_client.check_project_access.assert_not_called()

    def test_an_unreadable_submitter_is_undecided(self, repo, submitter, access_client):
        submitter.get_submitter_sub.side_effect = RuntimeError('connection lost')

        assert ReviewAccessGate(access_client).decide(JOB_ID, repo).status == ReviewAccess.UNDECIDED
        access_client.check_project_access.assert_not_called()

    def test_a_job_without_a_valid_build_is_refused(self, tmp_path, submitter, access_client):
        assert ReviewAccessGate(access_client).decide(JOB_ID, str(tmp_path)).status == ReviewAccess.REFUSED
        access_client.check_project_access.assert_not_called()

    def test_an_unconfigured_check_refuses_every_job(self, repo, submitter, access_client):
        decision = ReviewAccessGate(access_client, configured=False).decide(JOB_ID, repo)

        assert decision.status == ReviewAccess.REFUSED and 'not configured' in decision.reason
        access_client.check_project_access.assert_not_called()

    def test_a_disabled_check_allows_without_asking(self, tmp_path, submitter, access_client):
        decision = ReviewAccessGate(access_client, check_disabled=True).decide(JOB_ID, str(tmp_path))

        assert decision.allowed
        access_client.check_project_access.assert_not_called()


class TestFromEnvironment:
    @pytest.fixture(autouse=True)
    def clean_environment(self, monkeypatch):
        for name in ('PROJECT_ACCESS_CHECK', 'ENCLAVE_BACKEND', 'USE_LOCAL_ENCLAVE', 'EPSILON_API_URL',
                     'EPSILON_COORDINATOR_CLIENT_SECRET'):
            monkeypatch.delenv(name, raising=False)

    def test_disabled_is_honoured_only_with_the_local_enclave(self, monkeypatch, tmp_path, submitter):
        monkeypatch.setenv('PROJECT_ACCESS_CHECK', 'disabled')
        assert ReviewAccessGate.from_environment().decide(JOB_ID, str(tmp_path)).status == ReviewAccess.REFUSED

        monkeypatch.setenv('ENCLAVE_BACKEND', 'local')
        assert ReviewAccessGate.from_environment().decide(JOB_ID, str(tmp_path)).allowed

    def test_without_the_api_settings_no_job_is_allowed(self, repo, submitter):
        decision = ReviewAccessGate.from_environment().decide(JOB_ID, repo)

        assert decision.status == ReviewAccess.REFUSED and 'not configured' in decision.reason

    def test_with_the_api_settings_the_api_is_asked(self, monkeypatch, repo, submitter):
        monkeypatch.setenv('EPSILON_API_URL', 'https://api.example.test/api/v1/hub')
        monkeypatch.setenv('EPSILON_COORDINATOR_CLIENT_SECRET', 'client-secret')

        with patch('workers.ai_agent.access_gate.ProjectAccessClient') as client_class:
            client_class.return_value.check_project_access.return_value = True
            decision = ReviewAccessGate.from_environment().decide(JOB_ID, repo)

        assert decision.allowed
        client_class.return_value.check_project_access.assert_called_once_with(PROJECT_ID, SUBMITTER_SUB.lower())
