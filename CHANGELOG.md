# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.3.4](https://github.com/Epsilon-Data/coordinator/compare/v1.3.3...v1.3.4) (2026-03-30)


### Bug Fixes

* **ci:** add postgres service container for tests ([044084e](https://github.com/Epsilon-Data/coordinator/commit/044084e245a50fad888c4d5821f5d3257d6b65cc))
* correct license reference from Apache to MIT in README ([d58993c](https://github.com/Epsilon-Data/coordinator/commit/d58993cb546d164053a5f7b470564a30e9f3bb13))
* correct license reference from Apache to MIT in README ([c3aac50](https://github.com/Epsilon-Data/coordinator/commit/c3aac50ed5d38a4f30415660acff461c27c39b63))

## [1.2.0](https://github.com/Epsilon-Data/coordinator/compare/v1.1.3...v1.2.0) (2026-03-21)


### Features

* **ai-agent:** AST scanner, output disclosure checker, hardened pipeline ([dd38db6](https://github.com/Epsilon-Data/coordinator/commit/dd38db6be1d39d998d5714b190ce15560d883795))
* **ai-agent:** AST scanner, output disclosure checker, hardened pipeline ([7acb503](https://github.com/Epsilon-Data/coordinator/commit/7acb503a8ad6c9da5ed9398061fb3718aa0881e8))
* **ai-agent:** production-hardening with AST scanner, output disclosure checker, and structured LLM reasoning ([1cc84f7](https://github.com/Epsilon-Data/coordinator/commit/1cc84f7cc37c15c5d4b1921054475ffb7af27fae))


### Bug Fixes

* **ci:** install ai-agent deps in CI pipeline for test collection ([604ccd9](https://github.com/Epsilon-Data/coordinator/commit/604ccd987c9a41023174bf8ea20b28e82bdf6473))
* **tests:** update clone and executor tests to match new job status flow ([8d12468](https://github.com/Epsilon-Data/coordinator/commit/8d12468f2dcf3773379b60eacb7a5be257a71dce))

## [1.1.3](https://github.com/Epsilon-Data/coordinator/compare/v1.1.2...v1.1.3) (2026-03-16)


### Bug Fixes

* **executor:** normalize proxy_info fields and fallback to build config ([2cb7527](https://github.com/Epsilon-Data/coordinator/commit/2cb7527a3656850673ccc19c759effc626e50b1e))
* **executor:** normalize proxy_info fields and fallback to build config ([7dbd808](https://github.com/Epsilon-Data/coordinator/commit/7dbd8087e75b6d9b862dcc12e3bc3babc696367c))
* **proxy:** send raw JSON user_data for attestation binding ([acb7941](https://github.com/Epsilon-Data/coordinator/commit/acb7941646c2b6542baf06a0092d1be6fdefc95f))
* **proxy:** send raw JSON user_data for attestation binding ([621a1d0](https://github.com/Epsilon-Data/coordinator/commit/621a1d0664dde1d232b30c17fc0b10f5f91564ca))
* **proxy:** use configurable rathole host for proxy tunnel and add lo… ([08a9e47](https://github.com/Epsilon-Data/coordinator/commit/08a9e4794aca62730e1d0f03795292bc04861b66))
* **proxy:** use configurable rathole host for proxy tunnel and add logging ([69c9047](https://github.com/Epsilon-Data/coordinator/commit/69c904759d14c100ca651fe5c7e52f8f6ac82cd7))

## [1.1.2](https://github.com/Epsilon-Data/coordinator/compare/v1.1.1...v1.1.2) (2026-03-16)


### Bug Fixes

* **executor:** complete proxy mode handling in middleware response flow ([7b61beb](https://github.com/Epsilon-Data/coordinator/commit/7b61beb80a021f0c6f55f9f802a2f7d5a2c4a51e))
* **executor:** complete proxy mode handling in middleware response flow ([c2fad4f](https://github.com/Epsilon-Data/coordinator/commit/c2fad4fe662a41b7a0a195323a67fc053030c174))

## [1.1.1](https://github.com/Epsilon-Data/coordinator/compare/v1.1.0...v1.1.1) (2026-03-16)


### Bug Fixes

* **executor:** handle proxy mode response from middleware ([6e59489](https://github.com/Epsilon-Data/coordinator/commit/6e5948913e508071c8b0ea6fc6428f5e52126bb6))
* **executor:** handle proxy mode response from middleware ([eadd7b2](https://github.com/Epsilon-Data/coordinator/commit/eadd7b2f07dab6b06667cf2e13832f9c15f74da0))

## [1.1.0](https://github.com/Epsilon-Data/coordinator/compare/v1.0.2...v1.1.0) (2026-03-15)


### Features

* **executor:** add proxy tunnel support for data owner connections ([f20d1e7](https://github.com/Epsilon-Data/coordinator/commit/f20d1e707bfd950f4ef70a4f204d33c305ae572a))
* **executor:** add proxy tunnel support for data owner connections ([cd518b1](https://github.com/Epsilon-Data/coordinator/commit/cd518b1ca00f80260221b5621ce315df209e4450))


### Bug Fixes

* **tests:** update executor tests for proxy client parameter ([25786d7](https://github.com/Epsilon-Data/coordinator/commit/25786d7393b234d791dd2057025293f610bbb1a6))

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
