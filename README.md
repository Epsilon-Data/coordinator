# Epsilon Coordinator

A secure, privacy-preserving analytics platform that enables confidential computing using AWS Nitro Enclaves and KMS-based encryption.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [CSV Encryption System](#csv-encryption-system)
- [Components](#components)
- [Setup & Development](#setup--development)
- [Security Model](#security-model)

## Overview

Epsilon Coordinator is a distributed system that securely executes data analysis scripts on encrypted datasets within trusted execution environments (AWS Nitro Enclaves). The system ensures data privacy through end-to-end encryption and secure computation while providing a seamless development experience.

## Architecture

The system consists of several microservices orchestrated via Docker Compose:

- **Clone Worker**: Fetches and prepares source code repositories
- **Dataset Manager**: Handles CSV encryption and bundle preparation
- **Executor Worker**: Manages secure script execution in enclaves
- **AI Agent**: Provides intelligent analysis and decision-making
- **API Gateway**: Handles external requests and job orchestration

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
