"""
Shared pytest fixtures for Epsilon Coordinator tests.
"""
import os
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock
from typing import Dict, Any

import pytest


# =============================================================================
# Sample Data Fixtures
# =============================================================================

@pytest.fixture
def sample_job() -> Dict[str, Any]:
    """Sample job dictionary for testing."""
    return {
        'job_id': 'JOB-TEST123',
        'workspace_id': 'ws-test-001',
        'user_id': 'user-test-001',
        'status': 'pending',
        'github_repo': 'testuser/testrepo',
        'github_branch': 'main',
        'commit_sha': 'abc123def456',
        'commit_message': 'Test commit',
        'commit_author': 'testuser',
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
        'repo_path': '/tmp/test-repo',
        'error_message': None,
        'execution_output': None,
    }


@pytest.fixture
def sample_build_yml() -> str:
    """Sample build.yml content."""
    return """
analysis:
  script_file: main.py
  epsilon: 1.0

datasets:
  - dataset_id: ds-test-001
    archetype_id: arch-test-001
    archetype_path: generated/data.csv
    import_path: data.csv
"""


@pytest.fixture
def sample_build_config() -> Dict[str, Any]:
    """Parsed build.yml config."""
    return {
        'analysis': {
            'script_file': 'main.py',
            'epsilon': 1.0,
        },
        'datasets': [
            {
                'dataset_id': 'ds-test-001',
                'archetype_id': 'arch-test-001',
                'archetype_path': 'generated/data.csv',
                'import_path': 'data.csv',
            }
        ]
    }


# =============================================================================
# Temporary Directory Fixtures
# =============================================================================

@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def temp_repo_dir(temp_dir, sample_build_yml):
    """Create a temporary repository directory with build folder structure."""
    repo_path = Path(temp_dir) / 'test-repo'
    build_path = repo_path / 'build'
    generated_path = build_path / 'generated'

    # Create directories
    build_path.mkdir(parents=True)
    generated_path.mkdir(parents=True)

    # Create build.yml
    (build_path / 'build.yml').write_text(sample_build_yml)

    # Create main.py
    (build_path / 'main.py').write_text('''
import csv

def main():
    print("Hello from test script")
    return {"result": "success"}

if __name__ == "__main__":
    main()
''')

    # Create dummy CSV
    (generated_path / 'data.csv').write_text('id,name\n1,test\n')

    yield str(repo_path)


# =============================================================================
# RSA Keypair Fixtures
# =============================================================================

@pytest.fixture
def rsa_keypair():
    """Generate RSA keypair for encryption tests."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend

    # Generate key pair
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )

    # Serialize private key
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode('utf-8')

    # Serialize public key
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')

    return {
        'private_key': private_key,
        'public_key': public_key,
        'private_pem': private_pem,
        'public_pem': public_pem,
    }


# =============================================================================
# Mock Fixtures
# =============================================================================

@pytest.fixture
def mock_job_repository():
    """Mock JobRepository for database operations."""
    mock = MagicMock()
    mock.update_job_status = MagicMock(return_value=True)
    mock.get_job = MagicMock(return_value=None)
    mock.fetch_pending_jobs = MagicMock(return_value=[])
    mock.fetch_queued_jobs = MagicMock(return_value=[])
    mock.fetch_ai_approved_jobs = MagicMock(return_value=[])
    mock.add_job_log = MagicMock(return_value=True)
    mock.add_detailed_log = MagicMock(return_value=True)
    return mock


@pytest.fixture
def mock_job_logger():
    """Mock JobLogger for step logging."""
    mock = MagicMock()
    mock.log_step_start = MagicMock()
    mock.log_step_progress = MagicMock()
    mock.log_step_complete = MagicMock()
    mock.log_step_error = MagicMock()
    mock.log_critical = MagicMock()
    mock.log_warning = MagicMock()
    mock.log_debug = MagicMock()
    return mock


@pytest.fixture
def mock_enclave_client():
    """Mock enclave client for executor tests."""
    mock = MagicMock()
    mock.get_public_key = MagicMock(return_value=('-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----', 'session-123'))
    mock.encrypt_zip_data = MagicMock(return_value='encrypted_zip_data_base64')
    mock.send_encrypted_data_to_enclave = MagicMock(return_value=(True, 'Execution successful'))
    mock.health_check = MagicMock(return_value=True)
    mock.is_connected = True
    mock.enclave_cid = 999
    return mock


@pytest.fixture
def mock_middleware_client():
    """Mock middleware client for executor tests."""
    mock = MagicMock()

    # Create a mock response object
    mock_response = MagicMock()
    mock_response.success = True
    mock_response.encrypted_csv = 'encrypted_csv_base64_data'
    mock_response.csv_metadata = {'rows': 10}
    mock_response.error = None

    mock.fetch_encrypted_csv = MagicMock(return_value=mock_response)
    mock.health_check = MagicMock(return_value=True)
    mock.is_enabled = True

    return mock


@pytest.fixture
def mock_settings():
    """Mock Settings object."""
    mock = MagicMock()
    mock.worker_id = 'test-worker-001'
    mock.environment = 'test'
    mock.storage = MagicMock()
    mock.storage.shared_storage_path = '/tmp/test-storage'
    mock.enclave = MagicMock()
    mock.enclave.use_local_client = True
    mock.enclave.vsock_port = 5005
    mock.middleware = MagicMock()
    mock.middleware.endpoint_url = 'http://localhost:8001'
    mock.middleware.timeout_seconds = 60
    mock.execution = MagicMock()
    mock.execution.script_timeout_seconds = 300
    mock.is_production = MagicMock(return_value=False)
    mock.is_development = MagicMock(return_value=True)
    return mock


# =============================================================================
# Environment Fixtures
# =============================================================================

@pytest.fixture
def clean_env():
    """Provide a clean environment for testing."""
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def test_env(clean_env):
    """Set up test environment variables."""
    os.environ.update({
        'DATABASE_URL': 'postgresql://test:test@localhost:5432/testdb',
        'SHARED_STORAGE_PATH': '/tmp/test-storage',
        'WORKER_ID': 'test-worker-001',
        'ENVIRONMENT': 'test',
        'MIDDLEWARE_ENDPOINT_URL': 'http://localhost:8001',
        'USE_LOCAL_ENCLAVE': 'true',
    })
    yield os.environ
