# End-to-End Execution Flow

> Complete flow from researcher setup to verified output delivery.
> Covers all 13 repositories and the zero-trust output encryption architecture.

---

## Overview

```
Phase 1   SDK Setup         Researcher installs SDK, authenticates
Phase 2   Development       Init project, write code, test locally
Phase 3   Build             Package code + generate ephemeral key pair
Phase 4   Commit & Push     Git commit (unique hash), push to GitHub
Phase 5   Job Submission    ResearchWorkspace submits job with commit hash
Phase 6   EC2 Boot          Lambda detects pending job, starts EC2
Phase 7   Coordinator       Workers process job through pipeline
Phase 8   Enclave           Decrypt, execute, encrypt output, attest
Phase 9   Storage           Coordinator stores ciphertext in database
Phase 10  Retrieval         Researcher downloads, verifies, decrypts locally
Phase 11  Verification      Independent verification via Trust Center
```

---

## Phase 1: SDK Setup

**Repos:** `sdk-epsilon`

```
Researcher machine:
  $ pip install epsilon-sdk
  $ epsilon login
      │
      ├── Opens browser → Keycloak login page
      ├── Researcher authenticates (username/password or SSO)
      ├── Keycloak returns JWT (access_token + refresh_token)
      └── Stored at ~/.epsilon_sdk/credentials.ini

  $ epsilon datasets
      │
      └── GET /api/datasets (with JWT)
          Returns list of available datasets with metadata
```

---

## Phase 2: Development

**Repos:** `sdk-epsilon`, `api`, `go-packages` (Atlas)

```
  $ epsilon init <dataset_id>
      │
      ├── GET /api/datasets/<id>/archetype
      │   Returns archetype definition (tree structure mapping columns)
      │
      ├── Generate models.py from archetype
      │   (Python dataclass matching the archetype schema)
      │
      ├── Generate data.csv with dummy data
      │   (Synthetic rows matching column types, for local testing)
      │
      └── Create project structure:
          my-project/
          ├── main.py              # Researcher writes analysis here
          ├── models.py            # Auto-generated from archetype
          ├── data.csv             # Dummy data for local testing
          └── requirements.txt     # Python dependencies

  $ epsilon run
      │
      ├── Execute main.py locally with dummy data.csv
      ├── Researcher iterates: edit main.py → epsilon run → check output
      └── No real data involved, no network calls
```

---

## Phase 3: Build

**Repos:** `sdk-epsilon`

```
  $ epsilon build
      │
      ├── Validate project structure
      │   ├── main.py exists
      │   ├── requirements.txt exists
      │   └── models.py exists (from init)
      │
      ├── Generate ephemeral RSA key pair (NEW — zero-trust output)
      │   ├── researcher_private_key → stored locally (~/.epsilon_sdk/keys/<build_id>/)
      │   ├── researcher_public_key  → included in build/
      │   └── Key pair is unique to THIS build
      │
      ├── Generate build.yml manifest:
      │   version: '1.0'
      │   analysis:
      │     script_file: main.py
      │     requirements: requirements.txt
      │   datasets:
      │     - dataset_id: <uuid>
      │       archetype_id: <uuid>
      │   privacy:
      │     epsilon: 1.0
      │   execution:
      │     environment: enclave
      │     timeout: 300
      │   output:
      │     researcher_public_key: <PEM>
      │     researcher_key_hash: <SHA-256 of public key>
      │
      └── Create build/ folder:
          build/
          ├── build.yml               # Manifest with key hash
          ├── main.py                 # Analysis script
          ├── requirements.txt        # Dependencies
          ├── researcher_pubkey.pem   # Ephemeral public key
          └── generated/
              └── models.py           # Archetype schema

  Key property:
    If researcher modifies code → must re-run epsilon build
    Each build generates a NEW key pair
    Old private key is replaced (forward secrecy)
```

---

## Phase 4: Commit & Push

**Repos:** researcher's own GitHub repo

