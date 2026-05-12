"""Tests for SecureExecutor Step 4b commitment-then-dispatch (sprint A3).

Covers the five paper-load-bearing branches:
1. Happy path: researcher nonce + ATL online → real H(r||o), Commitment logged
2. ATL unreachable + allow_stale=False → RuntimeError, no enclave dispatch
3. ATL unreachable + allow_stale=True → Non-Compliant, continues
4. No researcher_nonce → Non-Compliant fallback, server generates r
5. Commitment submission fails → RuntimeError, no enclave dispatch
"""
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from workers.executor.executor import SecureExecutor
from workers.executor.models import JobExecutionRequest


@pytest.fixture
def mock_enclave_client():
    client = MagicMock()
    client.get_public_key.return_value = ('pubkey-pem', 'session_123')
    client.encrypt_zip_data.return_value = 'encrypted_zip_base64'
    client.send_encrypted_data_to_enclave.return_value = (True, 'Script output', None)
    client.health_check.return_value = True
    client.enclave_cid = 1
    return client


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.middleware = MagicMock()
    s.middleware.use_direct_db = False
    return s


@pytest.fixture
def mock_middleware_client():
    client = MagicMock()
    response = MagicMock()
    response.success = True
    response.encrypted_csv = 'encrypted_csv_base64'
    response.error = None
    response.is_direct_db = False
    response.is_proxy = False
    response.mode = 'legacy'
    client.fetch_encrypted_csv.return_value = response
    return client


@pytest.fixture
def mock_atl_client():
    """ATL client with a real Ed25519 key (so sign_jac inside Step 4b works)."""
    client = MagicMock()
    client._private_key = Ed25519PrivateKey.generate()
    # Default: ATL is online with a valid STH
    client.fetch_sth.return_value = {
        'tree_size': 1000,
        'root_hash': b'\x01' * 32,
        'timestamp': 1700000000,
    }
    client.derive_nonce.return_value = b'\xAA' * 32
    # Default: successful Commitment submission
    client.sign_and_submit_commitment.return_value = (
        b'\xCC' * 32,  # commitment_hash
        {'leaf_index': 7, 'tree_size': 1001, 'root_hash': b'\x02' * 32},
    )
    return client


@pytest.fixture
def temp_repo():
    """Temp repo with a build/ folder, build.yml, and main.py."""
    tmp = tempfile.mkdtemp()
    p = Path(tmp)
    build = p / 'build'
    build.mkdir()
    (build / 'generated').mkdir()
    (build / 'build.yml').write_text(
        'version: "1.0"\n'
        'analysis:\n'
        '  script_file: main.py\n'
        'datasets:\n'
        '  - dataset_id: ds-001\n'
        '    archetype_id: arch-001\n'
        'privacy:\n'
        '  epsilon: 1.5\n'
    )
    (build / 'main.py').write_text('print("hello")')
    yield str(p)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def executor_with_atl(mock_enclave_client, mock_settings, mock_middleware_client, mock_atl_client):
    """SecureExecutor with mocked ATL client wired in."""
    with patch('workers.executor.executor.ZipService') as MockZip, \
         patch('workers.executor.executor.JobLogger'):
        zip_mock = MagicMock()
        zip_mock.zip_directory.return_value = MagicMock(
            data=b'zip_data', files_count=1, original_size=10, compressed_size=8
        )
        MockZip.return_value = zip_mock
        ex = SecureExecutor(
            enclave_client=mock_enclave_client,
            settings=mock_settings,
            middleware_client=mock_middleware_client,
            atl_client=mock_atl_client,
        )
        ex._zip_service = zip_mock
        return ex


def _request(temp_repo, **overrides):
    base = dict(
        job_id='JOB-OP-1',
        repo_path=temp_repo,
        script_path='main.py',
        workspace_id='ws-001',
    )
    base.update(overrides)
    return JobExecutionRequest(**base)


# -----------------------------------------------------------------------------
# Branch 1 — Happy path
# -----------------------------------------------------------------------------

