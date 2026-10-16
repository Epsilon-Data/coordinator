"""ProjectAccessClient: the coordinator client's call to the Epsilon API (HTTP mocked)."""
import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from workers.executor.access_client import API_ATTEMPTS, API_TIMEOUT_SECONDS, ProjectAccessClient
from workers.executor.exceptions import ProjectAccessError
from workers.executor.settings import ProjectAccessConfig

API_URL = 'https://api.example.invalid/hub'
PROJECT_ID = 'eba92dec-17a1-4a47-816e-77087ceb9531'
USER_SUB = '5d0b0a8e-3c1f-4b7a-9e2d-6f4a1c8b2e90'
SECRET = 'SECRET-coordinator-client-7f3a'
TOKEN = 'TOKEN-eyJhbGciOiJSUzI1NiJ9-first'
TOKEN_2 = 'TOKEN-eyJhbGciOiJSUzI1NiJ9-second'


def _response(status_code=200, body=None, invalid_json=False):
    response = MagicMock()
    response.status_code = status_code
    if invalid_json:
        response.json.side_effect = ValueError('Expecting value')
    else:
        response.json.return_value = body
    return response


def _token(token=TOKEN, expires_in=300):
    return _response(body={'access_token': token, 'expires_in': expires_in, 'refresh_expires_in': 0,
                           'token_type': 'Bearer', 'not-before-policy': 0, 'scope': 'epsilon.coordinator'})


def _decision(approved=True):
    return _response(body={'isApproved': approved})


@pytest.fixture
def config():
    return ProjectAccessConfig(api_url=API_URL, client_id='coordinator-client', client_secret=SECRET, check='enforce')


@pytest.fixture
def client(config):
    return ProjectAccessClient(config)


@pytest.fixture
def http():
    with patch('workers.executor.access_client.requests.post') as post, \
            patch('workers.executor.access_client.requests.get') as get:
        post.return_value = _token()
        get.return_value = _decision(True)
        yield MagicMock(post=post, get=get)


@pytest.fixture(autouse=True)
def sleep():
    with patch('workers.executor.access_client.time.sleep') as sleep:
        yield sleep


@pytest.fixture
def clock():
    with patch('workers.executor.access_client.time.monotonic', return_value=1000.0) as monotonic:
        yield monotonic


class TestDecision:
    @pytest.mark.parametrize('approved', [True, False])
    def test_returns_the_api_decision(self, client, http, approved):
        http.get.return_value = _decision(approved)

        assert client.check_project_access(PROJECT_ID, USER_SUB) is approved

    def test_requests(self, client, http):
        client.check_project_access(PROJECT_ID, USER_SUB)

        http.post.assert_called_once_with(
            f'{API_URL}/coordinator/auth',
            timeout=API_TIMEOUT_SECONDS,
            json={'clientId': 'coordinator-client', 'clientSecret': SECRET},
        )
        http.get.assert_called_once_with(
            f'{API_URL}/coordinator/{PROJECT_ID}',
            timeout=API_TIMEOUT_SECONDS,
            params={'userId': USER_SUB},
            headers={'Authorization': f'Bearer {TOKEN}'},
        )

    @pytest.mark.parametrize('status_code', [400, 403, 404, 500, 502, 503, 524])
    def test_any_other_status_is_an_error(self, client, http, status_code):
        http.get.return_value = _response(status_code, {'message': 'Database is temporarily unavailable'})

        with pytest.raises(ProjectAccessError, match=f'answered HTTP {status_code}'):
            client.check_project_access(PROJECT_ID, USER_SUB)

    @pytest.mark.parametrize('response', [
        _response(invalid_json=True),
        _response(body=None),
        _response(body=[]),
        _response(body={}),
        _response(body={'isApproved': 'true'}),
        _response(body={'isApproved': 1}),
        _response(body={'isApproved': None}),
    ], ids=['invalid-json', 'null', 'list', 'no-decision', 'string', 'number', 'null-decision'])
    def test_a_malformed_answer_is_an_error_never_an_approval(self, client, http, response):
        http.get.return_value = response

        with pytest.raises(ProjectAccessError):
            client.check_project_access(PROJECT_ID, USER_SUB)

    @pytest.mark.parametrize('config_fields', [
        {'api_url': ''},
        {'client_secret': ''},
        {'client_id': ''},
    ], ids=['no-url', 'no-secret', 'no-client-id'])
    def test_missing_configuration_is_an_error_and_calls_nothing(self, config, http, config_fields):
        for name, value in config_fields.items():
            setattr(config, name, value)

        with pytest.raises(ProjectAccessError, match='not configured'):
            ProjectAccessClient(config).check_project_access(PROJECT_ID, USER_SUB)

        http.post.assert_not_called()
        http.get.assert_not_called()