```
  $ git add .
  $ git commit -m "analysis v3"
      │
      └── Git generates unique commit hash: abc123def456...
          This hash is an immutable binding:
            commit_hash → {main.py, build.yml, researcher_pubkey.pem}
          Changing ANY file = different commit hash

  $ git push origin main
```

---

## Phase 5: Job Submission

**Repos:** `ResearchWorkspace`

```
Researcher opens ResearchWorkspace (web app):
  │
  ├── Authenticate via GitHub OAuth
  │
  ├── Select workspace
  │
  ├── Submit job request:
  │   {
  │     github_repo:    "researcher/my-analysis",
  │     github_branch:  "main",
  │     commit_sha:     "abc123def456...",   ← specific commit
  │     workspace_id:   "<uuid>",
  │     dataset_id:     "<uuid>"
  │   }
  │
  └── Stored in PostgreSQL:
      INSERT INTO job_requests (
        job_id, github_repo, github_branch, commit_sha,
        workspace_id, user_id, status, created_at
      ) VALUES (..., 'pending', NOW())
```

---

## Phase 6: EC2 Boot

**Repos:** `epsilon-infra`, `ResearchWorkspace` (Lambda)

```
Lambda (runs every 2 minutes):
  │
  ├── SELECT * FROM job_requests WHERE status = 'pending'
  │
  ├── If pending jobs exist:
  │   └── Start EC2 instance (Nitro Enclave-enabled)
  │
  └── EC2 boots:
      │
      ├── systemd: epsilon-enclave.service
      │   └── nitro-cli run-enclave
      │       --eif-path epsilon.eif
      │       --cpu-count 2 --memory 4096 --enclave-cid 18
      │
      │   Inside enclave:
      │     ├── Generate RSA-2048 keypair (in-memory, for INPUT decryption)
      │     │   private_key → enclave memory (NEVER leaves)
      │     │   public_key  → available via VSock:5005
      │     └── Start VSock server on port 5005
      │
      ├── systemd: epsilon-gvproxy.service
      │   └── VSock networking bridge
      │
      ├── systemd: epsilon-coordinator.service
      │   └── docker compose up -d
      │       ├── job_fetcher   (WORKER_MODE=fetcher)
      │       ├── clone_worker  (WORKER_MODE=clone)
      │       └── executor      (WORKER_MODE=executor)
      │
      └── systemd: epsilon-idle-shutdown.timer
          └── Check every 2 min; shutdown EC2 after 10 min idle
```

---

## Phase 7: Coordinator Pipeline

**Repos:** `epsilon-cordinator`

### Step 7a: JobFetcher (pending → queued)

```
workers/job_fetcher/

  Poll loop (every POLLING_INTERVAL seconds):
    │
    ├── SELECT * FROM job_requests WHERE status = 'pending' LIMIT 1
    │
    ├── UPDATE status = 'queued', worker_id = '<this_worker>'
    │
    └── Job is now claimed by this EC2 instance
```

### Step 7b: CloneWorker (queued → cloned)

```
workers/clone/clone_worker.py

  Poll loop:
    │
    ├── SELECT * FROM job_requests WHERE status = 'queued' LIMIT 1
    │
    ├── git clone <github_repo>
    │   └── Clones to shared_storage/repositories/<job_id>/
    │
    ├── git checkout <commit_sha>         ← SPECIFIC COMMIT
    │   This ensures the EXACT code that was built is used
    │   commit_sha binds to: {main.py, build.yml, researcher_pubkey.pem}
    │
    ├── Validate repository structure
    │   └── get_repo_info() → {commit_hash, branch, files_count}
    │
    └── UPDATE status = 'cloned' (or 'ai_approved' if AI disabled)
        Store repo_path, repo_metadata
```

### Step 7c: AIAgent (cloned → ai_approved) — Optional

