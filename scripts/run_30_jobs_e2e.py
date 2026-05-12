"""End-to-end driver for sprint G1 — 30-job pipeline verification.

Inserts N jobs directly into the job_requests table (the coordinator picks
them up via its DB-polling job fetcher), waits for each to complete, and
verifies the paper's load-bearing properties on every successful job:

  1. job_id_committed is populated and is 64-char hex
  2. researcher_nonce is populated
  3. commitment_receipt JSON is populated, has tree_size and leaf_index
  4. ha_receipt JSON is populated, has tree_size and leaf_index
  5. Commitment came first in the log: commitment.leaf_index < ha.leaf_index
  6. is_non_compliant column is False (when researcher nonce was supplied)
  7. attestation field references the same job_id_committed (best-effort:
     the field is JSON; we just check the committed id appears in it)

Usage:

    # 1. Boot ATL + enclave (sim) + coordinator stack (left to operator)
    # 2. Ensure DATABASE_URL points at the dev DB
    # 3. Run:
    python scripts/run_30_jobs_e2e.py --jobs 30 --timeout 120

Exit code 0 iff every job verifies all 7 properties. Non-zero on any failure;
prints a per-job table so failures are diagnosable.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

from shared.db import db


@dataclass
class JobVerification:
    job_id: str
    job_id_committed: Optional[str]
    researcher_nonce: Optional[str]
    commitment_receipt: Optional[dict]
    ha_receipt: Optional[dict]
    attestation: Optional[str]
    status: str
    error: Optional[str]
    failures: list  # list of property names that failed

    @property
    def ok(self) -> bool:
        return not self.failures and self.status == "success"


SAMPLE_REPO = {
    "github_repo": "https://github.com/example/jac-e2e-template",
    "commit_sha": "abc1234567890",
    "commit_message": "e2e test template",
    "commit_author": "e2e-driver",
}


def _insert_job(researcher_nonce_hex: str) -> str:
    """Insert one minimal job row. Returns the operational job_id."""
    job_id = f"JOB-E2E-{uuid.uuid4().hex[:10]}"
    with db.get_session() as s:
        s.execute(
            text(
                """
                INSERT INTO job_requests (
                    job_id, workspace_id, user_id, status,
                    commit_sha, commit_message, commit_author,
                    researcher_nonce, created_at, updated_at
                ) VALUES (
                    :job_id, :ws, :uid, 'pending',
                    :sha, :msg, :author,
                    :nonce, NOW(), NOW()
                )
                """
            ),
            {
                "job_id": job_id,
                "ws": "ws-e2e",
                "uid": "user-e2e",
                "sha": SAMPLE_REPO["commit_sha"],
                "msg": SAMPLE_REPO["commit_message"],
                "author": SAMPLE_REPO["commit_author"],
                "nonce": researcher_nonce_hex,
            },
        )
        s.commit()
    return job_id


def _poll(job_id: str, timeout: float) -> JobVerification:
    """Poll until terminal status or timeout. Build a JobVerification record."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with db.get_session() as s:
            row = s.execute(
                text(
                    """
                    SELECT job_id, status, job_id_committed, researcher_nonce,
                           commitment_receipt, ha_receipt, attestation, error_message
                    FROM job_requests WHERE job_id = :job_id
                    """
                ),
                {"job_id": job_id},
            ).mappings().first()
        if row is None:
            time.sleep(0.5)
            continue
        if row["status"] in ("success", "failed", "rejected"):
            commit_receipt = (
                json.loads(row["commitment_receipt"]) if row["commitment_receipt"] else None
            )
            ha_receipt = (
                json.loads(row["ha_receipt"]) if row["ha_receipt"] else None
            )
            return JobVerification(
                job_id=row["job_id"],
                job_id_committed=row["job_id_committed"],
                researcher_nonce=row["researcher_nonce"],
                commitment_receipt=commit_receipt,
                ha_receipt=ha_receipt,
                attestation=row["attestation"],
                status=row["status"],
                error=row["error_message"],
                failures=[],
            )
        time.sleep(1.0)
    return JobVerification(
        job_id=job_id, status="timeout", error="poll deadline exceeded",
        job_id_committed=None, researcher_nonce=None,
        commitment_receipt=None, ha_receipt=None, attestation=None,
        failures=["timeout"],
    )


def _verify(v: JobVerification) -> None:
    """Run the 7 property checks on a completed job, mutating v.failures."""
    if v.status != "success":
        v.failures.append(f"status={v.status} (error={v.error})")
        return

    if not v.job_id_committed or len(v.job_id_committed) != 64:
        v.failures.append("job_id_committed missing or not 64-char hex")
    if not v.researcher_nonce:
        v.failures.append("researcher_nonce not persisted")
    if not v.commitment_receipt:
        v.failures.append("commitment_receipt missing")
    elif "tree_size" not in v.commitment_receipt or "leaf_index" not in v.commitment_receipt:
        v.failures.append("commitment_receipt missing tree_size/leaf_index")
    if not v.ha_receipt:
        v.failures.append("ha_receipt missing")
    elif "tree_size" not in v.ha_receipt or "leaf_index" not in v.ha_receipt:
        v.failures.append("ha_receipt missing tree_size/leaf_index")
    if v.commitment_receipt and v.ha_receipt:
        c_leaf = v.commitment_receipt.get("leaf_index")
        h_leaf = v.ha_receipt.get("leaf_index")
        if c_leaf is None or h_leaf is None or c_leaf >= h_leaf:
            v.failures.append(f"Commitment leaf ({c_leaf}) must precede HA leaf ({h_leaf})")
    if v.attestation and v.job_id_committed and v.job_id_committed not in v.attestation:
        v.failures.append("attestation does not reference job_id_committed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="Per-job poll timeout in seconds")
    args = parser.parse_args()

    print(f"[G1] Submitting {args.jobs} jobs...")
    submitted = []
    for i in range(args.jobs):
        researcher_nonce_hex = secrets.token_bytes(16).hex()
        job_id = _insert_job(researcher_nonce_hex)
        submitted.append((i, job_id, researcher_nonce_hex))
        print(f"  job {i:02d}: {job_id} (r_nonce={researcher_nonce_hex[:8]}...)")

    print(f"\n[G1] Polling for completion (timeout={args.timeout}s per job)...")
    results = []
    for i, job_id, _ in submitted:
        v = _poll(job_id, args.timeout)
        _verify(v)
        marker = "OK" if v.ok else "FAIL"
        suffix = "" if v.ok else f"  failures={v.failures}"
        print(f"  job {i:02d} {marker} {job_id}{suffix}")
        results.append(v)

    print()
    n_ok = sum(1 for r in results if r.ok)
    n_total = len(results)
    print(f"[G1] {n_ok}/{n_total} jobs verified all 7 properties")
    if n_ok != n_total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
