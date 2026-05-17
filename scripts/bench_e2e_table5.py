"""End-to-end timing benchmark for TDSC Table 5 (sprint B6).

Drives N jobs through the full integrated pipeline and aggregates per-stage
wall-clock timings. This is the number that retires the "computed by
composition" admission in paper §7.5.

Prerequisites:
  - epsilon-atl pre-populated to target scale (cmd/seed --type=mixed --count=1540000)
  - coordinator pipeline running (job_fetcher, ai_agent, clone, executor workers)
  - enclave in sim mode (so jobs actually complete in this driver's timeout)
  - DATABASE_URL points at the target DB
  - ATL_ENABLED=true in the coordinator's env

Usage:
  python scripts/bench_e2e_table5.py --jobs 30 --timeout 300 --output results/table5.csv

Output:
  - per-job CSV with columns: job_id, total_ms, atl_sth_fetch_ms,
    commitment_submit_ms, build_validation_ms, get_public_key_ms,
    fetch_middleware_ms, zip_and_encrypt_ms, proxy_fetch_ms,
    enclave_round_trip_ms (and the enclave_internal sub-stages)
  - summary stats (mean / std / P50 / P95 / P99) printed to stderr
  - exit 0 iff every job completed and produced a populated
    commitment_receipt and ha_receipt

The script reuses the G1 job-insertion path (DB INSERT into job_requests)
since the coordinator has no HTTP submit API.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import secrets
import statistics
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

from shared.db import db


# Stage columns ordered for CSV / paper Table 5. Names match the keys produced
# by executor.execute() in step_timing.
STAGE_KEYS = [
    "build_validation_ms",
    "get_public_key_ms",
    "fetch_middleware_ms",
    "zip_and_encrypt_ms",
    "atl_sth_fetch_ms",
    "commitment_submit_ms",
    "proxy_fetch_ms",
    "enclave_round_trip_ms",
    "total_ms",
]


@dataclass
class JobTiming:
    job_id: str
    job_id_committed: Optional[str]
    status: str
    metrics: dict
    has_commitment: bool
    has_ha: bool

    @property
    def ok(self) -> bool:
        return self.status == "success" and self.has_commitment and self.has_ha


def _insert_job() -> str:
    job_id = f"BENCH-T5-{uuid.uuid4().hex[:10]}"
    researcher_nonce = secrets.token_bytes(16).hex()
    with db.get_session() as s:
        s.execute(
            text(
                """
                INSERT INTO job_requests (
                    job_id, workspace_id, user_id, status,
                    commit_sha, researcher_nonce, created_at, updated_at
                ) VALUES (
                    :job_id, :ws, :uid, 'pending', :sha, :nonce, NOW(), NOW()
                )
                """
            ),
            {
                "job_id": job_id, "ws": "ws-bench", "uid": "user-bench",
                "sha": "0" * 40, "nonce": researcher_nonce,
            },
        )
        s.commit()
    return job_id


def _poll(job_id: str, timeout: float) -> JobTiming:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with db.get_session() as s:
            row = s.execute(
                text(
                    """
                    SELECT status, job_id_committed, execution_metrics,
                           commitment_receipt, ha_receipt
                    FROM job_requests WHERE job_id = :job_id
                    """
                ),
                {"job_id": job_id},
            ).mappings().first()
        if row and row["status"] in ("success", "failed", "rejected"):
            metrics = {}
            if row["execution_metrics"]:
                try:
                    metrics = json.loads(row["execution_metrics"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return JobTiming(
                job_id=job_id,
                job_id_committed=row["job_id_committed"],
                status=row["status"],
                metrics=metrics,
                has_commitment=bool(row["commitment_receipt"]),
                has_ha=bool(row["ha_receipt"]),
            )
        time.sleep(1.0)
    return JobTiming(
        job_id=job_id, job_id_committed=None, status="timeout",
        metrics={}, has_commitment=False, has_ha=False,
    )


def _percentile(values: list, p: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    k = (len(values) - 1) * (p / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def _summarize(samples: list, key: str) -> dict:
    vals = [s.metrics.get(key) for s in samples if s.ok]
    vals = [v for v in vals if isinstance(v, (int, float))]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": statistics.fmean(vals),
        "std": statistics.stdev(vals) if len(vals) > 1 else 0.0,
        "p50": _percentile(vals, 50),
        "p95": _percentile(vals, 95),
        "p99": _percentile(vals, 99),
        "min": min(vals),
        "max": max(vals),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--output", default="results/table5.csv")
    args = parser.parse_args()

    print(f"[B6] Submitting {args.jobs} jobs for Table 5 measurement...", file=sys.stderr)
    submitted = [_insert_job() for _ in range(args.jobs)]
    for i, jid in enumerate(submitted):
        print(f"  {i:02d}: {jid}", file=sys.stderr)

    print(f"\n[B6] Polling for completion (timeout={args.timeout}s per job)...", file=sys.stderr)
    samples = []
    for i, jid in enumerate(submitted):
        t = _poll(jid, args.timeout)
        marker = "OK" if t.ok else f"SKIP ({t.status} commit={t.has_commitment} ha={t.has_ha})"
        print(f"  {i:02d} {marker} {jid}", file=sys.stderr)
        samples.append(t)

    # Per-job CSV
    import os
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["job_id", "job_id_committed", "status"] + STAGE_KEYS)
        for s in samples:
            row = [s.job_id, s.job_id_committed or "", s.status]
            for k in STAGE_KEYS:
                v = s.metrics.get(k)
                row.append(f"{v:.3f}" if isinstance(v, (int, float)) else "")
            w.writerow(row)
    print(f"\n[B6] Per-job CSV: {args.output}", file=sys.stderr)

    # Summary
    n_ok = sum(1 for s in samples if s.ok)
    print(f"\n[B6] {n_ok}/{len(samples)} jobs completed with full Commitment+HA pair", file=sys.stderr)
    print(f"\n[B6] Stage statistics (compliant jobs only):", file=sys.stderr)
    print(f"  {'stage':<28} {'n':>4} {'mean_ms':>10} {'std_ms':>10} {'p50_ms':>10} {'p95_ms':>10} {'p99_ms':>10}", file=sys.stderr)
    for k in STAGE_KEYS:
        st = _summarize(samples, k)
        if st["n"] == 0:
            print(f"  {k:<28} {0:>4}  (no samples)", file=sys.stderr)
            continue
        print(
            f"  {k:<28} {st['n']:>4} {st['mean']:>10.2f} {st['std']:>10.2f} "
            f"{st['p50']:>10.2f} {st['p95']:>10.2f} {st['p99']:>10.2f}",
            file=sys.stderr,
        )

    if n_ok != len(samples):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
