# Epsilon Executor Enclave

This directory contains the AWS Nitro Enclave application for secure code execution within Epsilon.

## Components

- `app.py` - The main enclave server application that handles decryption and script execution
- `Dockerfile` - Container definition for the enclave
- `build_enclave.sh` - Build script to create the Enclave Image Format (EIF) file

## Features

- **KMS Decryption**: Supports both direct and envelope encryption through KMS proxy
- **Bundle Execution**: Can extract and execute encrypted code bundles with CSV data
- **Secure Environment**: Runs in isolated Nitro Enclave with no network access

## Building the Enclave

1. Ensure you're on an EC2 instance with Nitro Enclaves enabled
2. Run the build script:
   ```bash
   cd enclave_app
   ./build_enclave.sh
   ```
   This will:
   - Download required tools (kmstool_enclave_cli, libnsm.so) if missing
   - Build the Docker image
   - Convert to EIF format
   - Copy to `/opt/enclaves/executor.eif`

## Running the Enclave

The enclave is managed by the `EnclaveManager` service in the executor. It will be started automatically when needed.

Manual commands:
```bash
# Start enclave
nitro-cli run-enclave --cpu-count 2 --memory 4096 --enclave-cid 16 --eif-path /opt/enclaves/executor.eif --debug-mode

# View console output (debug mode)
nitro-cli console --enclave-id <enclave-id>

# Stop enclave
nitro-cli terminate-enclave --enclave-id <enclave-id>
```

## Communication Flow

1. Client encrypts data/script using KMS
2. Client sends encrypted payload to enclave via vsock (port 5005)
3. Enclave decrypts using KMS proxy (port 8000)
4. Enclave executes script and returns results
5. All sensitive data remains within the secure enclave

## Security Notes

- The enclave has no network access
- All KMS operations go through the vsock proxy
- Credentials are validated by both the proxy and KMS
- Temporary files are cleaned up after execution