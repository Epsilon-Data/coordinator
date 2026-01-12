# Epsilon Coordinator Architecture

## Overview

Epsilon Coordinator is a job processing system that securely executes user code in isolated enclaves. It uses a worker-based architecture where each worker handles a specific stage of the job pipeline.

## Job Flow

```
┌─────────┐    ┌────────┐    ┌────────┐    ┌─────────────┐    ┌───────────┐
│ pending │───▶│ queued │───▶│ cloned │───▶│ ai_approved │───▶│ completed │
└─────────┘    └────────┘    └────────┘    └─────────────┘    └───────────┘
     │              │             │               │
 job-fetcher    clone        ai-agent        executor
                worker        worker          worker
                                  │
                                  ▼
                            ┌─────────────┐
                            │ ai_rejected │
                            └─────────────┘
```

## Workers

| Worker | Polls Status | Sets Status | Responsibility |
|--------|-------------|-------------|----------------|
| `job-fetcher` | `pending` | `queued` | Marks new jobs for processing |
| `clone` | `queued` | `cloned` | Clones GitHub repository |
| `ai-agent` | `cloned` | `ai_approved` / `ai_rejected` | Security & compliance analysis |
| `executor` | `ai_approved` | `completed` / `failed` | Executes code in secure enclave |

## Directory Structure

```
epsilon-cordinator/
├── shared/                     # Shared utilities
│   ├── config.py              # Global configuration
│   ├── base_worker.py         # Base class for all workers
│   ├── job_logger.py          # Database logging
│   └── db/                    # Database models & repository
│
├── workers/
│   ├── job-fetcher/           # Simplest worker
│   │   └── job_fetcher.py
│   │
│   ├── clone/                 # Clones repositories (flat structure)
│   │   ├── clone_worker.py    # Entry point
│   │   └── services.py        # GitService, StorageManager
│   │
│   ├── ai-agent/              # AI security analysis
│   │   ├── ai_agent_worker.py
│   │   ├── analyzer.py        # CrewAI integration
│   │   ├── agents/            # AI agent definitions
│   │   └── tasks/             # AI task definitions
│   │
│   └── executor/              # Secure execution (flat structure)
│       ├── worker.py          # Entry point (ExecutorWorker)
│       ├── executor.py        # SecureExecutor
│       ├── services.py        # BuildValidator, ZipService, CryptoService
│       ├── clients.py         # EnclaveClient, MiddlewareClient
│       ├── factories.py       # EnclaveClientFactory, ExecutorFactory
│       ├── interfaces.py      # Abstract base classes
│       ├── models.py          # Pydantic data models
│       ├── exceptions.py      # Custom exceptions
│       ├── settings.py        # Configuration (Pydantic)
│       └── utils.py           # Logging decorators
│
└── tests/                     # Unit tests mirror structure
```

## Worker Details

### 1. Job Fetcher (`workers/job-fetcher/`)

**Purpose:** Moves jobs from `pending` to `queued` status.

**Flow:**
1. Poll database for `pending` jobs
2. Update status to `queued`
3. Log the transition

**Files:**
- `job_fetcher.py` - Single file, ~80 lines

---

### 2. Clone Worker (`workers/clone/`)

**Purpose:** Clones GitHub repositories to local storage.

**Flow:**
1. Poll database for `queued` jobs
2. Prepare storage directory
3. Clone repository using `git`
4. Validate repository structure
5. Update status to `cloned`

**Key Classes (in `services.py`):**
- `CloneWorker` - Main worker class
- `GitService` - Git operations wrapper
- `StorageManager` - File system operations

---

### 3. AI Agent Worker (`workers/ai-agent/`)

**Purpose:** Analyzes code for security risks and compliance.

**Flow:**
1. Poll database for `cloned` jobs
2. Run CrewAI analysis (analyzer + decision agents)
3. Check for PII, security vulnerabilities
4. Update status to `ai_approved` or `ai_rejected`

