# Epsilon Executor Worker

A robust, scalable, and secure execution worker for the Epsilon system that processes job execution requests using AWS Nitro Enclaves.

## Architecture Overview

```
┌─────────────────────┐       ┌──────────────────┐       ┌─────────────────┐
│   PostgreSQL DB     │──────>│  Executor Worker │──────>│   PostgreSQL DB │
│ (ai_approved jobs)  │       │    (EC2 Host)    │       │ (completed/fail)│
└─────────────────────┘       └────────┬─────────┘       └─────────────────┘
                                       │ vsock
                             ┌─────────┴─────────┐
                             │  Nitro Enclave    │
                             │ • KMS Decryption  │
                             │ • Bundle Extract  │
                             │ • Script Execute  │
                             │ • Secure Cleanup  │
                             └───────────────────┘
```

### Flat Module Architecture

The codebase uses a flat structure for maintainability and testability:

## Components

### 1. **Executor Worker** (`worker.py`)
- Polls database for `ai_approved` jobs
- Manages Nitro Enclave lifecycle
- Sends encrypted scripts/data to enclave
- Updates job status in database

### 2. **Secure Executor** (`executor.py`)
- Orchestrates the execution pipeline
- Validates build configuration
- Coordinates encryption and enclave communication

### 3. **Clients** (`clients.py`)
- `EnclaveClient` - vsock communication with enclave
- `MiddlewareClient` - External data fetching
- Hybrid encryption (RSA + AES-256-CBC)

### 4. **Services** (`services.py`)
- `BuildValidator` - Validates build.yml configuration
- `ZipService` - Creates encrypted ZIP bundles
- `CryptoService` - Handles hybrid encryption

### 5. **Models** (`models.py`)
- Pydantic models for type-safe data handling
- `BuildConfig`, `ExecutionResult`, `EnclaveResponse`

### 6. **Factories** (`factories.py`)
- Dependency injection for testability
- `EnclaveClientFactory`, `ExecutorFactory`

## Setup

### Prerequisites

1. **EC2 Instance Requirements**:
   - Nitro-based instance (e.g., m5.xlarge)
   - Enable Nitro Enclaves: `sudo amazon-linux-extras install aws-nitro-enclaves-cli`
   - IAM role with KMS permissions

2. **KMS Key Policy**:
   ```json
   {
     "Sid": "Enable enclave decrypt",
     "Effect": "Allow",
     "Principal": {
       "AWS": "arn:aws:iam::ACCOUNT:role/EC2-Role"
     },
     "Action": "kms:Decrypt",
     "Condition": {
       "StringEqualsIgnoreCase": {
         "kms:RecipientAttestation:ImageSha384": "PCR0_VALUE"
       }
     }
   }
   ```

### Building Enclave Image

1. Create enclave Docker image:
   ```bash
   cd enclave_app
   docker build -t executor-enclave .
   ```

2. Build enclave image file:
   ```bash
   nitro-cli build-enclave \
     --docker-uri executor-enclave \
     --output-file /opt/enclaves/executor.eif
   ```

3. Note the PCR0 value and update KMS key policy

### Running the Worker

1. **With Docker Compose**:
   ```bash
   docker-compose up executor
   ```

2. **Standalone**:
   ```bash
   python -m workers.executor.worker
   ```

## Environment Variables

- `DATABASE_URL`: PostgreSQL connection string
- `MIDDLEWARE_ENDPOINT_URL`: Endpoint for encrypted data fetching
- `USE_LOCAL_ENCLAVE`: Use local mock enclave for development
- `ENCLAVE_CID`: Nitro Enclave CID (production)
- `ENCLAVE_PORT`: vsock port (default: 5000)

## Local Development

When running locally without Nitro Enclaves, set `USE_LOCAL_ENCLAVE=true` for mock execution:

```python
# Mock execution output example
Mock Execution Result
Job ID: JOB-123
Script: example_analysis.py
Data Size: 1024 bytes

This is a mock execution result for local development.
In production, this would run in a secure Nitro Enclave.
```

## Security Features

1. **Zero Trust**: Data encrypted end-to-end, executor never sees plaintext
2. **Hybrid Encryption**: RSA-OAEP for key exchange, AES-256-CBC for data
3. **Attestation**: Enclave identity verified by KMS
4. **Isolation**: No network access inside enclave, encrypted memory
5. **Input Validation**: BuildValidator checks all inputs with Pydantic

## File Structure

```
executor/
├── worker.py          # Entry point (ExecutorWorker)
├── executor.py        # SecureExecutor pipeline
├── services.py        # BuildValidator, ZipService, CryptoService
├── clients.py         # EnclaveClient, MiddlewareClient
├── factories.py       # Dependency injection factories
├── interfaces.py      # Abstract base classes
├── models.py          # Pydantic data models
├── exceptions.py      # Custom exceptions
├── settings.py        # Pydantic configuration
├── utils.py           # Logging decorators
└── enclave_app/       # Enclave application code
```

## Troubleshooting

1. **Enclave not starting**:
   ```bash
   # Check enclave status
   nitro-cli describe-enclaves

   # View console output
   nitro-cli console --enclave-id <CID>
   ```

2. **KMS decryption failing**:
   - Verify PCR0 in key policy matches enclave
   - Check IAM role permissions
   - Ensure vsock-proxy is running

3. **vsock connection issues**:
   - Verify enclave CID
   - Check firewall rules
   - Ensure vsock driver loaded