class TestToken:
    def test_is_cached_between_checks(self, client, http, clock):
        client.check_project_access(PROJECT_ID, USER_SUB)
        clock.return_value = 1000.0 + 300 - 31
        client.check_project_access(PROJECT_ID, USER_SUB)

        assert http.post.call_count == 1
        assert http.get.call_count == 2

    def test_is_renewed_30_seconds_before_it_expires(self, client, http, clock):
        client.check_project_access(PROJECT_ID, USER_SUB)
        http.post.return_value = _token(TOKEN_2)
        clock.return_value = 1000.0 + 300 - 29

        client.check_project_access(PROJECT_ID, USER_SUB)

        assert http.post.call_count == 2
        assert http.get.call_args.kwargs['headers'] == {'Authorization': f'Bearer {TOKEN_2}'}

    @pytest.mark.parametrize('expires_in', [30, 0, None, 'soon'])
    def test_a_short_or_missing_lifetime_is_not_cached(self, client, http, clock, expires_in):
        http.post.return_value = _token(expires_in=expires_in)

        client.check_project_access(PROJECT_ID, USER_SUB)
        client.check_project_access(PROJECT_ID, USER_SUB)

        assert http.post.call_count == 2

    @pytest.mark.parametrize('response', [
        _response(401, {'message': 'Invalid user credentials'}),
        _response(500, {'message': 'Authorisation service is currently unavailable'}),
    ], ids=['401', '500'])
    def test_a_refused_token_request_is_an_error(self, client, http, response):
        http.post.return_value = response

        with pytest.raises(ProjectAccessError, match=f'could not get a token \\(HTTP {response.status_code}\\)'):
            client.check_project_access(PROJECT_ID, USER_SUB)
        http.get.assert_not_called()

    @pytest.mark.parametrize('response', [
        _response(invalid_json=True), _response(body={'expires_in': 300}), _response(body={'access_token': ''}),
        _response(body=['token']),
    ], ids=['invalid-json', 'no-token', 'empty-token', 'list'])
    def test_a_token_answer_without_a_token_is_an_error(self, client, http, response):
        http.post.return_value = response

        with pytest.raises(ProjectAccessError, match='without an access token'):
            client.check_project_access(PROJECT_ID, USER_SUB)
        http.get.assert_not_called()


class TestUnauthorizedRetry:
    def test_a_401_is_retried_once_with_a_new_token(self, client, http, clock):
        http.post.side_effect = [_token(TOKEN), _token(TOKEN_2)]
        http.get.side_effect = [_response(401, {'message': 'Unauthorized'}), _decision(False)]

        assert client.check_project_access(PROJECT_ID, USER_SUB) is False

        assert http.post.call_count == 2
        assert [c.kwargs['headers'] for c in http.get.call_args_list] == [
            {'Authorization': f'Bearer {TOKEN}'}, {'Authorization': f'Bearer {TOKEN_2}'},
        ]

    def test_a_cached_token_that_is_refused_is_replaced(self, client, http, clock):
        client.check_project_access(PROJECT_ID, USER_SUB)
        http.post.return_value = _token(TOKEN_2)
        http.get.side_effect = [_response(401), _decision(True)]

        assert client.check_project_access(PROJECT_ID, USER_SUB) is True

        assert http.post.call_count == 2
        assert http.get.call_args.kwargs['headers'] == {'Authorization': f'Bearer {TOKEN_2}'}

    def test_a_second_401_is_an_error(self, client, http, clock):
        http.get.return_value = _response(401)

        with pytest.raises(ProjectAccessError, match='answered HTTP 401'):
            client.check_project_access(PROJECT_ID, USER_SUB)

        assert http.post.call_count == 2
        assert http.get.call_count == 2


class TestNetworkErrors:
    @pytest.mark.parametrize('failing', ['post', 'get'])
    def test_timeout(self, client, http, failing):
        getattr(http, failing).side_effect = requests.exceptions.ReadTimeout('read timed out')

        with pytest.raises(ProjectAccessError, match=f'did not answer within {API_TIMEOUT_SECONDS}s'):
            client.check_project_access(PROJECT_ID, USER_SUB)

    @pytest.mark.parametrize('error', [
        requests.exceptions.ConnectionError('Name or service not known'),
        requests.exceptions.SSLError('certificate verify failed'),
        requests.exceptions.TooManyRedirects('too many redirects'),
    ])
    @pytest.mark.parametrize('failing', ['post', 'get'])
    def test_unreachable(self, client, http, failing, error):
        getattr(http, failing).side_effect = error

        with pytest.raises(ProjectAccessError, match=f'unreachable \\({type(error).__name__}\\)'):
            client.check_project_access(PROJECT_ID, USER_SUB)


