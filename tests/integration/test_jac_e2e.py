"""End-to-end integration test for commitment-then-dispatch (sprint A7).

Exercises the full Commitment + HA flow against a *real* running epsilon-atl
service. Verifies the wire contract end-to-end:

  1. POST /v1/entries with a CommitmentEntry — server enforces in-entry
     Ed25519 signature over (job_id || commitment_hash) (PR #2 on epsilon-atl)
  2. POST /v1/entries with an HAEntry referencing the same job_id_committed
  3. Both entries are retrievable via GET /v1/entries/{index}
  4. Ordering: Commitment leaf_index < HA leaf_index for the same job

Skipped by default (needs ATL up). Run explicitly:

    docker-compose -f ../epsilon-atl/docker-compose.yml up -d
    # Generate an Ed25519 key and register it as a coordinator key in ATL
    export ATL_ENABLED=true
    export ATL_URL=http://localhost:8080
    export ATL_SUBMITTER_ID=coordinator-test
    export ATL_SIGNING_KEY_PATH=/tmp/coord-test.pem
    pytest tests/integration/test_jac_e2e.py -v

The skip uses a TCP ping rather than an env var so accidentally leaving
ATL_ENABLED set in your shell doesn't run integration tests against a stale
log.
"""
import os
import secrets
import socket
import time
from urllib.parse import urlparse

import pytest

from workers.executor.atl_client import ATLClient
from workers.executor.jac import (
    compute_context_hash,
    compute_job_id,
    compute_script_hash,
    sign_jac,
)


def _atl_reachable(url: str) -> bool:
    """TCP-ping the ATL service to decide whether to run integration tests."""
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 80
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except (OSError, socket.timeout):
        return False


ATL_URL = os.environ.get("ATL_URL", "http://localhost:8080")

pytestmark = pytest.mark.skipif(
    not _atl_reachable(ATL_URL),
    reason=(
        f"ATL service not reachable at {ATL_URL}. "
        f"Start it with: docker-compose -f epsilon-atl/docker-compose.yml up -d"
    ),
)


@pytest.fixture(scope="module")
def atl_client() -> ATLClient:
    """ATLClient built from env vars. Fails the test if not fully configured."""
    client = ATLClient.from_env()
    if client is None:
        pytest.skip(
            "ATL env not fully configured. Set ATL_ENABLED=true, ATL_URL, "
            "ATL_SUBMITTER_ID, ATL_SIGNING_KEY_PATH and retry."
        )
    return client


def test_full_commitment_then_ha_flow(atl_client: ATLClient) -> None:
    """Submit a Commitment, then an HA referencing the same job; verify both."""
    # 1. Freshness nonce from current STH
    sth = atl_client.fetch_sth()
    assert sth is not None, "ATL STH endpoint returned None"
    nonce = atl_client.derive_nonce(sth)
    assert len(nonce) == 32

    # 2. Mutually committed job_id
    r_nonce = secrets.token_bytes(16)
    o_nonce = secrets.token_bytes(16)
    job_id_committed = compute_job_id(r_nonce, o_nonce)
    assert len(job_id_committed) == 64  # SHA-256 hex

    # 3. JAC payload
    script_bytes = b"def run():\n    pass\n"
    script_hash = compute_script_hash(script_bytes)
    dataset_id = "test-ds-a7"

    jac = sign_jac(
        private_key=atl_client._private_key,
        job_id=job_id_committed,
        script_hash=script_hash,
        dataset_id=dataset_id,
        nonce=nonce,
        t_accept=int(time.time()),
    )
    assert "signature" in jac
    assert jac["job_id"] == job_id_committed

    # 4. Submit Commitment
    commitment_hash, commit_response = atl_client.sign_and_submit_commitment(
        job_id=job_id_committed,
        jac_payload_bytes=jac["payload"].encode(),
    )
    assert commit_response is not None, "Commitment submission failed"
    commit_leaf = commit_response["leaf_index"]
    commit_tree_size = commit_response["tree_size"]

    # 5. Submit HA referencing the same job_id_committed
    # Use a deterministic fake attestation for the test
    fake_attestation = b"a7-test-attestation-document-bytes"
    ok, ha_response = atl_client.submit_ha_entry(
        job_id=job_id_committed,
        tee_platform="aws-nitro",
        attestation_doc=fake_attestation,
        nonce=nonce,
    )
    assert ok is True
    assert ha_response is not None
    ha_leaf = ha_response["leaf_index"]
    ha_tree_size = ha_response["tree_size"]

    # 6. Ordering: Commitment came first in the log
    assert commit_leaf < ha_leaf, (
        f"Commitment leaf ({commit_leaf}) must precede HA leaf ({ha_leaf}) "
        f"for the same job"
    )
    assert commit_tree_size < ha_tree_size

    # 7. Both entries are retrievable
    commit_entry = atl_client.fetch_entry(commit_leaf)
    assert commit_entry is not None
    ha_entry = atl_client.fetch_entry(ha_leaf)
    assert ha_entry is not None


def test_commitment_with_bad_signature_is_rejected(atl_client: ATLClient) -> None:
    """The server must reject a Commitment whose in-entry CoordSignature does
    not verify against the registered coordinator pubkey. This guards the
    paper's offline-auditability property."""
    sth = atl_client.fetch_sth()
    assert sth is not None
    nonce = atl_client.derive_nonce(sth)

    r_nonce = secrets.token_bytes(16)
    o_nonce = secrets.token_bytes(16)
    job_id_committed = compute_job_id(r_nonce, o_nonce)

    # Build a JAC, but pass a garbage signature directly to the lower-level
    # submit method. Bypasses sign_and_submit_commitment so we control the sig.
    jac = sign_jac(
        private_key=atl_client._private_key,
        job_id=job_id_committed,
        script_hash="aa" * 32,
        dataset_id="test-ds",
        nonce=nonce,
    )
    import hashlib
    commitment_hash = hashlib.sha256(jac["payload"].encode()).digest()

    bad_signature = b"\x00" * 64
    ok, response = atl_client.submit_commitment_entry(
        job_id=job_id_committed,
        commitment_hash=commitment_hash,
        coord_signature=bad_signature,
    )
    assert ok is False, "Server accepted a Commitment with invalid signature"
    assert response is None
