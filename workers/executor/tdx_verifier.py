"""
Coordinator-side Intel TDX quote verification.

Mirrors the Nitro verifier's role: cryptographically verify a TDX quote
(signature + PCK certificate chain) via the go-tdx-guest ``tdverify`` helper,
then confirm the quote's 64-byte REPORTDATA equals SHA-512 of the out-of-band
proof JSON the enclave returned. Verification is self-hosted -- PCK collateral
comes from Intel PCS; no third-party verification service is in the trust path.
"""
import base64
import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger("epsilon.executor")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# Repo root is two levels up from workers/executor: epsilon-cordinator/tdx/tdverify/tdverify
DEFAULT_TDVERIFY_BIN = os.path.abspath(os.path.join(_THIS_DIR, "..", "..", "tdx", "tdverify", "tdverify"))
TDVERIFY_BIN = os.getenv("TDVERIFY_BIN", DEFAULT_TDVERIFY_BIN)
TDVERIFY_TIMEOUT_SECONDS = int(os.getenv("TDVERIFY_TIMEOUT_SECONDS", "60"))
EXPECTED_MRTD = os.getenv("EXPECTED_MRTD")


@dataclass
class TdxVerificationResult:
    """Verification outcome for a TDX attestation, shaped like the Nitro result."""
    valid: bool = False
    syntax_valid: bool = False
    certificate_chain_valid: bool = False
    signature_valid: bool = False
    pcr_verified: bool = False
    output_verified: bool = False
    measurements: Dict[str, str] = field(default_factory=dict)  # mrtd, rtmr0..n
    report_data: Optional[str] = None
    module_id: str = "tdx"
    error: Optional[str] = None


def _run_tdverify(quote: bytes) -> Dict[str, Any]:
    """Invoke the go-tdx-guest verify helper and return its JSON verdict.

    The verdict is the JSON object on stdout; we slice from the first '{' to stay
    robust against any leading log lines a dependency might emit on stdout.
    """
    proc = subprocess.run(
        [TDVERIFY_BIN],
        input=quote,
        capture_output=True,
        timeout=TDVERIFY_TIMEOUT_SECONDS,
    )
    out = proc.stdout.decode(errors="replace")
    start = out.find("{")
    if start == -1:
        raise RuntimeError(
            f"tdverify emitted no JSON verdict (rc={proc.returncode}): "
            f"{(out or proc.stderr.decode(errors='replace'))[:500]}"
        )
    return json.loads(out[start:])


def verify_tdx_attestation(
    att_doc: Dict[str, Any],
    expected_mrtd: Optional[str] = EXPECTED_MRTD,
) -> TdxVerificationResult:
    """Verify a TDX attestation document produced by ``TdxAttestationService``.

    Args:
        att_doc: The attestation payload ``{"attestation": {...}, "proof": {...}}``.
        expected_mrtd: Expected TD image measurement (hex). When None, the MRTD is
            recorded but not enforced (pcr_verified stays False).
    """
    result = TdxVerificationResult()
    try:
        inner = att_doc.get("attestation", {}) if isinstance(att_doc, dict) else {}
        b64_quote = inner.get("attestation_document", "")
        if not b64_quote:
            result.error = "No attestation_document found in TDX attestation"
            return result
        quote = base64.b64decode(b64_quote)

        verdict = _run_tdverify(quote)
        result.syntax_valid = bool(verdict.get("syntax_valid"))
        # go-tdx-guest verifies signature and PCK chain together.
        quote_ok = bool(verdict.get("quote_verified"))
        result.certificate_chain_valid = quote_ok
        result.signature_valid = quote_ok
        result.report_data = verdict.get("report_data")

        mrtd = verdict.get("mrtd")
        if mrtd:
            result.measurements["mrtd"] = mrtd
        for i, rtmr in enumerate(verdict.get("rtmrs") or []):
            result.measurements[f"rtmr{i}"] = rtmr

        # pcr_verified: the TD image measurement (MRTD) matches the expected one.
        if expected_mrtd:
            result.pcr_verified = (mrtd == expected_mrtd.strip().lower())

        # output_verified: the verified quote's REPORTDATA equals SHA-512 of the
        # out-of-band canonical proof JSON, binding this exact execution to the quote.
        proof = att_doc.get("proof", {}) if isinstance(att_doc, dict) else {}
        proof_canonical = proof.get("proof_canonical")
        if quote_ok and proof_canonical and result.report_data:
            expected_report_data = hashlib.sha512(proof_canonical.encode()).hexdigest()
            result.output_verified = (expected_report_data == result.report_data.lower())

        result.valid = (
            result.syntax_valid
            and result.certificate_chain_valid
            and result.signature_valid
            and result.output_verified
        )
        if not result.valid and not result.error:
            result.error = verdict.get("error")
        return result
    except Exception as e:  # noqa: BLE001 - surface as a structured failure like Nitro
        result.error = str(e)
        return result
