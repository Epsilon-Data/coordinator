"""
Tests for workers/executor/executor.py - SecureExecutor
"""
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from workers.executor.executor import SecureExecutor
from workers.executor.models import JobExecutionRequest, ExecutionResult, BuildConfig, DatasetConfig, JobStatus
from workers.executor.exceptions import BuildValidationError


class TestSecureExecutor:
    """Test cases for SecureExecutor class."""

    @pytest.fixture
    def mock_enclave_client(self):
        """Create mock enclave client."""
        client = MagicMock()
        client.get_public_key.return_value = ('-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----', 'session_123')
        client.encrypt_zip_data.return_value = 'encrypted_zip_base64'
        client.send_encrypted_data_to_enclave.return_value = (True, 'Script output')
        client.health_check.return_value = True
        client.enclave_cid = 999
        return client

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.middleware = MagicMock()
        settings.middleware.endpoint_url = 'http://localhost:8001'
        return settings

    @pytest.fixture
    def mock_middleware_client(self):
        """Create mock middleware client."""
        client = MagicMock()
        response = MagicMock()
        response.success = True
        response.encrypted_csv = 'encrypted_csv_base64'
        response.error = None
        client.fetch_encrypted_csv.return_value = response
        return client

    @pytest.fixture
    def mock_job_logger(self):
        """Create mock job logger."""
        with patch('workers.executor.executor.JobLogger') as MockLogger:
            logger = MagicMock()
            MockLogger.return_value = logger
            yield logger

    @pytest.fixture
    def temp_repo(self):
        """Create temporary repository with build structure."""
        temp_dir = tempfile.mkdtemp()
        repo_path = Path(temp_dir)

        # Create build folder structure
        build_path = repo_path / 'build'
        build_path.mkdir()
        (build_path / 'generated').mkdir()

        # Create valid build.yml
        build_yml = """
version: "1.0"
analysis:
  script_file: main.py
datasets:
  - dataset_id: ds-001
    archetype_id: arch-001
privacy:
  epsilon: 1.5
"""
        (build_path / 'build.yml').write_text(build_yml)
        (build_path / 'main.py').write_text('print("Hello")')

        yield str(repo_path)

        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def temp_repo_no_datasets(self):
        """Create temp repo without datasets."""
        temp_dir = tempfile.mkdtemp()
        repo_path = Path(temp_dir)

        build_path = repo_path / 'build'
        build_path.mkdir()
        (build_path / 'generated').mkdir()

        build_yml = """
version: "1.0"
analysis:
  script_file: main.py
datasets: []
"""
        (build_path / 'build.yml').write_text(build_yml)
        (build_path / 'main.py').write_text('print("No datasets")')

        yield str(repo_path)

        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def secure_executor(self, mock_enclave_client, mock_settings, mock_middleware_client, mock_job_logger):
        """Create SecureExecutor instance."""
        with patch('workers.executor.executor.ZipService') as MockZip:
            mock_zip = MagicMock()
            mock_zip.zip_directory.return_value = MagicMock(
                data=b'zip_data',
                files_count=5,
                original_size=1000,
                compressed_size=500
            )
            MockZip.return_value = mock_zip

            executor = SecureExecutor(
                enclave_client=mock_enclave_client,
                settings=mock_settings,
                middleware_client=mock_middleware_client
            )
            executor._zip_service = mock_zip
            return executor

    @pytest.fixture
    def sample_request(self, temp_repo):
        """Create sample execution request."""
        return JobExecutionRequest(
            job_id='JOB-123',
            repo_path=temp_repo,
            script_path='main.py',
            workspace_id='ws-001'
        )

    def test_executor_initialization(self, mock_enclave_client, mock_settings, mock_middleware_client):
        """Test SecureExecutor initializes correctly."""
        with patch('workers.executor.executor.ZipService'):
            with patch('workers.executor.executor.JobLogger'):
                executor = SecureExecutor(
                    enclave_client=mock_enclave_client,
                    settings=mock_settings,
                    middleware_client=mock_middleware_client
                )

                assert executor._enclave_client is mock_enclave_client
                assert executor._settings is mock_settings
                assert executor._middleware_client is mock_middleware_client

    def test_execute_success(self, secure_executor, sample_request, mock_enclave_client):
        """Test successful job execution."""
        result = secure_executor.execute(sample_request)

        assert result.status == 'success'
        assert result.job_id == 'JOB-123'
        assert result.output == 'Script output'
        assert result.error is None
        assert result.execution_time > 0

        # Verify all steps were called
        mock_enclave_client.get_public_key.assert_called_once()
        mock_enclave_client.encrypt_zip_data.assert_called_once()
        mock_enclave_client.send_encrypted_data_to_enclave.assert_called_once()

    def test_execute_no_datasets_skips_middleware(self, secure_executor, temp_repo_no_datasets, mock_middleware_client):
        """Test execution without datasets skips middleware fetch."""
        request = JobExecutionRequest(
            job_id='JOB-NO-DATA',
            repo_path=temp_repo_no_datasets,
            script_path='main.py',
            workspace_id='ws-001'
        )

        result = secure_executor.execute(request)

        assert result.status == 'success'
        # Middleware should not be called when no datasets
        mock_middleware_client.fetch_encrypted_csv.assert_not_called()

    def test_execute_enclave_failure(self, secure_executor, sample_request, mock_enclave_client):
        """Test handling of enclave execution failure."""
        mock_enclave_client.send_encrypted_data_to_enclave.return_value = (False, 'Script failed')

        result = secure_executor.execute(sample_request)

        assert result.status == 'failed'
        assert result.error == 'Script failed'

    def test_execute_middleware_fetch_error(self, secure_executor, sample_request, mock_middleware_client):
        """Test handling of middleware fetch error."""
        response = MagicMock()
        response.success = False
        response.error = 'Dataset not found'
        mock_middleware_client.fetch_encrypted_csv.return_value = response

        result = secure_executor.execute(sample_request)

        assert result.status == 'rejected'
        assert 'Dataset not found' in result.error

    def test_execute_middleware_empty_csv(self, secure_executor, sample_request, mock_middleware_client):
        """Test handling of empty CSV from middleware."""
        response = MagicMock()
        response.success = True
        response.encrypted_csv = ''
        response.error = None
        mock_middleware_client.fetch_encrypted_csv.return_value = response

        result = secure_executor.execute(sample_request)

        assert result.status == 'rejected'
        assert 'empty CSV' in result.error

    def test_execute_build_validation_error(self, secure_executor, mock_enclave_client):
        """Test handling of build validation error."""
        request = JobExecutionRequest(
            job_id='JOB-INVALID',
            repo_path='/nonexistent/path',
            script_path='main.py'
        )

        result = secure_executor.execute(request)

        assert result.status == 'rejected'
        assert 'Build' in result.error or 'not found' in result.error.lower()
        # Enclave should not be called on validation error
        mock_enclave_client.get_public_key.assert_not_called()

    def test_execute_enclave_keypair_error(self, secure_executor, sample_request, mock_enclave_client):
        """Test handling of enclave keypair generation error."""
        mock_enclave_client.get_public_key.side_effect = Exception('Enclave connection failed')

        result = secure_executor.execute(sample_request)

        assert result.status == 'failed'
        assert 'Enclave connection failed' in result.error

    def test_step_validate_build_success(self, secure_executor, temp_repo):
        """Test build validation step."""
        config = secure_executor._step_validate_build('JOB-123', temp_repo)

        assert config is not None
        assert config.script_file == 'main.py'
        assert len(config.datasets) == 1

    def test_step_get_public_key(self, secure_executor, mock_enclave_client):
        """Test public key retrieval step."""
        public_key, session_id = secure_executor._step_get_public_key('JOB-123')

        assert 'PUBLIC KEY' in public_key
        assert session_id == 'session_123'
        mock_enclave_client.get_public_key.assert_called_once_with('JOB-123')

    def test_step_fetch_encrypted_csv(self, secure_executor, sample_request, mock_middleware_client):
        """Test encrypted CSV fetch step."""
        build_config = BuildConfig(
            version='1.0',
            script_file='main.py',
            datasets=[DatasetConfig(dataset_id='ds-001', archetype_id='arch-001')],
            epsilon=1.0,
            timeout=300,
            environment='enclave'
        )

        encrypted_csv = secure_executor._step_fetch_encrypted_csv(
            'JOB-123', sample_request, build_config, 'public_key_pem'
        )

        assert encrypted_csv == 'encrypted_csv_base64'
        mock_middleware_client.fetch_encrypted_csv.assert_called_once()

    def test_step_fetch_encrypted_csv_no_datasets(self, secure_executor, sample_request):
        """Test CSV fetch step skipped when no datasets."""
        build_config = BuildConfig(
            version='1.0',
            script_file='main.py',
            datasets=[],
            epsilon=1.0,
            timeout=300,
            environment='enclave'
        )

        result = secure_executor._step_fetch_encrypted_csv(
            'JOB-123', sample_request, build_config, 'public_key_pem'
        )

        assert result is None

    def test_step_zip_and_encrypt(self, secure_executor, temp_repo, mock_enclave_client):
        """Test zip and encrypt step."""
        encrypted_zip = secure_executor._step_zip_and_encrypt(
            'JOB-123', temp_repo, 'public_key_pem'
        )

        assert encrypted_zip == 'encrypted_zip_base64'
        secure_executor._zip_service.zip_directory.assert_called_once()
        mock_enclave_client.encrypt_zip_data.assert_called_once()

    def test_step_execute_in_enclave(self, secure_executor, mock_enclave_client):
        """Test enclave execution step."""
        success, output = secure_executor._step_execute_in_enclave(
            'JOB-123', 'session_123', 'encrypted_zip', 'encrypted_csv'
        )

        assert success is True
        assert output == 'Script output'
        mock_enclave_client.send_encrypted_data_to_enclave.assert_called_once_with(
            'session_123', 'encrypted_zip', 'encrypted_csv'
        )

    def test_create_result_success(self, secure_executor):
        """Test result creation for successful execution."""
        result = secure_executor._create_result(
            job_id='JOB-123',
            success=True,
            output='Success output',
            execution_time=1.5
        )

        assert result.job_id == 'JOB-123'
        assert result.status == 'success'
        assert result.output == 'Success output'
        assert result.error is None
        assert result.execution_time == 1.5

    def test_create_result_failure(self, secure_executor):
        """Test result creation for failed execution."""
        result = secure_executor._create_result(
            job_id='JOB-123',
            success=False,
            output='Error message',
            execution_time=0.5
        )

        assert result.status == 'failed'
        assert result.error == 'Error message'
        assert result.output is None

    def test_handle_validation_error(self, secure_executor):
        """Test validation error handling."""
        error = BuildValidationError('Missing build.yml')

        result = secure_executor._handle_validation_error(
            'JOB-123', error, start_time=0
        )

        assert result.status == 'rejected'
        assert 'Missing build.yml' in result.error

    def test_handle_execution_error(self, secure_executor):
        """Test general error handling."""
        error = Exception('Unexpected error')

        result = secure_executor._handle_execution_error(
            'JOB-123', error, start_time=0
        )

        assert result.status == 'failed'
        assert 'Unexpected error' in result.error

    def test_prepare_environment(self, secure_executor, temp_repo):
        """Test environment preparation."""
        request = JobExecutionRequest(
            job_id='JOB-ENV',
            repo_path=temp_repo,
            script_path='main.py'
        )

        with patch('os.makedirs'):
            env = secure_executor.prepare_environment(request)

        assert env['job_id'] == 'JOB-ENV'
        assert env['status'] == 'ready'
        assert 'JOB-ENV' in secure_executor._active_jobs

    def test_prepare_environment_repo_not_found(self, secure_executor):
        """Test environment preparation with missing repo."""
        request = JobExecutionRequest(
            job_id='JOB-MISSING',
            repo_path='/nonexistent/path',
            script_path='main.py'
        )

        with pytest.raises(FileNotFoundError):
            secure_executor.prepare_environment(request)

    def test_cleanup_environment(self, secure_executor, temp_repo):
        """Test environment cleanup."""
        request = JobExecutionRequest(
            job_id='JOB-CLEANUP',
            repo_path=temp_repo,
            script_path='main.py'
        )

        with patch('os.makedirs'):
            secure_executor.prepare_environment(request)

        assert 'JOB-CLEANUP' in secure_executor._active_jobs

        with patch('shutil.rmtree'):
            secure_executor.cleanup_environment('JOB-CLEANUP')

        assert 'JOB-CLEANUP' not in secure_executor._active_jobs

    def test_get_status_active_job(self, secure_executor, temp_repo):
        """Test getting status of active job."""
        request = JobExecutionRequest(
            job_id='JOB-STATUS',
            repo_path=temp_repo,
            script_path='main.py'
        )

        with patch('os.makedirs'):
            secure_executor.prepare_environment(request)

        status = secure_executor.get_status('JOB-STATUS')

        assert status['job_id'] == 'JOB-STATUS'
        assert status['active'] is True
        assert status['status'] == 'ready'

    def test_get_status_unknown_job(self, secure_executor):
        """Test getting status of unknown job."""
        status = secure_executor.get_status('JOB-UNKNOWN')

        assert status['job_id'] == 'JOB-UNKNOWN'
        assert status['active'] is False
        assert status['status'] == 'unknown'

    def test_is_ready_property(self, secure_executor, mock_enclave_client):
        """Test is_ready property checks enclave health."""
        assert secure_executor.is_ready is True
        mock_enclave_client.health_check.assert_called()

    def test_is_ready_enclave_unhealthy(self, secure_executor, mock_enclave_client):
        """Test is_ready when enclave is unhealthy."""
        mock_enclave_client.health_check.return_value = False

        # Still returns True due to exception handling
        assert secure_executor.is_ready is False


