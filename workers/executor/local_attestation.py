"""
Local attestation document generator for development/testing.

Produces structurally identical COSE_Sign1 attestation documents
to AWS Nitro Enclave NSM, but signed by a local ECDSA P-384 CA
instead of the AWS Nitro Attestation PKI.

The generated documents pass all syntactical validation and signature
verification — only the root CA differs from production.
"""

import base64
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import cbor2
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.x509.oid import NameOID, ExtensionOID
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

# Default paths for local CA keys
LOCAL_CA_DIR = os.environ.get("LOCAL_CA_DIR", "/tmp/epsilon-local-ca")

# Simulated PCR values (SHA-384 = 48 bytes)
# These match the published PCR registry format
SIMULATED_PCRS = {
    0: bytes.fromhex("000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001"),
    1: bytes.fromhex("000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000002"),
    2: bytes.fromhex("000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000003"),
}


def _ensure_local_ca() -> Tuple[ec.EllipticCurvePrivateKey, x509.Certificate,
                                 ec.EllipticCurvePrivateKey, x509.Certificate,
                                 ec.EllipticCurvePrivateKey, x509.Certificate]:
    """
    Generate or load a local 3-level CA hierarchy:
    Root CA → Intermediate CA → Leaf (enclave) cert.

    All use ECDSA P-384 to match AWS Nitro.
    Returns: (root_key, root_cert, intermediate_key, intermediate_cert, leaf_key, leaf_cert)
    """
    ca_dir = Path(LOCAL_CA_DIR)
    ca_dir.mkdir(parents=True, exist_ok=True)

    root_key_path = ca_dir / "root.key"
    root_cert_path = ca_dir / "root.pem"
    int_key_path = ca_dir / "intermediate.key"
    int_cert_path = ca_dir / "intermediate.pem"
    leaf_key_path = ca_dir / "leaf.key"
    leaf_cert_path = ca_dir / "leaf.pem"

    if all(p.exists() for p in [root_key_path, root_cert_path, int_key_path, int_cert_path, leaf_key_path, leaf_cert_path]):
        # Load existing
        root_key = serialization.load_pem_private_key(root_key_path.read_bytes(), password=None)
        root_cert = x509.load_pem_x509_certificate(root_cert_path.read_bytes())
        int_key = serialization.load_pem_private_key(int_key_path.read_bytes(), password=None)
        int_cert = x509.load_pem_x509_certificate(int_cert_path.read_bytes())
        leaf_key = serialization.load_pem_private_key(leaf_key_path.read_bytes(), password=None)
        leaf_cert = x509.load_pem_x509_certificate(leaf_cert_path.read_bytes())
        logger.info(f"[LOCAL-ATTESTATION] Loaded existing CA from {ca_dir}")
        return root_key, root_cert, int_key, int_cert, leaf_key, leaf_cert

    logger.info(f"[LOCAL-ATTESTATION] Generating local CA hierarchy in {ca_dir}")
    now = datetime.now(timezone.utc)

    # --- Root CA ---
    root_key = ec.generate_private_key(ec.SECP384R1(), default_backend())
    root_name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Epsilon Local Root CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Epsilon (Local Dev)"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "AU"),
    ])
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=2), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, key_encipherment=False, content_commitment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False
        ), critical=True)
        .sign(root_key, hashes.SHA384(), default_backend())
    )

    # --- Intermediate CA ---
    int_key = ec.generate_private_key(ec.SECP384R1(), default_backend())
    int_name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Epsilon Local Intermediate CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Epsilon (Local Dev)"),
    ])
    int_cert = (
        x509.CertificateBuilder()
        .subject_name(int_name)
        .issuer_name(root_name)
        .public_key(int_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1825))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, key_encipherment=False, content_commitment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False
        ), critical=True)
        .sign(root_key, hashes.SHA384(), default_backend())
    )

    # --- Leaf (enclave) certificate ---
    leaf_key = ec.generate_private_key(ec.SECP384R1(), default_backend())
    leaf_name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Epsilon Local Enclave"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Epsilon (Local Dev)"),
    ])
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(int_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(hours=6))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_encipherment=False, content_commitment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False
        ), critical=True)
        .sign(int_key, hashes.SHA384(), default_backend())
    )

    # Save all keys and certs
    for path, key in [(root_key_path, root_key), (int_key_path, int_key), (leaf_key_path, leaf_key)]:
        path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()
        ))
        os.chmod(path, 0o600)

    for path, cert in [(root_cert_path, root_cert), (int_cert_path, int_cert), (leaf_cert_path, leaf_cert)]:
        path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    # Also save root cert as DER for the trust-center to use
    (ca_dir / "root.der").write_bytes(root_cert.public_bytes(serialization.Encoding.DER))

    logger.info(f"[LOCAL-ATTESTATION] CA hierarchy generated in {ca_dir}")
    return root_key, root_cert, int_key, int_cert, leaf_key, leaf_cert


