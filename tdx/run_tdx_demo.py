#!/usr/bin/env python3
"""
End-to-end TDX backend demo / smoke test.

Exercises the real coordinator path against a running TDX agent (main_tdx.py):
generate keypair -> encrypt a minimal researcher bundle -> execute in the TD ->
receive the TDX-quote attestation -> verify it (signature + PCK chain via the
self-hosted tdverify helper, and REPORTDATA == SHA-512(proof)). Prints the
execution output, the verification receipt, and the warm end-to-end latency
(the single number for the paper's TDX-vs-Nitro overhead row).

Run from the coordinator repo root, with the TDX agent already listening:

    TD_HOST=127.0.0.1 TD_PORT=5005 \\
    TDVERIFY_BIN="$PWD/tdx/tdverify/tdverify" \\
    PYTHONPATH=. python3 tdx/run_tdx_demo.py
"""
import io
import json
import sys
import time
import zipfile

from workers.executor.clients import EnclaveClientTDX
from workers.executor.tdx_verifier import verify_tdx_attestation

JOB_ID = "tdx-demo-001"

_MAIN_PY = '''\
import os

def main():
    print("Hello from the Epsilon TDX trust domain")
    csv_path = "generated/data.csv"
    if os.path.exists(csv_path):
        with open(csv_path) as f:
            print(f"rows (incl header): {len(f.readlines())}")
    else:
        print("no CSV provided")
    return {"result": "success"}

if __name__ == "__main__":
    print(main())
'''

_BUILD_YML = '''\
version: '1.0'
analysis:
  name: TDX demo
  description: TDX backend smoke test
  script_file: main.py
'''

_CSV = (
    "personal_info.first_name,personal_info.last_name\n"
    "John,Doe\n"
    "Jane,Smith\n"
)


def make_bundle() -> bytes:
    """Build the minimal researcher bundle the execute service expects."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("main.py", _MAIN_PY)
        zf.writestr("build.yml", _BUILD_YML)
        zf.writestr("generated/.gitkeep", "")
    return buffer.getvalue()


def main() -> int:
    client = EnclaveClientTDX()  # TD_HOST / TD_PORT from settings/env

    print("[1/4] Requesting keypair from TDX agent...")
    client.connect()  # probe the endpoint up front for a clear failure if it's down
    public_key, session_id = client.get_public_key(JOB_ID)

    print("[2/4] Encrypting bundle + CSV...")
    encrypted_zip = client.encrypt_zip_data(make_bundle(), public_key)
    encrypted_csv = client.encrypt_zip_data(_CSV.encode(), public_key)

    print("[3/4] Executing in the TD and collecting the TDX attestation...")
    started = time.time()
    success, output, attestation = client.send_encrypted_data_to_enclave(
        session_id, encrypted_zip, encrypted_csv
    )
    latency_ms = (time.time() - started) * 1000

    print("\n--- execution ---")
    print(f"success: {success}")
    print(f"output:\n{output}")
    print(f"warm end-to-end latency: {latency_ms:.1f} ms")

    if not success or attestation is None:
        print("\nFAILED: no successful execution / attestation", file=sys.stderr)
        return 1

    print("\n[4/4] Verifying the TDX quote (self-hosted)...")
    result = verify_tdx_attestation(attestation)
    receipt = {
        "valid": result.valid,
        "checks": {
            "syntax_valid": result.syntax_valid,
            "certificate_chain_valid": result.certificate_chain_valid,
            "signature_valid": result.signature_valid,
            "pcr_verified": result.pcr_verified,
            "output_verified": result.output_verified,
        },
        "measurements": result.measurements,
        "module_id": result.module_id,
        "error": result.error,
    }
    print(json.dumps(receipt, indent=2))

    return 0 if result.valid else 2


if __name__ == "__main__":
    sys.exit(main())