class TestHappyPath:
    def test_real_mutual_commitment_when_researcher_nonce_supplied(
        self, executor_with_atl, temp_repo, mock_atl_client
    ):
        req = _request(temp_repo, researcher_nonce='ab' * 16)
        result = executor_with_atl.execute(req)

        assert result.is_non_compliant is False
        assert result.job_id_committed is not None
        # SHA-256 hex is 64 chars
        assert len(result.job_id_committed) == 64
        assert result.researcher_nonce == 'ab' * 16
        # Commitment was actually submitted
        mock_atl_client.sign_and_submit_commitment.assert_called_once()
        # JAC + receipt + commitment_hash all populated
        assert result.signed_jac is not None
        assert result.commitment_receipt is not None
        assert result.commitment_receipt['leaf_index'] == 7
        assert result.commitment_hash is not None
        # commitment_hash should be hex
        assert all(c in '0123456789abcdef' for c in result.commitment_hash)

    def test_step_4b_signs_jac_over_committed_id_not_operational(
        self, executor_with_atl, temp_repo, mock_atl_client
    ):
        """JAC payload must reference job_id_committed, not request.job_id."""
        req = _request(temp_repo, job_id='JOB-OP-OPAQUE-7', researcher_nonce='ab' * 16)
        result = executor_with_atl.execute(req)

        # The job_id field inside the JAC must be the committed identity,
        # not the operational platform-assigned one.
        assert result.signed_jac['job_id'] == result.job_id_committed
        assert result.signed_jac['job_id'] != 'JOB-OP-OPAQUE-7'

    def test_step_4b_passes_nonce_and_context_to_enclave_call(
        self, executor_with_atl, temp_repo, mock_enclave_client
    ):
        req = _request(temp_repo, researcher_nonce='ab' * 16)
        executor_with_atl.execute(req)

        # The enclave was called with the ATL nonce + context hash so the
        # hardware-signed user_data binds them (C1).
        call = mock_enclave_client.send_encrypted_data_to_enclave.call_args
        assert call.kwargs['atl_nonce'] is not None
        assert call.kwargs['atl_context_hash'] is not None
        assert isinstance(call.kwargs['atl_nonce'], bytes)
        assert isinstance(call.kwargs['atl_context_hash'], bytes)


# -----------------------------------------------------------------------------
# Branch 2 — ATL unreachable, allow_stale=False → refuse
# -----------------------------------------------------------------------------

class TestAtlUnreachableRefuses:
    def test_raises_when_sth_unreachable_and_not_allow_stale(
        self, executor_with_atl, temp_repo, mock_atl_client, mock_enclave_client
    ):
        mock_atl_client.fetch_sth.return_value = None
        req = _request(temp_repo, researcher_nonce='ab' * 16, allow_stale=False)
        result = executor_with_atl.execute(req)

        # The RuntimeError is caught by execute()'s top-level handler and
        # converted into a FAILED result. The key invariant is: no enclave
        # dispatch happened.
        assert result.status.value == 'failed'
        mock_enclave_client.send_encrypted_data_to_enclave.assert_not_called()
        mock_atl_client.sign_and_submit_commitment.assert_not_called()


# -----------------------------------------------------------------------------
# Branch 3 — ATL unreachable, allow_stale=True → Non-Compliant continue
# -----------------------------------------------------------------------------

class TestAllowStale:
    def test_marks_non_compliant_and_continues_when_allow_stale(
        self, executor_with_atl, temp_repo, mock_atl_client, mock_enclave_client
    ):
        mock_atl_client.fetch_sth.return_value = None
        req = _request(temp_repo, researcher_nonce='ab' * 16, allow_stale=True)
        result = executor_with_atl.execute(req)

        assert result.is_non_compliant is True
        # No Commitment submission — STH was unreachable
        mock_atl_client.sign_and_submit_commitment.assert_not_called()
        # But the enclave still ran
        mock_enclave_client.send_encrypted_data_to_enclave.assert_called_once()
        # And the job succeeded
        assert result.status.value == 'success'


# -----------------------------------------------------------------------------
# Branch 4 — No researcher_nonce → Non-Compliant fallback
# -----------------------------------------------------------------------------

class TestMissingResearcherNonce:
    def test_marks_non_compliant_when_researcher_nonce_absent(
        self, executor_with_atl, temp_repo, mock_atl_client, mock_enclave_client
    ):
        # ATL is online, but no researcher nonce — mutual commitment property
        # is broken on the researcher's side.
        req = _request(temp_repo)  # no researcher_nonce
        result = executor_with_atl.execute(req)

        assert result.is_non_compliant is True
        # job_id_committed is still computed from the server-generated nonce
        assert result.job_id_committed is not None
        # Non-Compliant jobs skip the Commitment log entry
        mock_atl_client.sign_and_submit_commitment.assert_not_called()
        # Enclave still ran
        mock_enclave_client.send_encrypted_data_to_enclave.assert_called_once()


# -----------------------------------------------------------------------------
# Branch 5 — Commitment submission fails → refuse
# -----------------------------------------------------------------------------

class TestCommitmentSubmissionFailure:
    def test_raises_when_commitment_receipt_is_none(
        self, executor_with_atl, temp_repo, mock_atl_client, mock_enclave_client
    ):
        # ATL reachable, JAC signed, but the log refused the submission
        # (e.g., invalid signature on the in-entry CoordSignature).
        mock_atl_client.sign_and_submit_commitment.return_value = (b'\xCC' * 32, None)
        req = _request(temp_repo, researcher_nonce='ab' * 16)
        result = executor_with_atl.execute(req)

        # Same as STH-unreachable case: caught at top level, no dispatch
        assert result.status.value == 'failed'
        mock_enclave_client.send_encrypted_data_to_enclave.assert_not_called()
