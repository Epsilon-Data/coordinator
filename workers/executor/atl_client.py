"""
ATL (Attestation Transparency Log) client for the Epsilon coordinator.

Submits attestation entries to the ATL after enclave execution and returns
inclusion receipts. Supports both High-Assurance (successful execution) and
Low-Assurance (failure) entry types.

The ATL is TEE-agnostic — this client constructs CBOR entries per the ATL
entry format specification (TDSC paper §4.2).
"""
import base64
import hashlib
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import cbor2
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

logger = logging.getLogger(__name__)

# Entry type constants (match ATL Go constants)
ENTRY_TYPE_HA = 1
ENTRY_TYPE_LA = 2
ENTRY_TYPE_CONFIG = 3
ENTRY_TYPE_COMMITMENT = 4

# SHA-256 commitment hash length in bytes (validated server-side).
COMMITMENT_HASH_LEN = 32


class ATLClient:
    """Client for submitting entries to the Attestation Transparency Log."""

    def __init__(
        self,
        atl_url: str,
        submitter_id: str,
        private_key: Ed25519PrivateKey,
    ):
        self.atl_url = atl_url.rstrip("/")
        self.submitter_id = submitter_id
        self._private_key = private_key
        self._client = httpx.Client(timeout=30.0)

    @classmethod
    def from_env(cls) -> Optional["ATLClient"]:
        """Create an ATLClient from environment variables.

        Required env vars (set ATL_ENABLED=true to activate):
            ATL_URL: Base URL of the ATL service (e.g., http://localhost:8080)
            ATL_SUBMITTER_ID: Coordinator identity for ATL submissions
            ATL_SIGNING_KEY_PATH: Path to Ed25519 private key (PEM, PKCS8)

        Returns None if ATL is not enabled or not fully configured.
        """
        enabled = os.environ.get("ATL_ENABLED", "false").lower() == "true"
        if not enabled:
            return None

        atl_url = os.environ.get("ATL_URL")
        if not atl_url:
            return None

        submitter_id = os.environ.get("ATL_SUBMITTER_ID", "coordinator-default")
        key_path = os.environ.get("ATL_SIGNING_KEY_PATH")
        if not key_path:
            logger.warning("[ATL] ATL_SIGNING_KEY_PATH not set, ATL integration disabled")
            return None

        try:
            with open(key_path, "rb") as f:
                private_key = serialization.load_pem_private_key(f.read(), password=None)
            if not isinstance(private_key, Ed25519PrivateKey):
                logger.error("[ATL] Key at %s is not Ed25519", key_path)
                return None
        except Exception as e:
            logger.error("[ATL] Failed to load signing key: %s", e)
            return None

        logger.info("[ATL] Initialized: url=%s submitter=%s", atl_url, submitter_id)
        return cls(atl_url=atl_url, submitter_id=submitter_id, private_key=private_key)

    def fetch_sth(self) -> Optional[Dict[str, Any]]:
        """Fetch the latest Signed Tree Head from the ATL.

        Returns the STH as a dict with keys: tree_size, root_hash, timestamp, signature.
        Returns None on failure.
        """
        try:
            resp = self._client.get(f"{self.atl_url}/v1/sth")
            resp.raise_for_status()
            return cbor2.loads(resp.content)
        except Exception as e:
            logger.error("[ATL] Failed to fetch STH: %s", e)
            return None

    def derive_nonce(self, sth: Dict[str, Any]) -> bytes:
        """Derive a freshness nonce from an STH: nonce = SHA256(CBOR(sth)).

        This binds the execution to a specific point in the ATL's history,
        preventing replay attacks (paper §3.4.2).
        """
        sth_cbor = cbor2.dumps(sth)
        return hashlib.sha256(sth_cbor).digest()

    def submit_ha_entry(
        self,
        job_id: str,
        tee_platform: str,
        attestation_doc: bytes,
        nonce: bytes,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Submit a High-Assurance (successful execution) entry to the ATL.

        Args:
            job_id: Mutually committed job identifier H(r || o)
            tee_platform: TEE platform identifier (e.g., "aws-nitro")
            attestation_doc: Raw hardware-signed attestation document bytes
            nonce: Freshness nonce (should be H(STH_current))

        Returns:
            (success, response_dict) where response_dict contains leaf_index,
            tree_size, root_hash, timestamp, receipt, and timing.
        """
        entry = {
            0: ENTRY_TYPE_HA,      # entry_type
            1: job_id,             # job_id
            2: tee_platform,       # tee_platform
            3: attestation_doc,    # attestation (raw bytes)
            4: nonce,              # nonce
            5: self.submitter_id,  # submitter_id
        }
        return self._submit_entry(entry)

    def submit_la_entry(
        self,
        job_id: str,
        error_class: str,
        error_detail: str,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Submit a Low-Assurance (failure) entry to the ATL.

        Args:
            job_id: Job identifier
            error_class: Error classification (e.g., "MemoryError", "timeout")
            error_detail: Human-readable error description

        Returns:
            (success, response_dict)
        """
        # Sign the failure claim with coordinator key
        claim = f"{job_id}:{error_class}:{error_detail}".encode()
        signature = self._private_key.sign(claim)

        entry = {
            0: ENTRY_TYPE_LA,      # entry_type
            1: job_id,             # job_id
            2: error_class,        # error_class
            3: error_detail,       # error_detail
            4: self.submitter_id,  # submitter_id
            5: signature,          # coordinator_signature
        }
        return self._submit_entry(entry)

    def submit_commitment_entry(
        self,
        job_id: str,
        commitment_hash: bytes,
        coord_signature: bytes,
        timestamp: Optional[int] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Submit a Commitment entry to the ATL.

        Maps to Go entry.CommitmentEntry (epsilon-atl/internal/entry/types.go):
            0: EntryType (= 4)
            1: JobID
            2: CommitmentHash (32 bytes, SHA-256 of full JAC payload)
            3: CoordSignature (Ed25519 over job_id_bytes || commitment_hash)
            4: SubmitterID
            5: Timestamp

        Args:
            job_id: Mutually committed H(researcher_nonce || coordinator_nonce)
            commitment_hash: SHA-256 of full JAC payload (exactly 32 bytes)
            coord_signature: Ed25519 signature over (job_id.encode() || commitment_hash)
            timestamp: Unix seconds (defaults to now)

        Returns:
            (success, response_dict). response_dict on success contains
            leaf_index, tree_size, root_hash, timestamp, receipt (COSE_Sign1 bytes),
            and timing breakdown.
        """
        if len(commitment_hash) != COMMITMENT_HASH_LEN:
            raise ValueError(
                f"commitment_hash must be {COMMITMENT_HASH_LEN} bytes (SHA-256), "
                f"got {len(commitment_hash)}"
            )

        if timestamp is None:
            timestamp = int(time.time())

        entry = {
            0: ENTRY_TYPE_COMMITMENT,
            1: job_id,
            2: commitment_hash,
            3: coord_signature,
            4: self.submitter_id,
            5: timestamp,
        }
        return self._submit_entry(entry)

    def sign_and_submit_commitment(
        self,
        job_id: str,
        jac_payload_bytes: bytes,
    ) -> Tuple[bytes, Optional[Dict[str, Any]]]:
        """Compute commitment_hash, sign it, submit. Convenience for executor.py Step 4b.

        The in-entry CoordSignature is verified server-side (epsilon-atl
        policy.validateCommitment) so the entry is auditable offline against
        the coordinator's published key.

        Args:
            job_id: Mutually committed job ID
            jac_payload_bytes: Serialized JAC payload — the preimage of commitment_hash.
                The full JAC stays held by the coordinator; only its hash is logged.

        Returns:
            (commitment_hash, response_dict). commitment_hash is always returned
            (caller may need it for the held JAC even on submission failure);
            response_dict is None if submission failed.
        """
        commitment_hash = hashlib.sha256(jac_payload_bytes).digest()
        signed = job_id.encode() + commitment_hash
        coord_signature = self._private_key.sign(signed)
        ok, response = self.submit_commitment_entry(
            job_id=job_id,
            commitment_hash=commitment_hash,
            coord_signature=coord_signature,
        )
        return commitment_hash, response if ok else None

    def _submit_entry(self, entry: dict) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Submit a CBOR entry to POST /v1/entries with coordinator signature."""
        try:
            entry_cbor = cbor2.dumps(entry)

            # Sign the entry body with coordinator Ed25519 key
            signature = self._private_key.sign(entry_cbor)
            sig_b64 = base64.b64encode(signature).decode()

            resp = self._client.post(
                f"{self.atl_url}/v1/entries",
                content=entry_cbor,
                headers={
                    "Content-Type": "application/cbor",
                    "X-Submitter-ID": self.submitter_id,
                    "X-Coordinator-Signature": sig_b64,
                },
            )

            if resp.status_code == 201:
                result = cbor2.loads(resp.content)
                logger.info(
                    "[ATL] Entry submitted: leaf=%d tree_size=%d",
                    result.get("leaf_index", -1),
                    result.get("tree_size", -1),
                )
                return True, result
            else:
                error_body = resp.text
                try:
                    error_body = cbor2.loads(resp.content).get("error", resp.text)
                except Exception:
                    pass
                logger.error(
                    "[ATL] Submission failed: status=%d error=%s",
                    resp.status_code,
                    error_body,
                )
                return False, None

        except Exception as e:
            logger.error("[ATL] Submission error: %s", e)
            return False, None

    def close(self):
        """Close the HTTP client."""
        self._client.close()
