# Enclave Application Architecture

## Module Structure

```
enclave_app/
├── app.py              # Main entry point
├── config.py           # Configuration settings
├── server.py           # VSock server implementation
├── request_handler.py  # Request routing and handling
├── kms_decrypt.py      # KMS decryption operations
├── script_executor.py  # Script execution coordination
├── bundle_executor.py  # Bundle extraction and execution
└── csv_decryptor.py    # CSV file decryption
```

## Module Responsibilities

### 1. **app.py**
- Entry point for the enclave application
- Environment verification
- Server initialization

### 2. **config.py**
- Centralized configuration management
- Constants for ports, paths, timeouts
- Environment-based settings

### 3. **server.py**
- VSock server implementation
- Client connection handling
- Request/response management

### 4. **request_handler.py**
- Request parsing and validation
- Operation routing (decrypt, execute_script_envelope, health_check)
- Response formatting

### 5. **kms_decrypt.py**
- KMS decryption via kmstool-enclave-cli
- Direct decryption for small data (<4KB)
- Envelope decryption for large data (>4KB)
- Credential management

### 6. **script_executor.py**
- High-level script execution coordination
- Mode detection (bundle vs single script)
- Execution routing

### 7. **bundle_executor.py**
- ZIP bundle extraction
- Script discovery from build.yml
- Working directory management
- Subprocess execution

### 8. **csv_decryptor.py**
- CSV file encryption detection
- In-place decryption
- Error handling for corrupted files

## Data Flow

1. **Client Request** → Server receives encrypted request via VSock
2. **Request Handling** → RequestHandler validates and routes operation
3. **Decryption** → KMSDecryptor decrypts data using KMS proxy
4. **Execution** → ScriptExecutor determines execution mode
5. **Bundle Processing** → BundleExecutor extracts and runs scripts
6. **CSV Decryption** → CSVDecryptor handles encrypted data files
7. **Response** → Results sent back to client via VSock

## Security Features

- **No Network Access**: Enclave has no internet connectivity
- **KMS Proxy**: All KMS operations go through authenticated proxy
- **Memory Isolation**: Code runs in isolated enclave memory
- **Attestation**: Enclave identity verified by KMS
- **Credential Validation**: Each request includes AWS credentials

## Communication Protocols

### VSock Communication
- Port: 5005
- Protocol: TCP over VSock
- Format: JSON request/response

### KMS Proxy Communication
- Port: 8000 (localhost only)
- Tool: kmstool-enclave-cli
- Authentication: AWS credentials + attestation

## Error Handling

- Graceful degradation for CSV decryption failures
- Timeout protection for script execution
- Detailed error messages for debugging
- Cleanup of temporary files on all paths

## Sequence Diagrams

### 1. Bundle Execution Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server
    participant RH as RequestHandler
    participant SE as ScriptExecutor
    participant KD as KMSDecryptor
    participant BE as BundleExecutor
    participant CD as CSVDecryptor
    participant KP as KMS Proxy

    C->>S: Connect via VSock (port 5005)
    C->>S: Send execute_script_envelope request
    S->>RH: handle_request(request_data)
    RH->>RH: Parse & validate request
    
    Note over RH: Detect bundle mode (empty script)
    
    RH->>SE: execute_script_with_bundle()
    SE->>KD: decrypt_data(encrypted_bundle)
    KD->>KP: kmstool decrypt (via localhost:8000)
    KP-->>KD: Decrypted bundle bytes
    KD-->>SE: Bundle zip bytes
    
    SE->>BE: execute_bundle(bundle_data)
    BE->>BE: Extract ZIP to temp directory
    BE->>BE: Find CSV files in archetypes/
    
    loop For each CSV file
        BE->>CD: decrypt_csv_file(csv_path)
        CD->>KD: decrypt_data(csv_metadata)
        KD->>KP: kmstool decrypt
        KP-->>KD: Decrypted CSV data
        KD-->>CD: CSV plaintext
        CD->>CD: Write decrypted CSV
    end
    
    BE->>BE: Parse build.yml for script
    BE->>BE: Execute Python script
    BE-->>SE: Execution output
    SE-->>RH: Success with output
    RH-->>S: Response JSON
    S-->>C: Send response via VSock
