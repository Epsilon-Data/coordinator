# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.2](https://github.com/Epsilon-Data/coordinator/compare/v1.0.1...v1.0.2) (2026-02-18)


### Bug Fixes

* **executor:** remove explicit None for script_path and fix Docker volume permissions ([57e87fb](https://github.com/Epsilon-Data/coordinator/commit/57e87fbb9ae1ae829370d1b548124a954c0d3deb))

## [1.0.1](https://github.com/Epsilon-Data/coordinator/compare/v1.0.0...v1.0.1) (2026-02-18)


### Bug Fixes

* harden workers with atomic job claiming, input validation, and non-root Docker ([0635425](https://github.com/Epsilon-Data/coordinator/commit/063542571b9129542d7ea3c31a2c0e2975cb366b))


### Documentation

* fix env var mismatch, project structure, and missing config ([12ce623](https://github.com/Epsilon-Data/coordinator/commit/12ce62379e4cfbf9dd24b8d63caa6d7a38baed05))

## 1.0.0 (2026-02-17)

### Features

* Docker-based worker architecture (job-fetcher, clone-worker, executor-worker)
* AWS Nitro Enclave integration for confidential computing
* Enclave attestation document capture and server-side verification
* Envelope encryption with AWS KMS for data protection
* Middleware client with SigV4-signed Lambda invocation
* Direct database fetch mode for enclave execution
* Alembic database migrations
* Conventional commit enforcement via commitizen
* Per-job execution metrics and step timing
* Enclave version and PCR0 metadata tracking
* Boot time measurement via boot_events table

### Bug Fixes

* clean up worker code and fix bugs ([d6195a8](https://github.com/Epsilon-Data/coordinator/commit/d6195a87b498627a8fa99a18b4280a938a049a18))
* fix CI test failures and add test environment setup ([73cfa8a](https://github.com/Epsilon-Data/coordinator/commit/73cfa8aba1da802bad8eb5067fe81dbe39581f20))
* harden security, remove dead config, and improve code quality ([71d8094](https://github.com/Epsilon-Data/coordinator/commit/71d80949e1ab29bff70046fd167ce39bc490a8b3))

### Security

* Zero Trust architecture: data encrypted end-to-end
* Attestation verification for every enclave execution
* Credential logging downgraded to DEBUG level
* No secrets in repository or git history