```
workers/ai_agent/

  If config.ai_agent_enabled:
    │
    ├── CrewAI framework + GPT-4:
    │   ├── PolicyAgent:   Evaluate governance policies
    │   ├── AnalyzerAgent: Scan code for risks (PII leaks, forbidden ops)
    │   └── DecisionAgent: Approve/reject with confidence score
    │
    ├── Output: {approved: bool, confidence: float, reasoning: string}
    │   Stored: shared_storage/ai_analysis/<job_id>/analysis_result.json
    │
    └── UPDATE status = 'ai_approved' or 'ai_rejected'
```

### Step 7d: Executor (ai_approved → success/failed)

```
workers/executor/executor.py — SecureExecutor.execute()

  Step 1: Validate build
    │
    ├── BuildValidator validates build/ folder:
    │   ├── build.yml exists and is valid YAML
    │   ├── script_file (main.py) exists
    │   ├── requirements.txt exists
    │   ├── datasets section valid
    │   └── researcher_pubkey.pem exists (NEW — zero-trust)
    │
    └── Parse BuildConfig: {script_file, datasets[], epsilon, timeout}

  Step 2: Get enclave public key
    │
    ├── VSock:5005 → get_public_key request
    │
    └── Enclave returns:
        ├── public_key (RSA-2048 PEM, for INPUT encryption)
        └── session_id (for this execution session)

  Step 3: Fetch encrypted data from middleware
    │
    ├── POST to middleware Lambda:
    │   {dataset_id, archetype_id, public_key, job_id, workspace_id}
    │
    └── Middleware:
        ├── Authenticate via Keycloak
        ├── Query custodian's database using archetype mapping
        ├── Generate CSV from query results
        ├── Encrypt CSV with enclave's public key (RSA+AES)
        └── Return encrypted_csv (base64)

  Step 4: Zip and encrypt code bundle
    │
    ├── Zip build/ folder → compressed bytes
    │
    └── Encrypt zip with enclave's public key:
        [encrypted_aes_key(256B)][IV(16B)][AES-CBC ciphertext]

  Step 5: Send to enclave
    │
    ├── VSock:5005 → execute_script_rsa_hybrid request:
    │   {
    │     session_id:             "<from step 2>",
    │     encrypted_zip:          "<from step 4>",
    │     encrypted_csv:          "<from step 3>",
    │     researcher_public_key:  "<from build/researcher_pubkey.pem>"  (NEW)
    │   }
    │
    └── Wait for response...
```

---

## Phase 8: Enclave Execution

**Repos:** `epsilon-enclave`

```
Inside Nitro Enclave (CID 18, isolated memory, no network, no disk):

  Step 8a: Decrypt inputs
    │
    ├── RSA-OAEP decrypt → AES key (from encrypted zip header)
    ├── AES-256-CBC decrypt → zip bytes
    ├── Extract zip → main.py, requirements.txt, models.py, researcher_pubkey.pem
    │
    ├── RSA-OAEP decrypt → AES key (from encrypted CSV header)
    └── AES-256-CBC decrypt → data.csv (REAL data, first time in plaintext)

  Step 8b: Execute
    │
    ├── pip install -r requirements.txt
    ├── Inject data.csv into execution directory
    ├── Execute main.py in sandboxed subprocess (timeout 300s)
    └── Capture stdout → raw_output

  Step 8c: Encrypt output (ZERO TRUST — no KMS)
    │
    ├── Load researcher_public_key from extracted build/
    │
    ├── Generate random data key:
    │   data_key = os.urandom(32)     # AES-256, exists ONLY in enclave RAM
    │
    ├── Encrypt output:
    │   iv = os.urandom(12)           # 96-bit GCM nonce
    │   encrypted_output = AES-256-GCM(raw_output, data_key, iv)
    │   # GCM = confidentiality + integrity (auth tag)
    │
    ├── Wrap data key for researcher:
    │   wrapped_key = RSA-OAEP-SHA256(data_key, researcher_public_key)
    │   # ONLY researcher's private key can unwrap
    │
    ├── Compute output hash:
    │   output_hash = SHA-256(raw_output)
    │
    ├── Request attestation from /dev/nsm (Nitro hardware):
    │   user_data = CBOR({
    │     "job_id":              "<uuid>",
    │     "commit_hash":         "<commit_sha from build>",
    │     "output_hash":         "<SHA-256 of raw output>",
    │     "researcher_key_hash": "<SHA-256 of researcher public key>",
    │     "timestamp":           <unix_ms>
    │   })
    │
    │   /dev/nsm returns COSE_Sign1:
    │     ├── Signed by Nitro hypervisor (ECDSA P-384)
    │     ├── Contains PCR0 (enclave image hash), PCR1, PCR2
    │     ├── Contains certificate chain → AWS Nitro Root CA
    │     └── user_data embedded (includes key hash + output hash)
    │
    └── Zeroize from memory: data_key, raw_output, data.csv

  Step 8d: Return via VSock
    │
    └── Send to coordinator:
        {
          output:                  encrypted_output (AES-256-GCM blob),
          wrapped_key_researcher:  RSA-OAEP wrapped data key,
          iv:                      12 bytes (public, useless without key),
          attestation:             COSE_Sign1 signed by Nitro hardware
        }

        Coordinator receives ONLY ciphertext.
        No data key. No private key. No KMS. Cannot decrypt.
```