```

### 2. Direct Decryption Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server
    participant RH as RequestHandler
    participant KD as KMSDecryptor
    participant KP as KMS Proxy
    participant KMS as AWS KMS

    C->>S: Connect via VSock
    C->>S: Send decrypt request
    S->>RH: handle_request(request_data)
    RH->>RH: Validate decrypt request
    
    RH->>KD: decrypt_with_kms(ciphertext)
    KD->>KD: Prepare kmstool command
    
    KD->>KP: Execute kmstool_enclave_cli
    Note over KP: Proxy validates enclave attestation
    KP->>KMS: Decrypt request (via Internet)
    KMS-->>KP: Plaintext
    KP-->>KD: PLAINTEXT: base64_data
    
    KD->>KD: Decode base64
    KD-->>RH: Decrypted plaintext
    RH-->>S: Success response
    S-->>C: Send JSON response
```

### 3. Envelope Decryption Flow

```mermaid
sequenceDiagram
    participant SE as ScriptExecutor
    participant KD as KMSDecryptor
    participant KP as KMS Proxy
    participant F as Fernet

    Note over SE: Large data (>4KB)
    
    SE->>KD: decrypt_envelope(encrypted_data_key, encrypted_data)
    
    Note over KD: Step 1: Decrypt data key
    KD->>KP: Decrypt encrypted_data_key
    KP-->>KD: Plaintext data key
    
    Note over KD: Step 2: Use data key for symmetric decryption
    KD->>F: Create Fernet with data key
    KD->>F: Decrypt encrypted_data
    F-->>KD: Decrypted data bytes
    
    KD-->>SE: Decrypted data
```

### 4. Script Execution Within Bundle

```mermaid
sequenceDiagram
    participant BE as BundleExecutor
    participant OS as Operating System
    participant PY as Python Process

    BE->>BE: Create temp directory
    BE->>BE: Extract bundle.zip
    BE->>BE: Decrypt all CSV files
    BE->>BE: Parse build.yml
    
    alt Script in bundle
        BE->>BE: Find script (e.g., example_analysis.py)
    else No script in bundle
        BE->>BE: Create script from content
    end
    
    BE->>OS: Change to bundle directory
    BE->>OS: Set PYTHONPATH=bundle_dir
    
    BE->>PY: subprocess.run(script)
    Note over PY: Script can import from bundle
    PY->>PY: Load healthcare_db module
    PY->>PY: Read decrypted CSV data
    PY->>PY: Execute analysis
    PY-->>BE: stdout + stderr
    
    BE->>OS: Change back to original dir
    BE->>BE: Clean up temp directory
    BE-->>SE: Formatted output
```

### 5. Error Handling Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server
    participant RH as RequestHandler
    participant M as Module

    C->>S: Send request
    S->>RH: handle_request()
    
    alt JSON Parse Error
        RH->>RH: Invalid JSON
        RH-->>S: Error: Invalid JSON
    else Validation Error
        RH->>RH: Missing required field
        RH-->>S: Error: Missing field X
    else Execution Error
        RH->>M: Execute operation
        M->>M: Exception occurs
        M-->>RH: Error details
        RH-->>S: Error: Operation failed
    end
    
    S-->>C: Send error response
    Note over C: {"status": "error", "message": "..."}
```

### 6. Health Check Flow

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server
    participant RH as RequestHandler

    C->>S: Send health_check request
    S->>RH: handle_request()
    RH->>RH: Route to health check
    RH-->>S: {"status": "success", "message": "healthy"}
    S-->>C: Send response
```

## Component Interaction Summary

The sequence diagrams above illustrate:

1. **Bundle Execution**: The complete flow from encrypted bundle to executed script output
2. **Direct Decryption**: Simple KMS decryption for small data
3. **Envelope Decryption**: Two-step decryption for large data
4. **Script Execution**: How scripts run within the bundle context
5. **Error Handling**: Graceful error propagation
6. **Health Check**: Simple connectivity verification

Key points:
- All KMS operations go through the proxy (no direct internet access)
- Bundle execution involves multiple decryption steps
- Temporary files are always cleaned up
- Errors are caught and returned as structured responses