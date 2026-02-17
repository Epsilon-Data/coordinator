# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-02-17

### Added
- Docker-based worker architecture (job-fetcher, clone-worker, executor-worker)
- AWS Nitro Enclave integration for confidential computing
- Enclave attestation document capture and server-side verification
- Envelope encryption with AWS KMS for data protection
- Middleware client with SigV4-signed Lambda invocation
- Direct database fetch mode for enclave execution
- Alembic database migrations
- Conventional commit enforcement via commitizen
- Per-job execution metrics and step timing
- Enclave version and PCR0 metadata tracking
- Boot time measurement via boot_events table

### Security
- Zero Trust architecture: data encrypted end-to-end
- Attestation verification for every enclave execution
- Credential logging downgraded to DEBUG level
- No secrets in repository or git history
