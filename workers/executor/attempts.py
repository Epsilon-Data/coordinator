"""Execution attempts and signed run outcomes.

A job can be executed more than once (a stale or orphaned job is re-queued).
Each execution is an attempt with its own identifier, written to the job row
before dispatch and sent to the enclave, which signs it together with the
platform job id. A signed record from one attempt therefore cannot stand in
for another attempt, or for another job.

The enclave also signs what happened to the script (outcome, exit code and,
for a failed run, the hash of the error text), so a failed run has a signed
record like a successful one.
"""
import hashlib
import re
import secrets
from enum import Enum
from typing import Any, Dict, Optional

# The enclave refuses a platform_job_id or attempt_id that does not match this
ENCLAVE_IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')

# Outcomes the enclave signs (epsilon-enclave interfaces/execute_interface.py)
OUTCOME_SUCCEEDED = 'succeeded'
SIGNED_OUTCOMES = frozenset({
    OUTCOME_SUCCEEDED, 'failed', 'timed_out', 'invalid_bundle', 'output_rejected', 'error',
})

# Signed fields copied into the verification receipt
EXECUTION_FIELDS = ('outcome', 'exit_code', 'error_hash', 'platform_job_id', 'attempt_id')


class AttemptBinding(str, Enum):
    """Result of checking a signed record against the attempt the job row expects."""
    BOUND = "bound"
    MISMATCH = "mismatch"  # the record belongs to another job or attempt
    UNBOUND_LEGACY = "unbound_legacy"  # the enclave signed no attempt (older enclave image)
    NO_EXPECTED_ATTEMPT = "no_expected_attempt"  # the job row has no attempt to compare with


def new_attempt_id() -> str:
    """Unpredictable identifier for one execution attempt (32 hex characters)."""
    return secrets.token_hex(16)


def enclave_identifier(value: Optional[str]) -> Optional[str]:
    """The value if the enclave accepts it as an identifier, else None (it is then not sent)."""
    if isinstance(value, str) and ENCLAVE_IDENTIFIER_PATTERN.fullmatch(value):
        return value
    return None


def check_attempt_binding(
    signed_fields: Dict[str, Any],
    expected_job_id: str,
    expected_attempt_id: Optional[str],
) -> AttemptBinding:
    """Compare the signed attempt_id and platform_job_id with the job row.

    expected_attempt_id must come from the job row written before dispatch,
    never from the record being checked. The caller must have verified the
    record first: signed_fields from an unverified record prove nothing.
    A record without platform_job_id is bound by its attempt_id alone (the
    job id is not sent when the enclave would refuse its format).
    """
    if not expected_attempt_id:
        return AttemptBinding.NO_EXPECTED_ATTEMPT

    signed_attempt = signed_fields.get('attempt_id')
    if not signed_attempt:
        return AttemptBinding.UNBOUND_LEGACY

    if not secrets.compare_digest(str(signed_attempt), expected_attempt_id):
        return AttemptBinding.MISMATCH
    signed_job = signed_fields.get('platform_job_id')
    if signed_job is not None and signed_job != expected_job_id:
        return AttemptBinding.MISMATCH
    return AttemptBinding.BOUND


def error_text_hash(error_text: str) -> str:
    """SHA-256 of a failed run's error text, as the enclave signs it in error_hash."""
    return hashlib.sha256(error_text.encode()).hexdigest()
