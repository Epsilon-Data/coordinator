"""Aggregate per-stage timings from successful jobs for latency analysis.

Reads `execution_metrics` JSON from `job_requests` plus per-step durations
from `job_logs`, then prints mean/median/P95/N per stage. Handles both the
flat stage names (build_validation_ms, enclave_round_trip_ms, …) and the
nested `proxy_detail.*` sub-stages emitted by the executor when the proxy
returns decomposed timings.

Usage:
    DB_URL="postgresql://…" WORKSPACE_ID="<uuid>" python scripts/bench_table4.py
"""

import json
import os
import statistics
import sys

import psycopg2

DB_URL = os.environ.get("DB_URL")
WORKSPACE_ID = os.environ.get("WORKSPACE_ID")

if not DB_URL:
    sys.exit("DB_URL environment variable is required")
if not WORKSPACE_ID:
    sys.exit("WORKSPACE_ID environment variable is required")

# Top-level stage names recorded directly in execution_metrics.
TOP_STAGES = [
    "build_validation_ms",
    "get_public_key_ms",
    "fetch_middleware_ms",
    "proxy_fetch_ms",
    "zip_and_encrypt_ms",
    "enclave_round_trip_ms",
    "total_ms",
]

# Proxy-path sub-stages emitted inside execution_metrics["proxy_detail"].
PROXY_SUB_STAGES = [
    "get_attestation_ms",
    "proxy_health_ms",
    "proxy_query_total_ms",
    "proxy_server_total_ms",
    "proxy_network_rtt_ms",
    "proxy_parse_ms",
    "proxy_hmac_verify_ms",
    "proxy_attestation_verify_ms",
    "proxy_sql_validate_ms",
    "proxy_db_query_ms",
    "proxy_encrypt_ms",
]


def pick(d, key):
    """Return d[key] as float if present and numeric, else None."""
    if d is None or key not in d:
        return None
    try:
        return float(d[key])
    except (TypeError, ValueError):
        return None


def main():
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id, execution_metrics, duration_seconds
                FROM job_requests
                WHERE workspace_id = %s AND status = 'success'
                ORDER BY created_at DESC
                """,
                (WORKSPACE_ID,),
            )
            jobs = cur.fetchall()
            print(f"Found {len(jobs)} successful jobs")

            collected = {name: [] for name in TOP_STAGES + PROXY_SUB_STAGES}
            for _job_id, metrics_json, _duration in jobs:
                if not metrics_json:
                    continue
                metrics = metrics_json if isinstance(metrics_json, dict) else json.loads(metrics_json)
                proxy_detail = metrics.get("proxy_detail") or {}
                for name in TOP_STAGES:
                    v = pick(metrics, name)
                    if v is not None:
                        collected[name].append(v)
                for name in PROXY_SUB_STAGES:
                    v = pick(proxy_detail, name)
                    if v is not None:
                        collected[name].append(v)

            # Fall back to job_logs step durations for any stage missing from metrics.
            cur.execute(
                """
                SELECT jl.step_type, jl.duration_ms
                FROM job_logs jl
                JOIN job_requests jr ON jl.job_id = jr.job_id
                WHERE jr.workspace_id = %s AND jl.duration_ms IS NOT NULL
                """,
                (WORKSPACE_ID,),
            )
            log_stages = {}
            for step, dur in cur.fetchall():
                log_stages.setdefault(step, []).append(float(dur))

    print("\n=== Per-stage timings ===")
    print(f"{'Stage':<36} {'Mean':>10} {'Median':>10} {'P95':>10} {'N':>5}")
    for stage in TOP_STAGES + PROXY_SUB_STAGES:
        values = collected[stage]
        if not values:
            continue
        vs = sorted(values)
        p95 = vs[min(len(vs) - 1, int(len(vs) * 0.95))]
        print(f"{stage:<36} {statistics.mean(values):>9.1f}ms {statistics.median(values):>9.1f}ms {p95:>9.1f}ms {len(values):>5}")

    if log_stages:
        print("\n=== Per-step durations from job_logs ===")
        for stage, values in log_stages.items():
            if not values:
                continue
            vs = sorted(values)
            p95 = vs[min(len(vs) - 1, int(len(vs) * 0.95))]
            print(f"{stage:<36} {statistics.mean(values):>9.1f}ms {statistics.median(values):>9.1f}ms {p95:>9.1f}ms {len(values):>5}")


if __name__ == "__main__":
    main()
