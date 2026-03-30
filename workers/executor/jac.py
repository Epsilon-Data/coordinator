"""
Job Acceptance Commitment (JAC) — non-repudiable proof that the coordinator
accepted a job for execution.

The JAC is Ed25519-signed by the coordinator and returned to the researcher
before execution begins. If the researcher holds a valid JAC but no
corresponding ATL entry appears within the MMD window, this constitutes
cryptographic evidence of suppression (TDSC paper §3.4.1).

JAC = Sign_K_coord(job_id || script_hash || dataset_id || nonce || t_accept)
"""
import hashlib
import json
import time
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def compute_job_id(researcher_nonce: bytes, operator_nonce: bytes) -> str:
    """Compute mutually committed job ID: H(r || o).

    Neither party can unilaterally control the job ID:
    the researcher cannot predict o, and the operator cannot predict r.
    """
    combined = researcher_nonce + operator_nonce
    return hashlib.sha256(combined).hexdigest()


def compute_context_hash(
    job_id: str,
    commit_sha: str,
    archetype_id: str,
    dataset_id: str,
) -> str:
    """Compute context commitment hash.

    context_hash = H(job_id || commit_sha || archetype_id || dataset_id)

    This binds the ATL entry wrapper metadata to the hardware-signed
    attestation (TDSC paper §3.5). A verifier can recompute this hash
    from the entry metadata and compare it to the value inside the
    hardware-signed user_data.
    """
    combined = f"{job_id}|{commit_sha}|{archetype_id}|{dataset_id}".encode()
    return hashlib.sha256(combined).hexdigest()


def sign_jac(
    private_key: Ed25519PrivateKey,
    job_id: str,
    script_hash: str,
    dataset_id: str,
    nonce: bytes,
    t_accept: Optional[int] = None,
) -> Dict[str, Any]:
    """Sign a Job Acceptance Commitment.

    Args:
        private_key: Coordinator's Ed25519 signing key
        job_id: Mutually committed job ID = H(r || o)
        script_hash: SHA-256 of the submitted script
        dataset_id: Dataset identifier
        nonce: Freshness nonce = H(STH_current)
        t_accept: Acceptance timestamp (Unix seconds, defaults to now)

    Returns:
        JAC dict containing the signed commitment and all bound fields.
    """
    if t_accept is None:
        t_accept = int(time.time())

    # Construct the payload to sign
    payload = f"{job_id}|{script_hash}|{dataset_id}|{nonce.hex()}|{t_accept}".encode()
    signature = private_key.sign(payload)

    return {
        "job_id": job_id,
        "script_hash": script_hash,
        "dataset_id": dataset_id,
        "nonce": nonce.hex(),
        "t_accept": t_accept,
        "signature": signature.hex(),
        "payload": payload.decode(),
    }


def compute_script_hash(script_bytes: bytes) -> str:
    """Compute SHA-256 hash of a script."""
    return hashlib.sha256(script_bytes).hexdigest()