---

## Phase 9: Storage

**Repos:** `epsilon-cordinator`

```
workers/executor/worker.py — process_job()

  Coordinator:
    │
    ├── Verify attestation (server-side, optional integrity check):
    │   └── epsilon_verifier.verify_attestation()
    │       ├── COSE_Sign1 signature valid?
    │       ├── Certificate chain → AWS Root?
    │       └── PCR0 matches expected?
    │
    ├── Generate verification_receipt (JSON, stored alongside)
    │
    └── UPDATE job_requests SET
          status = 'success',
          execution_result = encrypted_output,    -- ciphertext
          wrapped_key_researcher = <blob>,         -- RSA-OAEP wrapped
          output_iv = <blob>,                      -- AES-GCM nonce
          attestation = <COSE_Sign1>,              -- Nitro-signed
          verification_receipt = <JSON>,            -- server-side check
          enclave_version = '1.0.0',
          enclave_pcr0 = '<hex>',
          completed_at = NOW()
        WHERE job_id = '<uuid>';

  All stored values are ciphertext or signed blobs.
  Database compromise → attacker gets ciphertext only.
  AWS admin compromise → attacker gets ciphertext only.
  No decryption key exists anywhere in AWS.
```

---

## Phase 10: Researcher Retrieves Output

**Repos:** `sdk-epsilon`, `epsilon-attestation-verifier`

```
  $ epsilon results <job_id>
      │
      ├── Download encrypted blobs from API:
      │   GET /api/jobs/<job_id>/output
      │   Authorization: Bearer <keycloak_jwt>
      │   Returns: {encrypted_output, wrapped_key_researcher, iv, attestation}
      │
      ├── Verify attestation FIRST (before any decryption):
      │   ├── Parse COSE_Sign1 attestation document
      │   ├── Verify certificate chain → AWS Nitro Root CA
      │   ├── Verify COSE signature (ECDSA P-384)
      │   ├── Extract PCR0 → compare with published value
      │   │   (from pcr-registry.json on GitHub)
      │   ├── Verify job_id matches
      │   ├── Verify commit_hash matches the commit they submitted
      │   └── Verify researcher_key_hash == SHA-256(my_public_key)
      │       ┌────────────────────────────────────────────────────┐
      │       │ CRITICAL CHECK                                     │
      │       │ Detects key substitution attack.                   │
      │       │ If coordinator replaced the public key,            │
      │       │ the hash won't match → ABORT.                      │
      │       │ Coordinator can't forge this — it's inside         │
      │       │ the Nitro-signed attestation document.             │
      │       └────────────────────────────────────────────────────┘
      │
      │   If ANY check fails → ABORT, report tampering
      │
      ├── Decrypt output (locally, no AWS calls):
      │   ├── Unwrap data key:
      │   │   data_key = RSA-OAEP-Decrypt(wrapped_key, my_private_key)
      │   ├── Decrypt output:
      │   │   raw_output = AES-256-GCM-Decrypt(encrypted_output, data_key, iv)
      │   │   (GCM auth tag verified automatically)
      │   └── Verify integrity:
      │       SHA-256(raw_output) == attestation.user_data.output_hash
      │
      ├── Display output to researcher
      │
      └── Cleanup: zeroize data_key, delete ephemeral private key
```