class TestTransientRetry:
    @pytest.mark.parametrize('first', [
        requests.exceptions.ReadTimeout('read timed out'),
        requests.exceptions.ConnectionError('Connection reset by peer'),
        _response(503, {'message': 'Service Unavailable'}),
        _response(524),
    ], ids=['timeout', 'connection-reset', '503', '524'])
    @pytest.mark.parametrize('failing', ['post', 'get'])
    def test_a_transient_failure_is_retried_once(self, client, http, sleep, failing, first):
        success = _token() if failing == 'post' else _decision(True)
        getattr(http, failing).side_effect = [first, success]

        assert client.check_project_access(PROJECT_ID, USER_SUB) is True

        assert getattr(http, failing).call_count == 2
        sleep.assert_called_once()
        assert 0.5 <= sleep.call_args.args[0] <= 1.5

    @pytest.mark.parametrize('failing', ['post', 'get'])
    def test_a_persistent_transient_failure_is_an_error_after_the_retries(self, client, http, sleep, failing):
        getattr(http, failing).return_value = _response(503)

        with pytest.raises(ProjectAccessError, match='HTTP 503'):
            client.check_project_access(PROJECT_ID, USER_SUB)

        assert getattr(http, failing).call_count == API_ATTEMPTS
        assert sleep.call_count == API_ATTEMPTS - 1

    @pytest.mark.parametrize('response', [_response(403), _response(404), _response(500), _decision(False)],
                             ids=['403', '404', '500', 'not-approved'])
    def test_a_definite_answer_is_not_retried(self, client, http, sleep, response):
        http.get.return_value = response

        try:
            client.check_project_access(PROJECT_ID, USER_SUB)
        except ProjectAccessError:
            pass

        assert http.get.call_count == 1
        sleep.assert_not_called()

    def test_an_ssl_error_is_not_retried(self, client, http, sleep):
        http.post.side_effect = requests.exceptions.SSLError('certificate verify failed')

        with pytest.raises(ProjectAccessError, match='unreachable \\(SSLError\\)'):
            client.check_project_access(PROJECT_ID, USER_SUB)

        assert http.post.call_count == 1
        sleep.assert_not_called()


class TestSecretsNeverLogged:
    @pytest.mark.parametrize('scenario', [
        'approved', 'not-approved', 'decision-500', 'decision-401-twice', 'token-401', 'timeout', 'unreachable',
        'invalid-json',
    ])
    def test_secret_and_tokens_stay_out_of_logs_and_errors(self, client, http, clock, caplog, scenario):
        caplog.set_level(logging.DEBUG)
        http.post.side_effect = [_token(TOKEN), _token(TOKEN_2)]
        http.get.return_value = {
            'not-approved': _decision(False),
            'decision-500': _response(500, {'message': f'echo {TOKEN}'}),
            'decision-401-twice': _response(401),
            'invalid-json': _response(invalid_json=True),
        }.get(scenario, _decision(True))
        if scenario == 'token-401':
            http.post.side_effect = [_response(401, {'message': 'Invalid client secret'})]
        elif scenario == 'timeout':
            http.get.side_effect = requests.exceptions.ConnectTimeout(f'timeout for Bearer {TOKEN}')
        elif scenario == 'unreachable':
            http.post.side_effect = requests.exceptions.ConnectionError(f'connection refused ({SECRET})')

        try:
            client.check_project_access(PROJECT_ID, USER_SUB)
            message = ''
        except ProjectAccessError as e:
            message = str(e)

        logged = '\n'.join(f'{r.getMessage()} {r.exc_text or ""}' for r in caplog.records)
        for secret in (SECRET, TOKEN, TOKEN_2):
            assert secret not in message
            assert secret not in logged


class TestConfig:
    def test_reads_the_environment(self, monkeypatch):
        monkeypatch.setenv('EPSILON_API_URL', ' https://app.epsilon-data.org/hub/ ')
        monkeypatch.setenv('EPSILON_COORDINATOR_CLIENT_SECRET', SECRET)
        monkeypatch.delenv('EPSILON_COORDINATOR_CLIENT_ID', raising=False)
        monkeypatch.setenv('PROJECT_ACCESS_CHECK', ' Disabled ')

        config = ProjectAccessConfig()

        assert config.api_url == 'https://app.epsilon-data.org/hub'
        assert config.client_id == 'coordinator-client'
        assert config.check == 'disabled'
        assert config.is_configured
        assert SECRET not in repr(config)

    def test_enforced_by_default(self, monkeypatch):
        monkeypatch.delenv('PROJECT_ACCESS_CHECK', raising=False)
        monkeypatch.delenv('EPSILON_API_URL', raising=False)

        config = ProjectAccessConfig()

        assert config.check == 'enforce'
        assert not config.is_configured
