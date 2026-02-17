"""
Tests for workers/executor/factories.py
"""
import pytest
from unittest.mock import MagicMock, patch

from workers.executor.factories import EnclaveClientFactory, MiddlewareClientFactory, ExecutorFactory
from workers.executor.exceptions import ConfigurationError


class TestEnclaveClientFactory:
    """Test cases for EnclaveClientFactory class."""

    @pytest.fixture
    def mock_settings_local(self):
        """Create mock settings for local client."""
        settings = MagicMock()
        settings.enclave = MagicMock()
        settings.enclave.use_local_client = True
        return settings

    @pytest.fixture
    def mock_settings_nitro(self):
        """Create mock settings for Nitro client."""
        settings = MagicMock()
        settings.enclave = MagicMock()
        settings.enclave.use_local_client = False
        return settings

    def test_create_client_local(self, mock_settings_local):
        """Test creating local enclave client."""
        with patch('workers.executor.factories.EnclaveClientLocal') as MockLocal:
            mock_client = MagicMock()
            MockLocal.return_value = mock_client

            client = EnclaveClientFactory.create_client(mock_settings_local)

            MockLocal.assert_called_once()
            assert client is mock_client

    def test_create_client_nitro(self, mock_settings_nitro):
        """Test creating Nitro enclave client."""
        with patch('workers.executor.factories.EnclaveClient') as MockNitro:
            mock_client = MagicMock()
            MockNitro.return_value = mock_client

            client = EnclaveClientFactory.create_client(mock_settings_nitro)

            MockNitro.assert_called_once()
            assert client is mock_client

    def test_create_client_respects_local_setting(self, mock_settings_local):
        """Test use_local_client setting controls client type."""
        with patch('workers.executor.factories.EnclaveClientLocal') as MockLocal:
            mock_client = MagicMock()
            MockLocal.return_value = mock_client

            client = EnclaveClientFactory.create_client(mock_settings_local)

            MockLocal.assert_called_once()
            assert client is mock_client

    def test_create_client_uses_default_settings(self):
        """Test that default settings are used when none provided."""
        with patch('workers.executor.factories.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.enclave.use_local_client = True
            mock_get_settings.return_value = mock_settings

            with patch('workers.executor.factories.EnclaveClientLocal') as MockLocal:
                MockLocal.return_value = MagicMock()

                EnclaveClientFactory.create_client()

                mock_get_settings.assert_called_once()

    def test_create_client_raises_on_error(self, mock_settings_local):
        """Test that ConfigurationError is raised on failure."""
        with patch('workers.executor.factories.EnclaveClientLocal') as MockLocal:
            MockLocal.side_effect = Exception('Connection failed')

            with pytest.raises(ConfigurationError, match='Failed to create enclave client'):
                EnclaveClientFactory.create_client(mock_settings_local)


class TestMiddlewareClientFactory:
    """Test cases for MiddlewareClientFactory class."""

    @pytest.fixture
    def mock_settings_with_url(self):
        """Create mock settings with middleware URL."""
        settings = MagicMock()
        settings.middleware = MagicMock()
        settings.middleware.endpoint_url = 'http://localhost:8001'
        return settings

    @pytest.fixture
    def mock_settings_no_url(self):
        """Create mock settings without middleware URL."""
        settings = MagicMock()
        settings.middleware = MagicMock()
        settings.middleware.endpoint_url = None
        return settings

    def test_create_client_success(self, mock_settings_with_url):
        """Test creating middleware client with valid URL."""
        with patch('workers.executor.factories.MiddlewareClient') as MockClient:
            mock_client = MagicMock()
            MockClient.return_value = mock_client

            client = MiddlewareClientFactory.create_client(mock_settings_with_url)

            MockClient.assert_called_once_with(
                settings=mock_settings_with_url,
                endpoint_url='http://localhost:8001',
                aws_region=mock_settings_with_url.middleware.aws_region,
                mode=mock_settings_with_url.middleware.mode,
            )
            assert client is mock_client

    def test_create_client_no_url_raises(self, mock_settings_no_url):
        """Test that ValueError is raised when URL not configured."""
        with pytest.raises(ValueError, match='MIDDLEWARE_ENDPOINT_URL is required'):
            MiddlewareClientFactory.create_client(mock_settings_no_url)

    def test_create_client_empty_url_raises(self):
        """Test that empty URL raises error."""
        settings = MagicMock()
        settings.middleware = MagicMock()
        settings.middleware.endpoint_url = ''

        with pytest.raises(ValueError, match='MIDDLEWARE_ENDPOINT_URL is required'):
            MiddlewareClientFactory.create_client(settings)

    def test_create_client_uses_default_settings(self):
        """Test that default settings are used when none provided."""
        with patch('workers.executor.factories.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.middleware.endpoint_url = 'http://localhost:8001'
            mock_get_settings.return_value = mock_settings

            with patch('workers.executor.factories.MiddlewareClient') as MockClient:
                MockClient.return_value = MagicMock()

                MiddlewareClientFactory.create_client()

                mock_get_settings.assert_called_once()


class TestExecutorFactory:
    """Test cases for ExecutorFactory class."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.enclave = MagicMock()
        settings.enclave.use_local_client = True
        settings.middleware = MagicMock()
        settings.middleware.endpoint_url = 'http://localhost:8001'
        return settings

    @pytest.fixture
    def mock_enclave_client(self):
        """Create mock enclave client."""
        client = MagicMock()
        client.health_check.return_value = True
        return client

    @pytest.fixture
    def mock_middleware_client(self):
        """Create mock middleware client."""
        return MagicMock()

    def test_create_executor_with_all_deps(
        self, mock_settings, mock_enclave_client, mock_middleware_client
    ):
        """Test creating executor with all dependencies provided."""
        with patch('workers.executor.factories.SecureExecutor') as MockExecutor:
            mock_executor = MagicMock()
            MockExecutor.return_value = mock_executor

            executor = ExecutorFactory.create_executor(
                settings=mock_settings,
                enclave_client=mock_enclave_client,
                middleware_client=mock_middleware_client
            )

            MockExecutor.assert_called_once_with(
                enclave_client=mock_enclave_client,
                settings=mock_settings,
                middleware_client=mock_middleware_client
            )
            assert executor is mock_executor

    def test_create_executor_creates_enclave_client(self, mock_settings, mock_middleware_client):
        """Test that enclave client is created when not provided."""
        with patch('workers.executor.factories.EnclaveClientFactory') as MockEnclaveFactory:
            mock_enclave = MagicMock()
            MockEnclaveFactory.create_client.return_value = mock_enclave

            with patch('workers.executor.factories.SecureExecutor') as MockExecutor:
                MockExecutor.return_value = MagicMock()

                ExecutorFactory.create_executor(
                    settings=mock_settings,
                    middleware_client=mock_middleware_client
                )

                MockEnclaveFactory.create_client.assert_called_once_with(mock_settings)

    def test_create_executor_creates_middleware_client(self, mock_settings, mock_enclave_client):
        """Test that middleware client is created when not provided."""
        with patch('workers.executor.factories.MiddlewareClientFactory') as MockMiddlewareFactory:
            mock_middleware = MagicMock()
            MockMiddlewareFactory.create_client.return_value = mock_middleware

            with patch('workers.executor.factories.SecureExecutor') as MockExecutor:
                MockExecutor.return_value = MagicMock()

                ExecutorFactory.create_executor(
                    settings=mock_settings,
                    enclave_client=mock_enclave_client
                )

                MockMiddlewareFactory.create_client.assert_called_once_with(mock_settings)

    def test_create_executor_uses_default_settings(self):
        """Test that default settings are used when none provided."""
        with patch('workers.executor.factories.get_settings') as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.enclave.use_local_client = True
            mock_settings.middleware.endpoint_url = 'http://test:8001'
            mock_get_settings.return_value = mock_settings

            with patch('workers.executor.factories.EnclaveClientFactory') as MockEnclaveFactory:
                MockEnclaveFactory.create_client.return_value = MagicMock()

                with patch('workers.executor.factories.MiddlewareClientFactory') as MockMiddlewareFactory:
                    MockMiddlewareFactory.create_client.return_value = MagicMock()

                    with patch('workers.executor.factories.SecureExecutor') as MockExecutor:
                        MockExecutor.return_value = MagicMock()

                        ExecutorFactory.create_executor()

                        mock_get_settings.assert_called_once()

    def test_create_executor_raises_on_error(self, mock_settings):
        """Test that ConfigurationError is raised on failure."""
        with patch('workers.executor.factories.EnclaveClientFactory') as MockEnclaveFactory:
            MockEnclaveFactory.create_client.side_effect = Exception('Enclave failed')

            with pytest.raises(ConfigurationError, match='Failed to create executor'):
                ExecutorFactory.create_executor(settings=mock_settings)



