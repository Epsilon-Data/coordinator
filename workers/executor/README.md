# Epsilon Executor Worker

A robust, scalable, and secure execution worker for the Epsilon system that processes job execution requests using AWS Nitro Enclaves.

## 🏗️ Architecture Overview

```
┌─────────────────────┐       ┌──────────────────┐       ┌─────────────────┐
│   RabbitMQ Queue    │──────>│  Executor Worker │──────>│   Result Queue  │
│ epsilon.execute.job │       │    (EC2 Host)    │       │ epsilon.results │
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

### New Modular Architecture

The codebase has been completely refactored for scalability, maintainability, and testability:

## Components

### 1. **Executor Worker** (`execute_worker.py`)
- Subscribes to RabbitMQ for approved jobs
- Manages Nitro Enclave lifecycle
- Sends encrypted scripts/data to enclave
- Publishes results back to queue

### 2. **Enclave Client** (`enclave/enclave_client.py`)
- Handles vsock communication with enclave
- Encrypts data using KMS (direct or envelope)
- Retrieves EC2 instance credentials

### 3. **Enclave Manager** (`services/enclave_manager.py`)
- Starts/stops Nitro Enclaves
- Manages KMS proxy for attestation
- Monitors enclave health

### 4. **Repository Manager** (`services/repository_manager.py`)
- Extracts scripts and data from repositories
- Handles different file formats (CSV, JSON)
- Provides job metadata

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
   cd enclave
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
   python execute_worker.py
   ```

## Environment Variables

- `KMS_KEY_ARN`: ARN of KMS key for encryption
- `KMS_REGION`: AWS region for KMS
- `ENCLAVE_EIF_PATH`: Path to enclave image file
- `ENCLAVE_MEMORY_MB`: Memory allocation for enclave (default: 4096)
- `ENCLAVE_CPU_COUNT`: CPU cores for enclave (default: 2)

## Local Development

When running locally without Nitro Enclaves, the worker automatically falls back to mock execution mode:

```python
# Mock execution output example
Mock Execution Result
Job ID: JOB-123
Script: example_analysis.py
Data Size: 1024 bytes
Repository Files: 15

This is a mock execution result for local development.
In production, this would run in a secure Nitro Enclave.
```

## Security Features

1. **Attestation**: Enclave identity verified by KMS
2. **Isolation**: No network access, encrypted memory
3. **Encryption**: All data encrypted before sending to enclave
4. **Credentials**: Temporary EC2 credentials, never stored

## Monitoring

- Worker logs: CloudWatch Logs
- Enclave logs: `nitro-cli console --enclave-id <CID>`
- Metrics: Execution time, success rate, enclave health

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
   - Ensure KMS proxy is running

3. **vsock connection issues**:
   - Verify enclave CID
   - Check firewall rules
   - Ensure vsock driver loaded