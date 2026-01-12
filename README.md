# Epsilon Coordinator - Nitro Enclave Job Execution

A secure, privacy-preserving analytics platform that enables confidential computing using AWS Nitro Enclaves and KMS-based encryption.

## Table of Contents

- [Overview](#overview)
- [Job Execution Flow](#job-execution-flow)
- [KMS Encryption Architecture](#kms-encryption-architecture)
- [Network Architecture](#network-architecture)
- [Security Model](#security-model)
- [Key Components](#key-components)
- [CSV Encryption System](#csv-encryption-system)
- [Deployment Requirements](#deployment-requirements)

## Overview

Epsilon Coordinator orchestrates secure job execution using AWS Nitro Enclaves with KMS-based encryption for data protection. Jobs flow from external clients through Docker containers into isolated Nitro Enclaves where sensitive data is decrypted and processed.

## Job Execution Flow

```mermaid
sequenceDiagram
    participant Client as External Client
    participant API as API Server
    participant DB as PostgreSQL
    participant Workers as Workers (Fetcher/Clone/AI/Executor)
    participant Executor as Executor Worker
    participant Enclave as Nitro Enclave
    participant KMS as AWS KMS
    participant VSOCK as vsock-proxy

    Note over Client, VSOCK: Job Submission & Preparation
    Client->>API: Submit job with repository info
    API->>DB: Insert job (status: pending)

    Note over Workers: Database Polling Pipeline
    Workers->>DB: Poll for pending jobs
    Workers->>DB: Update status: pending → queued → cloned → ai_approved

    Note over Executor: ZIP Creation
    Executor->>DB: Poll for ai_approved jobs
    Executor->>Executor: Validate build.yml config
    Executor->>Executor: Create zip with code + encrypted CSVs
    Executor->>KMS: Encrypt zip with envelope encryption
    KMS-->>Executor: Return encrypted zip + data key

    Note over Executor, Enclave: Enclave Communication Setup
    Executor->>Enclave: Health check via vsock
    Enclave-->>Executor: Health status (success)

    Note over Executor, VSOCK: Job Execution Request
    Executor->>Enclave: Send encrypted zip via vsock
    Note over Enclave: Inside Secure Enclave
    Enclave->>VSOCK: Request KMS decryption via vsock-proxy
    VSOCK->>KMS: Forward KMS decrypt request
    KMS-->>VSOCK: Return decrypted data key
    VSOCK-->>Enclave: Forward decrypted data key

    Note over Enclave: Data Processing
    Enclave->>Enclave: Decrypt zip with data key
    Enclave->>Enclave: Extract zip
    Enclave->>Enclave: Find encrypted CSV files (.csv.encrypted)

    loop For each encrypted CSV
        Enclave->>VSOCK: Decrypt CSV data key via KMS
        VSOCK->>KMS: Forward decrypt request
        KMS-->>VSOCK: Return decrypted CSV data key
        VSOCK-->>Enclave: Forward decrypted key
        Enclave->>Enclave: Decrypt CSV content with data key
        Enclave->>Enclave: Save decrypted CSV to temp directory
    end

    Enclave->>Enclave: Execute Python script with decrypted data
    Enclave->>Enclave: Collect script output
    Enclave-->>Executor: Return execution results

    Note over Executor, Client: Result Delivery
    Executor->>DB: Update job status to completed
    API->>DB: Query job status
    API-->>Client: Return job results
```

## KMS Encryption Architecture

### 1. Envelope Encryption Process

The system uses AWS KMS envelope encryption for secure data handling:

**KMS Data Key Generation & Usage:**
```mermaid
sequenceDiagram
    participant App as Application
    participant KMS as AWS KMS
    participant Storage as File Storage
    
    Note over App, Storage: Data Key Generation
    App->>KMS: generate_data_key(KeyId)
    KMS-->>App: {plaintext_key: 32_bytes, encrypted_key: blob}
    
    Note over App: Encryption Process
    App->>App: encrypt_zip(data, plaintext_key)
    App->>App: delete plaintext_key from memory
    App->>Storage: store encrypted_zip + encrypted_key
    
    Note over App, Storage: Later - Decryption Process  
    App->>Storage: load encrypted_zip + encrypted_key
    App->>KMS: decrypt(encrypted_key)
    KMS-->>App: plaintext_key (32_bytes)
    App->>App: decrypt_zip(encrypted_zip, plaintext_key)
    App->>App: delete plaintext_key from memory
```

**Encryption (Outside Enclave):**
```mermaid
graph TD
    A[Original Data] --> B[Generate Random Data Key]
    B --> C[Encrypt Data with Data Key]
    B --> D[Encrypt Data Key with KMS]
    C --> E[Encrypted Data Blob]
    D --> F[Encrypted Data Key]
    E --> G[Store Both Together]
    F --> G
```

**Decryption (Inside Enclave):**
```mermaid
graph TD
    A[Encrypted Bundle] --> B[Extract Encrypted Data Key]
    A --> C[Extract Encrypted Data]
    B --> D[KMS Decrypt via vsock-proxy]
    D --> E[Plain Data Key]
    E --> F[Decrypt Data with Plain Key]
    C --> F
    F --> G[Decrypted Data]
```

### 2. CSV File Encryption Format

Binary encrypted CSV files use this structure:
```
[4 bytes: key_length][encrypted_data_key][encrypted_csv_content]
```

With accompanying `.meta` file containing:
```json
{
  "original_filename": "data.csv",
  "encryption_method": "envelope",
  "created_at": "2025-09-09T06:42:20Z"
}
```

## Network Architecture

### VSOCK Communication
- **vsock-proxy** runs on the host system
- Forwards KMS requests from enclave to AWS KMS
- Command: `vsock-proxy 8000 kms.ap-southeast-2.amazonaws.com 443`

### Docker Network Configuration
```yaml
executor-worker:
  privileged: true
  network_mode: host
  volumes:
    - /dev/nitro_enclaves:/dev/nitro_enclaves
    - /var/run/nitro_enclaves/:/var/run/nitro_enclaves/
    - /usr/bin/nitro-cli:/usr/bin/nitro-cli
```

## Security Model

### Enclave Isolation
- **Nitro Enclave**: Cryptographically isolated VM
- **No Network Access**: Except via vsock to host
- **No Persistent Storage**: All data in memory
- **Attestation**: Cryptographic proof of enclave integrity

### Key Management
- **KMS Integration**: All decryption via AWS KMS
- **Temporary Keys**: Data keys only exist in enclave memory
- **No Key Storage**: Keys destroyed after processing

### Data Protection
- **Encrypted at Rest**: All sensitive data encrypted
- **Encrypted in Transit**: vsock communication
- **Memory Only**: Decrypted data never touches disk

## Key Components

### Outside Enclave
- **API Server**: Job submission endpoint
- **PostgreSQL**: Job state and status management
- **Workers**: Pipeline stages (job-fetcher, clone, ai-agent, executor)
- **Executor Worker**: Bundle preparation and orchestration
- **vsock-proxy**: KMS communication bridge

### Inside Enclave
- **Enclave Server**: vsock listener and request handler
- **KMS Decryptor**: Handles KMS decryption requests
- **CSV Decryptor**: Binary CSV file decryption
- **Bundle Executor**: Script execution with decrypted data
- **Script Executor**: Python script runner

## CSV Encryption System

### Overview

The CSV encryption system ensures sensitive data is protected throughout the entire pipeline while maintaining optimal performance and minimal storage overhead.

### Architecture Flow

```mermaid
sequenceDiagram
    participant User as User/API
    participant SM as Secure Executor
    participant DM as Dataset Manager
    participant KMS as AWS KMS
    participant FS as File System
    participant EC as Enclave Client (Local)
    participant Script as Analysis Script

    Note over User, Script: CSV Encryption & Bundle Creation Phase
    
    User->>SM: Execute Job (JOB-MEWFTY24)
    SM->>DM: prepare_execution_data()
    
    Note over DM: Step 1: Load Repository
    DM->>FS: Load repo from /shared/epsilon/repositories/JOB-MEWFTY24
    FS-->>DM: Repository contents (including archetypes/)
    
    Note over DM: Step 2: Check Build Config
    DM->>FS: Read build/build.yml
    FS-->>DM: Config: dataset_id="healthcare_db"
    
    Note over DM: Step 3: Process CSV Files
    DM->>FS: Scan archetypes/healthcare_db/
    FS-->>DM: Found: healthcare_db_dummy.csv.encrypted (already encrypted)
    DM->>DM: Skip encryption (already done)
    
    Note over DM: Step 4: Create Bundle
    DM->>FS: Create ZIP bundle with encrypted CSV files
    DM->>KMS: generate_data_key(kms_key_arn)
    KMS-->>DM: {plaintext_key, encrypted_data_key}
    DM->>DM: Encrypt ZIP bundle with plaintext_key
    DM->>FS: Save encrypted bundle (45KB)
    DM-->>SM: Return bundle file path
    
    Note over User, Script: CSV Decryption & Execution Phase
    
    SM->>EC: execute_script_envelope(encrypted_bundle_path)
    EC->>FS: Load encrypted bundle
    EC->>KMS: decrypt(encrypted_data_key)
    KMS-->>EC: plaintext_key
    EC->>EC: Decrypt ZIP bundle
    EC->>EC: Extract to /tmp/enclave_bundle_xxx/
    
    Note over EC: CSV Decryption Process
    EC->>EC: Scan archetypes/healthcare_db/
    EC->>EC: Find: healthcare_db_dummy.csv.encrypted
    
    rect rgb(255, 240, 245)
        Note over EC: Binary CSV Decryption
        EC->>FS: Read healthcare_db_dummy.csv.meta
        FS-->>EC: {original_filename, method: "envelope"}
        EC->>FS: Read healthcare_db_dummy.csv.encrypted (binary)
        FS-->>EC: {key_length, encrypted_data_key, encrypted_content}
        EC->>KMS: decrypt(encrypted_data_key)
        KMS-->>EC: plaintext_key
        EC->>EC: Decrypt CSV content with plaintext_key
        EC->>FS: Write healthcare_db_dummy.csv (decrypted)
        EC->>FS: Remove .encrypted and .meta files
    end
    
    Note over EC: Script Execution
    EC->>Script: Execute example_analysis.py
    Script->>FS: Load archetypes/healthcare_db/healthcare_db_dummy.csv
    FS-->>Script: CSV data (14 records)
    Script->>Script: Process healthcare data
    Script-->>EC: Output: "Loaded 14 dummy records from CSV..."
    EC-->>SM: ExecutionResult(status='success', output=...)
    SM-->>User: Job completed successfully

    Note over User, Script: Key Security Benefits
    Note right of KMS: • CSV data encrypted at rest
    Note right of KMS: • Bundle size optimized (45KB vs 1GB)
    Note right of KMS: • End-to-end encryption via KMS
    Note right of KMS: • Temporary decryption only in secure enclave
```

### Key Features

#### 1. **Binary Encryption Format**
- **Problem Solved**: Previous Base64 JSON format caused 1GB+ bundle sizes
- **Solution**: Binary storage reduces bundles to ~45KB (2000% improvement)
- **Format**: 
  - `.csv.encrypted`: Binary encrypted data
  - `.csv.meta`: Small JSON metadata file

#### 2. **Envelope Encryption**
- **Data Keys**: Generated per CSV file via AWS KMS
- **Master Key**: Managed by AWS KMS (never exposed)
- **Process**: 
  1. Generate data key from KMS
  2. Encrypt CSV with data key
  3. Encrypt data key with master key
  4. Store encrypted data + encrypted data key

#### 3. **Dual Encryption Layers**
- **Layer 1**: Individual CSV files encrypted with KMS envelope encryption
- **Layer 2**: Entire ZIP bundle encrypted with KMS envelope encryption
- **Benefit**: Defense in depth while maintaining performance

### File Structure

```
archetypes/
└── healthcare_db/
    ├── __init__.py
    ├── healthcare_db.py              # Dataset loader
    ├── healthcare_db.json            # Schema definition
    ├── healthcare_db_dummy.csv.encrypted  # Binary encrypted CSV
    └── healthcare_db_dummy.csv.meta       # Encryption metadata
```

### Encryption Process

1. **Repository Loading**: Clone repository with archetype definitions
2. **CSV Detection**: Scan for `.csv` files in archetype directories
3. **KMS Key Generation**: Generate unique data key per CSV file
4. **Binary Encryption**: Encrypt CSV content, store as binary format
5. **Bundle Creation**: Create ZIP containing encrypted CSV files
6. **Bundle Encryption**: Encrypt entire ZIP with separate KMS key

### Decryption Process

1. **Bundle Decryption**: Decrypt ZIP bundle using KMS
2. **File Extraction**: Extract to temporary directory
3. **CSV Detection**: Find `.csv.encrypted` files
4. **Metadata Reading**: Load encryption metadata from `.csv.meta`
5. **Binary Decryption**: 
   - Read encrypted data key and content
   - Decrypt data key via KMS
   - Decrypt CSV content with data key
6. **File Restoration**: Create original `.csv` file
7. **Cleanup**: Remove encrypted files after successful decryption

## Deployment Requirements

### Host System
- AWS EC2 with Nitro Enclaves enabled
- Docker and docker-compose installed
- vsock-proxy running: `vsock-proxy 8000 kms.ap-southeast-2.amazonaws.com 443`

### AWS Permissions
- KMS decrypt permissions for enclave role
- Access to specified KMS keys for data decryption
- Regional KMS endpoint: `kms.ap-southeast-2.amazonaws.com`

### Environment Variables
```bash
DATABASE_URL=postgresql://...
OPENAI_API_KEY=sk-...
ENVIRONMENT=production
USE_LOCAL_ENCLAVE=false
```

### File Paths

#### Key Configuration Files
- `docker-compose.yml`: Container orchestration
- `workers/executor/enclave_app/`: Enclave application code
- `workers/executor/clients.py`: Enclave client interface
- `shared_storage/`: Temporary file storage

#### Execution Flow
1. **Job Queue**: PostgreSQL → Workers poll for jobs by status
2. **Bundle Prep**: `/shared/epsilon/repositories/JOB-ID/`
3. **Encrypted Storage**: `/shared/epsilon/enclave_execution_results/JOB-ID/`
4. **Enclave Temp**: `/tmp/enclave_bundle_*/` (inside enclave)

### Monitoring & Debugging

#### Log Locations
- **Executor Worker**: `docker logs coordinator-executor-worker-1`
- **Enclave Logs**: Inside enclave (shown in executor logs)
- **vsock-proxy**: Host system logs

#### Health Checks
- **Enclave Health**: `{"operation": "health_check"}` → `{"status": "success"}`
- **KMS Connectivity**: Verified during first decryption request
- **vsock Communication**: Tested with each job execution

This architecture ensures secure, isolated execution of sensitive data analysis jobs while maintaining compliance with data protection requirements.
