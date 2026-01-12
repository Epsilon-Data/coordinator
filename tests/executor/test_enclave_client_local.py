"""
Tests for workers/executor/clients.py - EnclaveClientLocal
"""
import zipfile
import tempfile
from pathlib import Path
from io import BytesIO
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from workers.executor.clients import EnclaveClientLocal


class TestEnclaveClientLocal:
    """Test cases for EnclaveClientLocal class."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        return settings

    @pytest.fixture
    def enclave_client(self, mock_settings):
        """Create EnclaveClientLocal instance."""
        with patch('workers.executor.clients.get_settings', return_value=mock_settings):
            return EnclaveClientLocal()

    @pytest.fixture
    def sample_zip_with_script(self):
        """Create a sample ZIP file with build.yml and script."""
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Add build.yml
            build_yml = """
version: "1.0"
analysis:
  script_file: main.py
datasets: []
"""
            zf.writestr('build.yml', build_yml)

            # Add main.py script
            script = 'print("Hello from enclave!")'
            zf.writestr('main.py', script)

            # Add generated folder
            zf.writestr('generated/.gitkeep', '')

        buffer.seek(0)
        return buffer.read()

    @pytest.fixture
    def sample_csv_data(self):
        """Create sample CSV data."""
        return b"id,name,value\n1,test,100\n2,sample,200"

    def test_initialization(self, mock_settings):
        """Test EnclaveClientLocal initializes correctly."""
        with patch('workers.executor.clients.get_settings', return_value=mock_settings):
            client = EnclaveClientLocal()

            assert client._connected is True
            assert client._sessions == {}
            assert client.enclave_cid == 999  # Default CID

    def test_initialization_with_cid(self, mock_settings):
        """Test EnclaveClientLocal with custom CID."""
        with patch('workers.executor.clients.get_settings', return_value=mock_settings):
            client = EnclaveClientLocal(enclave_cid=42)

            assert client.enclave_cid == 42

    def test_get_public_key(self, enclave_client):
        """Test get_public_key generates keypair and returns session."""
        public_key, session_id = enclave_client.get_public_key('JOB-123')

        assert public_key.startswith('-----BEGIN PUBLIC KEY-----')
        assert public_key.endswith('-----END PUBLIC KEY-----\n')
        assert session_id == 'local_session_JOB-123'
        assert session_id in enclave_client._sessions

    def test_get_public_key_unique_sessions(self, enclave_client):
        """Test that different jobs get unique sessions."""
        _, session1 = enclave_client.get_public_key('JOB-1')
        _, session2 = enclave_client.get_public_key('JOB-2')

        assert session1 != session2
        assert len(enclave_client._sessions) == 2

    def test_encrypt_zip_data(self, enclave_client, sample_zip_with_script):
        """Test encrypt_zip_data encrypts data correctly."""
        public_key, _ = enclave_client.get_public_key('JOB-123')

        encrypted = enclave_client.encrypt_zip_data(sample_zip_with_script, public_key)

        # Should return base64 string
        assert isinstance(encrypted, str)
        assert len(encrypted) > len(sample_zip_with_script)

    def test_send_encrypted_data_success(self, enclave_client, sample_zip_with_script):
        """Test successful execution of encrypted data."""
        public_key, session_id = enclave_client.get_public_key('JOB-123')
        encrypted_zip = enclave_client.encrypt_zip_data(sample_zip_with_script, public_key)

        success, output = enclave_client.send_encrypted_data_to_enclave(
            session_id=session_id,
            encrypted_zip=encrypted_zip
        )

        assert success is True
        assert 'Hello from enclave!' in output

    def test_send_encrypted_data_with_csv(self, enclave_client, sample_zip_with_script, sample_csv_data):
        """Test execution with encrypted CSV data."""
        # Create ZIP with script that reads CSV
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            build_yml = """
version: "1.0"
analysis:
  script_file: main.py
datasets:
  - dataset_id: ds-001
    archetype_id: arch-001
"""
            zf.writestr('build.yml', build_yml)

            script = """
import os
csv_path = 'generated/data.csv'
if os.path.exists(csv_path):
    with open(csv_path, 'r') as f:
        print(f'CSV content: {f.read()}')