class TestJobExecutionRequest:
    """Test cases for JobExecutionRequest model."""

    def test_from_dict(self):
        """Test creating request from dictionary."""
        data = {
            'job_id': 'JOB-MSG',
            'repo_path': '/path/to/repo',
            'script_path': 'analysis.py',
            'workspace_id': 'ws-001',
            'ai_decision': {'approved': True}
        }

        request = JobExecutionRequest(**data)

        assert request.job_id == 'JOB-MSG'
        assert request.repo_path == '/path/to/repo'
        assert request.script_path == 'analysis.py'

    def test_default_values(self):
        """Test default values are applied."""
        data = {
            'job_id': 'JOB-DICT',
            'repo_path': '/path/to/repo'
        }

        request = JobExecutionRequest(**data)

        assert request.job_id == 'JOB-DICT'
        assert request.script_path == 'example_analysis.py'  # Default


class TestExecutionResult:
    """Test cases for ExecutionResult model."""

    def test_to_dict(self):
        """Test converting result to dictionary."""
        result = ExecutionResult(
            job_id='JOB-123',
            status=JobStatus.SUCCESS,
            execution_time=1.5,
            output='Test output'
        )

        data = result.to_dict()

        assert data['job_id'] == 'JOB-123'
        assert data['status'] == 'success'
        assert data['execution_time'] == 1.5
        assert data['execution_type'] == 'nitro_enclave'

    def test_default_values(self):
        """Test default values are set correctly."""
        result = ExecutionResult(
            job_id='JOB-123',
            status=JobStatus.SUCCESS,
            execution_time=1.0
        )

        assert result.artifacts == []
        assert result.timestamp is not None

