"""
Tests for workers/executor/clients.py - MiddlewareClient
"""
import pytest
from unittest.mock import MagicMock, patch

from workers.executor.clients import MiddlewareClient
from workers.executor.interfaces import MiddlewareRequest


class TestMiddlewareClient:
    """Test cases for MiddlewareClient class."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.middleware = MagicMock()
        settings.middleware.endpoint_url = 'http://localhost:8001'
        settings.middleware.timeout_seconds = 60
        return settings

    @pytest.fixture
    def middleware_client(self, mock_settings):
        """Create MiddlewareClient instance."""
        with patch('workers.executor.clients.get_settings', return_value=mock_settings):
            return MiddlewareClient(mock_settings, 'http://localhost:8001')

    @pytest.fixture
    def mock_request(self):
        """Create mock MiddlewareRequest."""
        return MiddlewareRequest(
            dataset_id='ds-001',
            archetype_id='arch-001',
            public_key='-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----',
            job_id='JOB-123',
            workspace_id='ws-001'
        )

    def test_client_initialization(self, mock_settings):
        """Test MiddlewareClient initializes correctly."""
        with patch('workers.executor.clients.get_settings', return_value=mock_settings):
            client = MiddlewareClient(mock_settings, 'http://localhost:8001/')

            # URL should have trailing slash removed
            assert client._endpoint_url == 'http://localhost:8001'
            assert client._timeout == 60

    @patch('workers.executor.clients.requests.post')
    def test_fetch_encrypted_csv_success(self, mock_post, middleware_client, mock_request):
        """Test successful CSV fetch."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'success': True,
            'csv': 'encrypted_csv_data_base64',
            'metadata': {'rows': 10}
        }
        mock_response.headers = {'X-Request-ID': 'req-123'}
        mock_post.return_value = mock_response

        result = middleware_client.fetch_encrypted_csv(mock_request)

        assert result.success is True
        assert result.encrypted_csv == 'encrypted_csv_data_base64'
        assert result.csv_metadata == {'rows': 10}
        assert result.request_id == 'req-123'

    @patch('workers.executor.clients.requests.post')
    def test_fetch_encrypted_csv_http_error(self, mock_post, middleware_client, mock_request):
        """Test handling of HTTP error responses."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.content = b'{"error": "Internal server error"}'
        mock_response.json.return_value = {'error': 'Internal server error'}
        mock_post.return_value = mock_response

        result = middleware_client.fetch_encrypted_csv(mock_request)

        assert result.success is False
        assert 'Internal server error' in result.error

    @patch('workers.executor.clients.requests.post')
    def test_fetch_encrypted_csv_timeout(self, mock_post, middleware_client, mock_request):
        """Test handling of timeout."""
        import requests
        mock_post.side_effect = requests.exceptions.Timeout()

        result = middleware_client.fetch_encrypted_csv(mock_request)

        assert result.success is False
        assert 'timeout' in result.error.lower()

    @patch('workers.executor.clients.requests.post')
    def test_fetch_encrypted_csv_connection_error(self, mock_post, middleware_client, mock_request):
        """Test handling of connection error."""
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        result = middleware_client.fetch_encrypted_csv(mock_request)

        assert result.success is False
        assert 'connect' in result.error.lower()

    @patch('workers.executor.clients.requests.post')
    def test_fetch_encrypted_csv_not_success(self, mock_post, middleware_client, mock_request):
        """Test handling when success=False in response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'success': False,
            'error': 'Dataset not found'
        }
        mock_post.return_value = mock_response

        result = middleware_client.fetch_encrypted_csv(mock_request)

        assert result.success is False
        assert 'Dataset not found' in result.error

    @patch('workers.executor.clients.requests.get')
    def test_health_check_success(self, mock_get, middleware_client):
        """Test successful health check."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = middleware_client.health_check()

        assert result is True
        mock_get.assert_called_once()

    @patch('workers.executor.clients.requests.get')
    def test_health_check_failure(self, mock_get, middleware_client):
        """Test failed health check."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_get.return_value = mock_response

        result = middleware_client.health_check()

        assert result is False

    @patch('workers.executor.clients.requests.get')
    def test_health_check_exception(self, mock_get, middleware_client):
        """Test health check with exception."""
        mock_get.side_effect = Exception("Network error")

        result = middleware_client.health_check()

        assert result is False

    def test_is_enabled_with_url(self, middleware_client):
        """Test is_enabled returns True when URL is set."""
        assert middleware_client.is_enabled is True

    def test_is_enabled_without_url(self):
        """Test is_enabled returns False when URL is not set."""
        mock_settings = MagicMock()
        mock_settings.middleware.endpoint_url = None
        mock_settings.middleware.timeout_seconds = 60

        with patch('workers.executor.clients.get_settings', return_value=mock_settings):
            client = MiddlewareClient(mock_settings, None)

            assert client.is_enabled is False

    @patch('workers.executor.clients.requests.post')
    def test_fetch_sends_correct_payload(self, mock_post, middleware_client, mock_request):
        """Test that correct payload is sent to middleware."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'success': True, 'csv': 'data'}
        mock_response.headers = {}
        mock_post.return_value = mock_response

        middleware_client.fetch_encrypted_csv(mock_request)

        call_args = mock_post.call_args
        assert call_args[0][0] == 'http://localhost:8001'

        payload = call_args[1]['json']
        assert payload['dataset_id'] == 'ds-001'
        assert payload['archetype_id'] == 'arch-001'
        assert 'public_key' in payload

    @patch('workers.executor.clients.requests.post')
    def test_fetch_handles_empty_csv(self, mock_post, middleware_client, mock_request):
        """Test handling of empty CSV in response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'success': True,
            'csv': '',
            'metadata': {}
        }
        mock_response.headers = {}
        mock_post.return_value = mock_response

        result = middleware_client.fetch_encrypted_csv(mock_request)

        assert result.success is True
        assert result.encrypted_csv == ''
