"""Tests for ATLClient Commitment entry submission (sprint A1/A2)."""
import hashlib
from unittest.mock import MagicMock

import cbor2
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

from workers.executor.atl_client import (
    ATLClient,
    COMMITMENT_HASH_LEN,
    ENTRY_TYPE_COMMITMENT,
)


@pytest.fixture
def atl_client():
    """ATLClient with a fresh Ed25519 key and a MagicMock HTTP client."""
    priv = Ed25519PrivateKey.generate()
    client = ATLClient(
        atl_url="http://localhost:8080",
        submitter_id="test-coordinator",
        private_key=priv,
    )
    client._client = MagicMock()  # replace real httpx.Client
    return client


def _mock_201_response(payload: dict):
    """Build a MagicMock httpx response with CBOR-encoded body, status 201."""
    resp = MagicMock()
    resp.status_code = 201
    resp.content = cbor2.dumps(payload)
    return resp


def _mock_error_response(status: int, message: str = "boom"):
    resp = MagicMock()
    resp.status_code = status
    resp.content = cbor2.dumps({"error": message})
    resp.text = message
    return resp


class TestEntryTypeConstant:
    def test_commitment_constant_is_4(self):
        assert ENTRY_TYPE_COMMITMENT == 4

    def test_commitment_hash_len_is_32(self):
        assert COMMITMENT_HASH_LEN == 32


class TestSubmitCommitmentEntry:
    def test_rejects_short_hash(self, atl_client):
        with pytest.raises(ValueError, match="32 bytes"):
            atl_client.submit_commitment_entry(
                job_id="job-1",
                commitment_hash=b"too short",
                coord_signature=b"\x00" * 64,
            )

    def test_rejects_long_hash(self, atl_client):
        with pytest.raises(ValueError, match="32 bytes"):
            atl_client.submit_commitment_entry(
                job_id="job-1",
                commitment_hash=b"\x00" * 64,
                coord_signature=b"\x00" * 64,
            )

    def test_constructs_correct_cbor_entry(self, atl_client):
        atl_client._client.post.return_value = _mock_201_response({
            "leaf_index": 7, "tree_size": 8, "root_hash": b"\x01" * 32,
            "timestamp": 1700000000, "receipt": b"cose-bytes",
        })

        job_id = "job-c1"
        commitment_hash = bytes(range(32))
        coord_signature = b"\xAA" * 64

        ok, response = atl_client.submit_commitment_entry(
            job_id=job_id,
            commitment_hash=commitment_hash,
            coord_signature=coord_signature,
            timestamp=1700000000,
        )

        assert ok is True
        assert response["leaf_index"] == 7
        assert response["tree_size"] == 8

        # Inspect the actual POST body sent to ATL
        call = atl_client._client.post.call_args
        sent_cbor = call.kwargs["content"]
        entry = cbor2.loads(sent_cbor)
        # Wire format must match the Go CommitmentEntry struct:
        # 0=type, 1=job_id, 2=commitment_hash, 3=coord_signature, 4=submitter_id, 5=timestamp
        assert entry[0] == ENTRY_TYPE_COMMITMENT
        assert entry[1] == job_id
        assert entry[2] == commitment_hash
        assert entry[3] == coord_signature
        assert entry[4] == "test-coordinator"
        assert entry[5] == 1700000000

    def test_sends_required_headers(self, atl_client):
        atl_client._client.post.return_value = _mock_201_response({
            "leaf_index": 0, "tree_size": 1,
        })

        atl_client.submit_commitment_entry(
            job_id="job-1",
            commitment_hash=b"\x00" * 32,
            coord_signature=b"\x01" * 64,
        )

        headers = atl_client._client.post.call_args.kwargs["headers"]
        assert headers["Content-Type"] == "application/cbor"
        assert headers["X-Submitter-ID"] == "test-coordinator"
        assert "X-Coordinator-Signature" in headers

    def test_post_targets_v1_entries(self, atl_client):
        atl_client._client.post.return_value = _mock_201_response({
            "leaf_index": 0, "tree_size": 1,
        })
        atl_client.submit_commitment_entry(
            job_id="job-1",
            commitment_hash=b"\x00" * 32,
            coord_signature=b"\x01" * 64,
        )
        url = atl_client._client.post.call_args.args[0]
        assert url.endswith("/v1/entries")

    def test_returns_none_on_non_201(self, atl_client):
        atl_client._client.post.return_value = _mock_error_response(400, "bad")
        ok, response = atl_client.submit_commitment_entry(
            job_id="job-1",
            commitment_hash=b"\x00" * 32,
            coord_signature=b"\x01" * 64,
        )
        assert ok is False
        assert response is None


class TestSignAndSubmitCommitment:
    def test_commitment_hash_is_sha256_of_payload(self, atl_client):
        atl_client._client.post.return_value = _mock_201_response({
            "leaf_index": 1, "tree_size": 2,
        })

        jac_payload = b"job-c1|sh|ds-1|nonce|t"
        commitment_hash, response = atl_client.sign_and_submit_commitment(
            job_id="job-c1",
            jac_payload_bytes=jac_payload,
        )

        assert commitment_hash == hashlib.sha256(jac_payload).digest()
        assert len(commitment_hash) == 32
        assert response is not None
        assert response["tree_size"] == 2

    def test_signs_over_job_id_concat_commitment_hash(self, atl_client):
        """The in-entry CoordSignature must be Ed25519(job_id || commitment_hash).

        epsilon-atl policy.validateCommitment verifies this exact preimage.
        """
        atl_client._client.post.return_value = _mock_201_response({
            "leaf_index": 1, "tree_size": 2,
        })

        job_id = "job-c1"
        jac_payload = b"some-jac-payload-bytes"
        commitment_hash, _ = atl_client.sign_and_submit_commitment(
            job_id=job_id,
            jac_payload_bytes=jac_payload,
        )

        # Extract the in-entry signature from the wire payload
        sent_cbor = atl_client._client.post.call_args.kwargs["content"]
        entry = cbor2.loads(sent_cbor)
        coord_signature = entry[3]

        # Verify against the public key derived from the test private key
        pub = atl_client._private_key.public_key()
        signed_payload = job_id.encode() + commitment_hash
        # ed25519 verify raises on failure
        pub.verify(coord_signature, signed_payload)

    def test_returns_hash_even_when_submission_fails(self, atl_client):
        """Caller may need commitment_hash for the held JAC even on failure."""
        atl_client._client.post.return_value = _mock_error_response(503)

        jac_payload = b"payload"
        commitment_hash, response = atl_client.sign_and_submit_commitment(
            job_id="job-c1",
            jac_payload_bytes=jac_payload,
        )

        assert commitment_hash == hashlib.sha256(jac_payload).digest()
        assert response is None