def generate_local_attestation(
    user_data: Optional[bytes] = None,
    nonce: Optional[bytes] = None,
    public_key: Optional[bytes] = None,
    module_id: Optional[str] = None,
    pcrs: Optional[Dict[int, bytes]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Generate a structurally valid COSE_Sign1 attestation document
    signed by a local ECDSA P-384 CA hierarchy.

    The document is identical in format to AWS Nitro NSM output:
    - CBOR Tag 18 (COSE_Sign1)
    - Protected header: {1: -35} (ES384)
    - Payload: CBOR map with module_id, digest, timestamp, pcrs, certificate, cabundle, user_data, nonce, public_key
    - Signature: ECDSA P-384 over Sig_structure

    Args:
        user_data: Custom data (e.g., JSON hash bundle) - max 512 bytes
        nonce: Anti-replay nonce - max 512 bytes
        public_key: Enclave's RSA public key - max 1024 bytes
        module_id: Enclave module ID (auto-generated if not provided)
        pcrs: PCR values (uses simulated defaults if not provided)

    Returns:
        Tuple of (success, result_dict)
        result_dict contains 'document' (base64 COSE_Sign1) on success
    """
    try:
        t0 = time.time()

        # Load or generate CA hierarchy
        root_key, root_cert, int_key, int_cert, leaf_key, leaf_cert = _ensure_local_ca()

        # Build attestation payload
        use_pcrs = pcrs or SIMULATED_PCRS
        use_module_id = module_id or f"i-local-{os.urandom(8).hex()}-enc{os.urandom(4).hex()}"

        payload_map = {
            "module_id": use_module_id,
            "digest": "SHA384",
            "timestamp": int(time.time() * 1000),
            "pcrs": use_pcrs,
            "certificate": leaf_cert.public_bytes(serialization.Encoding.DER),
            "cabundle": [
                int_cert.public_bytes(serialization.Encoding.DER),
                root_cert.public_bytes(serialization.Encoding.DER),
            ],
        }

        if user_data is not None:
            payload_map["user_data"] = user_data
        if nonce is not None:
            payload_map["nonce"] = nonce
        if public_key is not None:
            payload_map["public_key"] = public_key

        # Encode payload as CBOR
        payload_bytes = cbor2.dumps(payload_map)

        # Build protected header: {1: -35} = ES384
        protected_header = cbor2.dumps({1: -35})

        # Build COSE Sig_structure per RFC 8152
        sig_structure = cbor2.dumps([
            "Signature1",
            protected_header,
            b"",  # external_aad
            payload_bytes
        ])

        # Sign with leaf key (ECDSA P-384)
        der_signature = leaf_key.sign(sig_structure, ec.ECDSA(hashes.SHA384()))

        # Convert DER signature to raw (r || s) format for COSE
        r, s = utils.decode_dss_signature(der_signature)
        raw_signature = r.to_bytes(48, 'big') + s.to_bytes(48, 'big')

        # Build COSE_Sign1: Tag 18 [protected, unprotected, payload, signature]
        cose_sign1 = cbor2.CBORTag(18, [
            protected_header,
            {},  # unprotected header (empty)
            payload_bytes,
            raw_signature,
        ])

        # Encode final document
        document_bytes = cbor2.dumps(cose_sign1)

        elapsed_ms = round((time.time() - t0) * 1000, 2)

        logger.info(
            f"[LOCAL-ATTESTATION] Generated document: {len(document_bytes)}B, "
            f"payload={len(payload_bytes)}B, user_data={len(user_data) if user_data else 0}B, "
            f"elapsed={elapsed_ms}ms"
        )

        return True, {
            "document": base64.b64encode(document_bytes).decode(),
            "document_raw": document_bytes,
            "module_id": use_module_id,
            "timestamp": payload_map["timestamp"],
            "is_local": True,
            "elapsed_ms": elapsed_ms,
        }

    except Exception as e:
        logger.error(f"[LOCAL-ATTESTATION] Failed to generate: {e}", exc_info=True)
        return False, {
            "error": "LOCAL_ATTESTATION_FAILED",
            "message": str(e),
            "is_local": True,
        }


def get_local_root_cert_der() -> Optional[bytes]:
    """Get the local root CA certificate in DER format (for trust-center dev mode)."""
    root_der_path = Path(LOCAL_CA_DIR) / "root.der"
    if root_der_path.exists():
        return root_der_path.read_bytes()
    return None


def get_local_root_cert_pem() -> Optional[str]:
    """Get the local root CA certificate in PEM format."""
    root_pem_path = Path(LOCAL_CA_DIR) / "root.pem"
    if root_pem_path.exists():
        return root_pem_path.read_text()
    return None
