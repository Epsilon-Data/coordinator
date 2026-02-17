# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email: **security@epsilon-data.io**

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response timeline

- **Acknowledgement**: within 48 hours
- **Initial assessment**: within 5 business days
- **Fix timeline**: depends on severity, typically within 30 days

### Scope

The following are in scope:

- Epsilon Coordinator worker code
- Docker image configuration
- Database migration scripts
- Authentication/authorization logic
- Enclave communication protocol

The following are out of scope:

- AWS Nitro Enclave firmware (report to AWS)
- Third-party dependencies (report to the respective maintainer)
- Issues in development/test configurations

## Security Design

Epsilon Coordinator is designed with security as a core principle:

- **Confidential Computing**: All sensitive data processing occurs inside AWS Nitro Enclaves
- **Zero Trust**: Data is encrypted end-to-end; decryption only happens inside the enclave
- **Attestation**: Cryptographic proof of enclave integrity is verified for every job
- **No Persistent Secrets**: Encryption keys exist only in enclave memory during processing