**Key Classes:**
- `AIAgentWorker` - Main worker class
- `analyze_repository()` - CrewAI orchestration
- `AnalyzerAgent` - Code analysis
- `DecisionAgent` - Approve/reject decision

---

### 4. Executor Worker (`workers/executor/`)

**Purpose:** Securely executes approved code in an enclave.

**Flow:**
1. Poll database for `ai_approved` jobs
2. Validate `build/build.yml` configuration
3. Get public key from enclave
4. Fetch encrypted CSV from middleware
5. Zip and encrypt build folder
6. Send to enclave for execution
7. Update status to `completed` or `failed`

**Key Classes (flat structure):**
- `ExecutorWorker` (`worker.py`) - Entry point
- `SecureExecutor` (`executor.py`) - Execution pipeline
- `EnclaveClient` (`clients.py`) - VSock communication
- `MiddlewareClient` (`clients.py`) - External data fetching
- `BuildValidator` (`services.py`) - Config validation
- `ZipService` (`services.py`) - Compression
- `CryptoService` (`services.py`) - Encryption

**Architecture Pattern:** Zero Trust
- Data encrypted at source (middleware)
- Only decrypted inside enclave
- Executor never sees plaintext data

## Database Tables

### `job_requests`
| Column | Type | Description |
|--------|------|-------------|
| `job_id` | string | Primary key |
| `workspace_id` | string | FK to workspaces |
| `status` | string | Current job status |
| `commit_sha` | string | Git commit |
| `error_message` | text | Error details if failed |
| `created_at` | datetime | Job creation time |
| `updated_at` | datetime | Last update time |

### `job_logs`
| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Primary key |
| `job_id` | string | FK to job_requests |
| `worker_name` | string | Which worker logged |
| `step_type` | string | Step identifier |
| `level` | string | info/warning/error |
| `message` | text | Log message |
| `progress` | int | 0-100 percentage |
| `created_at` | datetime | Log time |

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SHARED_STORAGE_PATH` | Yes | Path for cloned repos |
| `OPENAI_API_KEY` | Yes (ai-agent) | For CrewAI analysis |
| `AWS_KMS_KEY_ARN` | Production | KMS key for encryption |
| `ENCLAVE_CID` | Production | Nitro Enclave CID |
| `MIDDLEWARE_ENDPOINT_URL` | Yes (executor) | Data fetch endpoint |

## Running Workers

### Local Development

```bash
# Terminal 1: Job Fetcher
cd workers/job-fetcher
python job_fetcher.py

# Terminal 2: Clone Worker
cd workers/clone
python clone_worker.py

# Terminal 3: AI Agent
cd workers/ai-agent
python ai_agent_worker.py

# Terminal 4: Executor
cd workers/executor
python worker.py
```

### Docker

```bash
docker-compose up job-fetcher clone-worker ai-agent executor
```

## Logging

Two logging systems:

1. **Console/File Logging** - Python `logging` module
   - For debugging and monitoring
   - Outputs to stdout and rotating files

2. **Database Logging** - `JobLogger` class
   - For job tracking and user visibility
   - Stored in `job_logs` table
   - Includes progress percentage

```python
# Usage
from shared.job_logger import JobLogger

log = JobLogger("WorkerName")
log.info(job_id, "step_type", "Message", progress=50)
log.error(job_id, "step_type", "Error message", error=exception)
```

## Error Handling

Custom exception hierarchy in `workers/executor/exceptions.py`:

```
ExecutorError (base)
├── ValidationError         # Build config issues
├── ConfigurationError      # Settings issues
├── EncryptionError         # Crypto failures
└── ExecutorTimeoutError    # Operation timeouts
```

## Security

- **Zero Trust Architecture:** Data encrypted end-to-end
- **Enclave Execution:** Code runs in AWS Nitro Enclave
- **Hybrid Encryption:** AES-256-CBC + RSA-OAEP
- **Input Validation:** BuildValidator checks all inputs
- **No Plaintext Data:** Executor never sees decrypted CSV
