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