else:
    print('No CSV found')
"""
            zf.writestr('main.py', script)
            zf.writestr('generated/.gitkeep', '')

        buffer.seek(0)
        zip_data = buffer.read()

        public_key, session_id = enclave_client.get_public_key('JOB-123')
        encrypted_zip = enclave_client.encrypt_zip_data(zip_data, public_key)
        encrypted_csv = enclave_client._crypto.encrypt(sample_csv_data, public_key)

        success, output = enclave_client.send_encrypted_data_to_enclave(
            session_id=session_id,
            encrypted_zip=encrypted_zip,
            encrypted_csv=encrypted_csv
        )

        assert success is True
        assert 'id,name,value' in output  # CSV content should appear

    def test_send_encrypted_data_session_not_found(self, enclave_client, sample_zip_with_script):
        """Test error when session not found."""
        public_key, _ = enclave_client.get_public_key('JOB-123')
        encrypted_zip = enclave_client.encrypt_zip_data(sample_zip_with_script, public_key)

        success, output = enclave_client.send_encrypted_data_to_enclave(
            session_id='nonexistent_session',
            encrypted_zip=encrypted_zip
        )

        assert success is False
        assert 'Session not found' in output

    def test_send_encrypted_data_cleans_up_session(self, enclave_client, sample_zip_with_script):
        """Test that session is cleaned up after execution."""
        public_key, session_id = enclave_client.get_public_key('JOB-123')
        encrypted_zip = enclave_client.encrypt_zip_data(sample_zip_with_script, public_key)

        assert session_id in enclave_client._sessions

        enclave_client.send_encrypted_data_to_enclave(
            session_id=session_id,
            encrypted_zip=encrypted_zip
        )

        # Session should be removed after execution
        assert session_id not in enclave_client._sessions

    def test_send_encrypted_data_decryption_error(self, enclave_client):
        """Test handling of decryption errors."""
        _, session_id = enclave_client.get_public_key('JOB-123')

        success, output = enclave_client.send_encrypted_data_to_enclave(
            session_id=session_id,
            encrypted_zip='invalid_encrypted_data'
        )

        assert success is False

    def test_execute_script_no_build_yml(self, enclave_client):
        """Test execution fails without build.yml."""
        # Create ZIP without build.yml
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('main.py', 'print("test")')
        buffer.seek(0)
        zip_data = buffer.read()

        public_key, session_id = enclave_client.get_public_key('JOB-123')
        encrypted_zip = enclave_client.encrypt_zip_data(zip_data, public_key)

        success, output = enclave_client.send_encrypted_data_to_enclave(
            session_id=session_id,
            encrypted_zip=encrypted_zip
        )

        assert success is True  # Still returns True but with error message
        assert 'build.yml' in output.lower() or 'script_file' in output.lower()

    def test_execute_script_not_found(self, enclave_client):
        """Test execution when script file doesn't exist."""
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            build_yml = """
analysis:
  script_file: nonexistent.py
"""
            zf.writestr('build.yml', build_yml)
        buffer.seek(0)
        zip_data = buffer.read()

        public_key, session_id = enclave_client.get_public_key('JOB-123')
        encrypted_zip = enclave_client.encrypt_zip_data(zip_data, public_key)

        success, output = enclave_client.send_encrypted_data_to_enclave(
            session_id=session_id,
            encrypted_zip=encrypted_zip
        )

        assert success is True
        assert 'not found' in output.lower()

    def test_execute_script_with_error(self, enclave_client):
        """Test execution of script that raises error."""
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            build_yml = """
analysis:
  script_file: main.py
"""
            zf.writestr('build.yml', build_yml)
            zf.writestr('main.py', 'raise ValueError("Test error")')
        buffer.seek(0)
        zip_data = buffer.read()

        public_key, session_id = enclave_client.get_public_key('JOB-123')
        encrypted_zip = enclave_client.encrypt_zip_data(zip_data, public_key)

        success, output = enclave_client.send_encrypted_data_to_enclave(
            session_id=session_id,
            encrypted_zip=encrypted_zip
        )

        assert success is True  # Execution happened, but script failed
        assert 'ValueError' in output or 'error' in output.lower()

    @patch('workers.executor.clients.subprocess.run')
    def test_execute_script_timeout(self, mock_run, enclave_client, sample_zip_with_script):
        """Test script timeout handling."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='python', timeout=60)

        public_key, session_id = enclave_client.get_public_key('JOB-123')
        encrypted_zip = enclave_client.encrypt_zip_data(sample_zip_with_script, public_key)

        success, output = enclave_client.send_encrypted_data_to_enclave(
            session_id=session_id,
            encrypted_zip=encrypted_zip
        )

        assert success is True
        assert 'timed out' in output.lower()

    def test_health_check(self, enclave_client):
        """Test health check always returns True for local."""
        assert enclave_client.health_check() is True

    def test_is_connected_property(self, enclave_client):
        """Test is_connected property."""
        assert enclave_client.is_connected is True

    def test_connect(self, enclave_client):
        """Test connect method."""
        enclave_client._connected = False
        enclave_client.connect()
        assert enclave_client._connected is True

    def test_disconnect(self, enclave_client):
        """Test disconnect clears sessions and sets connected to False."""
        # Add some sessions
        enclave_client.get_public_key('JOB-1')
        enclave_client.get_public_key('JOB-2')
        assert len(enclave_client._sessions) == 2

        enclave_client.disconnect()

        assert enclave_client._connected is False
        assert len(enclave_client._sessions) == 0

    def test_get_script_from_build_yml_invalid_yaml(self, enclave_client):
        """Test handling of invalid YAML in build.yml."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / 'build.yml').write_text('invalid: yaml: [')

            result = enclave_client._get_script_from_build_yml(temp_path)

            assert result is None

    def test_get_script_from_build_yml_not_dict(self, enclave_client):
        """Test handling of non-dict YAML."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / 'build.yml').write_text('- item1\n- item2')

            result = enclave_client._get_script_from_build_yml(temp_path)

            assert result is None

    def test_get_script_from_build_yml_no_analysis(self, enclave_client):
        """Test handling of missing analysis section."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / 'build.yml').write_text('version: "1.0"')

            result = enclave_client._get_script_from_build_yml(temp_path)

            assert result is None

    def test_get_script_from_build_yml_success(self, enclave_client):
        """Test successful script file extraction."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / 'build.yml').write_text("""