---

## Phase 11: Independent Verification

**Repos:** `epsilon-trust-center`, `epsilon-attestation-verifier`

```
Trust Center (public web app):
  │
  ├── Anyone can verify any job's attestation
  │
  ├── Server-side verification:
  │   └── Pre-computed receipt stored in database at execution time
  │
  ├── Client-side verification (in-browser):
  │   └── @aspect-data/nitro-verify npm package
  │       ├── Parse COSE_Sign1 in browser
  │       ├── Verify certificate chain
  │       ├── Verify signature
  │       └── Display PCR values, timestamp, user_data
  │
  └── Three-panel layout:
      ├── Panel 1: Job details + status
      ├── Panel 2: Attestation document breakdown
      └── Panel 3: React Flow trust chain visualization
```

---

## What Each Component Sees

```
┌─────────────────────┬───────────────┬───────────────┬──────────────────────────────┐
│ Component           │ Sees output?  │ Sees data key?│ Why                          │
├─────────────────────┼───────────────┼───────────────┼──────────────────────────────┤
│ Enclave             │ YES           │ YES (briefly) │ Generates both, then zeroizes│
│ Coordinator         │ NO            │ NO            │ No private key, no KMS       │
│ Database            │ NO            │ NO            │ Stores ciphertext only       │
│ API server          │ NO            │ NO            │ Serves ciphertext only       │
│ AWS KMS             │ NO            │ NO            │ Not involved in output path  │
│ AWS Account Admin   │ NO            │ NO            │ No key exists in AWS         │
│ Middleware Lambda   │ NO            │ NO            │ Handles input data only      │
│ ResearchWorkspace   │ NO            │ NO            │ Job submission only          │
│ Keycloak            │ NO            │ NO            │ Identity only                │
│ Researcher          │ YES           │ YES           │ Holds ephemeral private key  │
└─────────────────────┴───────────────┴───────────────┴──────────────────────────────┘
```

---

## Security Properties

| Property | Mechanism |
|---|---|
| Output confidentiality | AES-256-GCM + researcher's public key |
| Key independence from AWS | Data key generated in enclave, wrapped locally |
| Forward secrecy | New ephemeral key pair per `epsilon build` |
| Enclave identity | PCR0/1/2 in Nitro-signed attestation |
| Recipient binding | SHA-256(public_key) in attestation user_data |
| Output integrity | GCM auth tag + SHA-256 in attestation |
| Key substitution detection | researcher_key_hash in attestation |
| Code-output binding | commit_hash in attestation user_data |
| Replay protection | nonce + job_id + timestamp in attestation |
| Admin bypass prevention | No decryption key exists in AWS |

---

## Repository Involvement by Phase

| Phase | Repositories |
|-------|-------------|
| 1. SDK Setup | `sdk-epsilon` |
| 2. Development | `sdk-epsilon`, `api`, `go-packages` |
| 3. Build | `sdk-epsilon` |
| 4. Commit & Push | researcher's GitHub repo |
| 5. Job Submission | `ResearchWorkspace` |
| 6. EC2 Boot | `epsilon-infra`, `ResearchWorkspace` (Lambda) |
| 7. Coordinator | `epsilon-cordinator` |
| 8. Enclave | `epsilon-enclave` |
| 9. Storage | `epsilon-cordinator` |
| 10. Retrieval | `sdk-epsilon`, `epsilon-attestation-verifier` |
| 11. Verification | `epsilon-trust-center`, `epsilon-attestation-verifier` |