analysis:
  script_file: analysis.py
""")

            result = enclave_client._get_script_from_build_yml(temp_path)

            assert result == 'analysis.py'

    def test_full_zero_trust_flow(self, enclave_client):
        """Test complete Zero Trust encryption/decryption flow."""
        # Create realistic build folder
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            build_yml = """
version: "1.0"
analysis:
  script_file: main.py
datasets:
  - dataset_id: ds-001
    archetype_id: arch-001
    archetype_path: generated/data.csv
privacy:
  epsilon: 1.5
"""
            zf.writestr('build.yml', build_yml)
            zf.writestr('main.py', """
import csv
import os

csv_path = 'generated/data.csv'
if os.path.exists(csv_path):
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        print(f'Loaded {len(rows)} rows')
        print(f'Columns: {rows[0].keys() if rows else "none"}')
else:
    print('CSV not found - running without data')
""")
            zf.writestr('generated/.gitkeep', '')

        buffer.seek(0)
        zip_data = buffer.read()

        # Real CSV data
        csv_data = b"id,name,value,score\n1,Alice,100,95\n2,Bob,200,87\n3,Charlie,300,92"

        # Generate keypair
        public_key, session_id = enclave_client.get_public_key('JOB-ZERO-TRUST')

        # Encrypt both payloads
        encrypted_zip = enclave_client.encrypt_zip_data(zip_data, public_key)
        encrypted_csv = enclave_client._crypto.encrypt(csv_data, public_key)

        # Execute in "enclave"
        success, output = enclave_client.send_encrypted_data_to_enclave(
            session_id=session_id,
            encrypted_zip=encrypted_zip,
            encrypted_csv=encrypted_csv
        )

        assert success is True
        assert 'Loaded 3 rows' in output
        assert 'id' in output and 'name' in output

