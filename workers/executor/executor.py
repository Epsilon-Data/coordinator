"""
Secure executor with Zero Trust enclave integration.

Flow:
1. Validate build configuration
2. Get public key from enclave
3. Fetch encrypted CSV from middleware
4. Package and encrypt build folder
5. Send encrypted payloads to enclave for execution
"""
import json
import os
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from workers.executor.settings import Settings
from workers.executor.constants import EnclaveOperations, MiddlewareModes
from workers.executor.interfaces import IEnclaveClient, IExecutor, IMiddlewareClient, MiddlewareRequest, MiddlewareResponse, ProxyInfo, ProxyResponse
from workers.executor.jac import (
    compute_context_hash,
    compute_job_id,
    compute_script_hash,
    sign_jac,
)
from workers.executor.models import JobExecutionRequest, ExecutionResult, BuildConfig, JobStatus
from workers.executor.exceptions import BuildValidationError
from workers.executor.services import BuildValidator, ZipService
from workers.executor.utils import get_logger
from shared.job_logger import JobLogger

logger = get_logger(__name__)

# SQL validation constants
MAX_SQL_QUERY_LENGTH = 10000
DANGEROUS_SQL_KEYWORDS = re.compile(
    r'\b(DROP|DELETE|INSERT|UPDATE|ALTER|TRUNCATE)\b',
    re.IGNORECASE
)


def _validate_sql_query(sql_query: str) -> None:
    """Validate that a SQL query is a safe read-only SELECT statement.

    Raises:
        ValueError: If the query fails validation.
    """
    stripped = sql_query.strip()

    if not stripped:
        raise ValueError("SQL query is empty")

    if len(stripped) > MAX_SQL_QUERY_LENGTH:
        raise ValueError(
            f"SQL query exceeds maximum length ({len(stripped)} > {MAX_SQL_QUERY_LENGTH} chars)"
        )

    if not stripped.upper().startswith('SELECT'):
        raise ValueError(
            f"SQL query must start with SELECT, got: {stripped[:20]}..."
        )

    match = DANGEROUS_SQL_KEYWORDS.search(stripped)
    if match:
        raise ValueError(
            f"SQL query contains dangerous keyword: {match.group(0)}"
        )


class SecureExecutor(IExecutor):
    """
    Secure job executor implementing Zero Trust architecture.

    Execution Flow:
        1. Validate build configuration (build.yml)
        2. Get public key from enclave
        3. Fetch encrypted CSV from middleware (if datasets configured)
        4. Zip and encrypt build folder with enclave's public key
        5. Send encrypted payloads to enclave for execution

    Data never leaves encryption until inside the enclave.
    """

    def __init__(
        self,
        enclave_client: IEnclaveClient,
        settings: Settings,
        middleware_client: IMiddlewareClient,
        proxy_client=None,
        atl_client=None,
    ):
        self._enclave_client = enclave_client
        self._settings = settings
        self._middleware_client = middleware_client
        self._proxy_client = proxy_client
        self._atl_client = atl_client
        self._zip_service = ZipService()
        self._log = JobLogger("ExecutorWorker")
        self._active_jobs: Dict[str, Dict[str, Any]] = {}

        logger.info(f"SecureExecutor initialized (proxy={'enabled' if proxy_client else 'disabled'})")

    def execute(self, request: JobExecutionRequest) -> ExecutionResult:
        """Execute a job with Zero Trust security model."""
        start_time = time.time()
        job_id = request.job_id
        step_timing = {}

        try:
            self._log.info(job_id, "execution", f"Starting execution for job {job_id}")

            # Step 1: Validate build
            t0 = time.time()
            build_config = self._step_validate_build(job_id, request.repo_path)
            step_timing['build_validation_ms'] = round((time.time() - t0) * 1000, 2)

            # Step 2: Get public key
            t0 = time.time()
            public_key, session_id = self._step_get_public_key(job_id)
            step_timing['get_public_key_ms'] = round((time.time() - t0) * 1000, 2)

            # Determine mode based on feature flags
            use_direct_db = self._settings.middleware.use_direct_db
            mode = MiddlewareModes.DIRECT_DB if use_direct_db else MiddlewareModes.LEGACY
            # Note: proxy mode is determined by middleware response, not a feature flag

            # Step 3: Fetch from middleware (mode-dependent)
            t0 = time.time()
            middleware_response = self._step_fetch_from_middleware(
                job_id, request, build_config, public_key, mode
            )
            step_timing['fetch_middleware_ms'] = round((time.time() - t0) * 1000, 2)

            # Step 4: Zip and encrypt
            t0 = time.time()
            encrypted_zip = self._step_zip_and_encrypt(job_id, request.repo_path, public_key)
            step_timing['zip_and_encrypt_ms'] = round((time.time() - t0) * 1000, 2)

            # Step 4b: JAC + Commitment (commitment-then-dispatch).
            # Paper §3.4.1, §4.2: the central protocol contribution. Sign a
            # JAC, submit a Commitment entry to the public log, and BLOCK on
            # the inclusion receipt before dispatching to the enclave. A
            # logged Commitment paired with no corresponding HA entry within
            # MMD is non-repudiable suppression evidence detectable from the
            # public log alone.
            #
            # Operates under the two-ID model: the operational request.job_id
            # is the platform-assigned primary key used throughout the
            # pipeline (logs, DB, API responses). The cryptographically
            # committed job_id_committed = H(researcher_nonce || operator_nonce)
            # is what gets bound into the JAC, Commitment entry, and HA
            # attestation. The mapping is persisted post-execution.
            signed_jac = None
            commitment_receipt = None
            commitment_hash = None
            job_id_committed = None
            researcher_nonce_hex = None
            atl_nonce = None
            atl_context_hash = None
            is_non_compliant = False

            if self._atl_client:
                # 4b.1 — Freshness nonce from current STH (paper §3.5).
                t0 = time.time()
                sth = self._atl_client.fetch_sth()
                step_timing['atl_sth_fetch_ms'] = round((time.time() - t0) * 1000, 2)

                if sth is None:
                    # Safety-over-availability (paper §3.4.2). Refuse unless
                    # the caller has explicitly opted in via allow_stale, in
                    # which case the job is marked Non-Compliant.
                    if not request.allow_stale:
                        raise RuntimeError(
                            "ATL unreachable; refusing to submit "
                            "(safety-over-availability). Set allow_stale=True "
                            "to override (marks job Non-Compliant)."
                        )
                    atl_nonce = secrets.token_bytes(32)
                    is_non_compliant = True
                    self._log.warning(
                        job_id, "atl",
                        "ATL offline and allow_stale=True; job will be marked Non-Compliant",
                    )
                else:
                    atl_nonce = self._atl_client.derive_nonce(sth)

                # 4b.2 — Mutually committed job_id = H(r || o). If the
                # researcher did not supply r, generate one server-side and
                # mark the job Non-Compliant: mutual commitment is the
                # property the paper relies on.
                if request.researcher_nonce:
                    researcher_nonce = bytes.fromhex(request.researcher_nonce)
                else:
                    researcher_nonce = secrets.token_bytes(16)
                    if not is_non_compliant:
                        is_non_compliant = True
                        self._log.warning(
                            job_id, "atl",
                            "No researcher_nonce supplied; job will be marked Non-Compliant",
                        )
                operator_nonce = secrets.token_bytes(16)
                job_id_committed = compute_job_id(researcher_nonce, operator_nonce)
                researcher_nonce_hex = researcher_nonce.hex()

                # 4b.3 — script_hash from the actual submitted script.
                script_full_path = os.path.join(request.repo_path, request.script_path)
                if not os.path.exists(script_full_path):
                    raise FileNotFoundError(f"Script not found: {script_full_path}")
                with open(script_full_path, 'rb') as f:
                    script_bytes = f.read()
                script_hash = compute_script_hash(script_bytes)

                # 4b.4 — context_hash binding entry metadata to user_data.
                commit_sha = (
                    request.ai_decision.get('commit_sha', '') if request.ai_decision else ''
                )
                archetype_id = (
                    build_config.datasets[0].archetype_id if build_config.datasets else ''
                )
                dataset_id = (
                    build_config.datasets[0].dataset_id if build_config.datasets else ''
                )
                atl_context_hash = bytes.fromhex(compute_context_hash(
                    job_id=job_id_committed,
                    commit_sha=commit_sha,
                    archetype_id=archetype_id,
                    dataset_id=dataset_id,
                ))

                # 4b.5 — Sign the JAC. The full payload stays with the
                # coordinator; only its SHA-256 is logged via Commitment.
                signed_jac = sign_jac(
                    private_key=self._atl_client._private_key,
                    job_id=job_id_committed,
                    script_hash=script_hash,
                    dataset_id=dataset_id,
                    nonce=atl_nonce,
                    t_accept=int(time.time()),
                )

                # 4b.6 — Submit Commitment and BLOCK on inclusion receipt.
                # Non-Compliant jobs skip the log entry; the JAC is still
                # signed and bound into user_data so the researcher has
                # something to verify against if they re-enable ATL.
                if not is_non_compliant:
                    t0 = time.time()
                    commitment_hash, commitment_receipt = self._atl_client.sign_and_submit_commitment(
                        job_id=job_id_committed,
                        jac_payload_bytes=signed_jac['payload'].encode(),
                    )
                    step_timing['commitment_submit_ms'] = round((time.time() - t0) * 1000, 2)

                    if commitment_receipt is None:
                        raise RuntimeError(
                            "ATL Commitment submission failed; refusing to proceed "
                            "(safety-over-availability). Job has not been dispatched."
                        )

                    self._log.info(
                        job_id, "atl",
                        f"JAC signed and Commitment logged: "
                        f"job_id_committed={job_id_committed[:16]}... "
                        f"tree_size={commitment_receipt.get('tree_size', '?')}",
                    )

            # State for post-execution result construction. Keyed by the
            # operational job_id so the existing result-publish path is
            # untouched — job_id_committed travels via this dict.
            self._active_jobs[job_id] = {
                'atl_nonce': atl_nonce,
                'atl_context_hash': atl_context_hash,
                'signed_jac': signed_jac,
                'commitment_receipt': commitment_receipt,
                'commitment_hash': commitment_hash,
                'job_id_committed': job_id_committed,
                'researcher_nonce': researcher_nonce_hex,
                'is_non_compliant': is_non_compliant,
            }

            # Step 5: Execute in enclave (branch on mode)
            t0 = time.time()
            if middleware_response and middleware_response.is_proxy:
                # Proxy mode: fetch encrypted CSV through proxy tunnel, then use legacy enclave path
                # Parse proxy_info with Pydantic model (handles camelCase/snake_case)
                raw_proxy_info = dict(middleware_response.proxy_info or {})
                # Ensure dataset info is available (from build config as fallback)
                if not raw_proxy_info.get('dataset_id') and not raw_proxy_info.get('datasetId') and build_config.datasets:
                    raw_proxy_info['dataset_id'] = build_config.datasets[0].dataset_id
                    raw_proxy_info['archetype_id'] = build_config.datasets[0].archetype_id
                proxy_info = ProxyInfo(**raw_proxy_info)
                # Validate SQL query before sending to proxy
                if proxy_info.sql_query:
                    _validate_sql_query(proxy_info.sql_query)
                proxy_response, proxy_timing = self._step_fetch_from_proxy(
                    job_id, request, public_key, proxy_info
                )
                step_timing['proxy_fetch_ms'] = round((time.time() - t0) * 1000, 2)
                step_timing['proxy_detail'] = proxy_timing
                t0 = time.time()
                success, output, attestation, enclave_timing = self._step_execute_in_enclave(
                    job_id, session_id, encrypted_zip, proxy_response.encrypted_csv
                )
            elif middleware_response and middleware_response.is_direct_db:
                # Validate SQL query before sending to enclave
                _validate_sql_query(middleware_response.sql_query)
                success, output, attestation, enclave_timing = self._step_execute_in_enclave_db_fetch(
                    job_id, session_id, encrypted_zip,
                    middleware_response.encrypted_credentials,
                    middleware_response.sql_query
                )
            else:
                encrypted_csv = middleware_response.encrypted_csv if middleware_response else None
                success, output, attestation, enclave_timing = self._step_execute_in_enclave(
                    job_id, session_id, encrypted_zip, encrypted_csv
                )
            step_timing['enclave_round_trip_ms'] = round((time.time() - t0) * 1000, 2)
            if enclave_timing:
                step_timing['enclave_internal'] = enclave_timing

            # Build result
            execution_time = time.time() - start_time
            step_timing['total_ms'] = round(execution_time * 1000, 2)
            logger.info(f"[TIMING] job={job_id} mode={mode} {step_timing}")
            return self._create_result(job_id, success, output, execution_time, attestation, step_timing)

        except BuildValidationError as e:
            return self._handle_validation_error(job_id, e, start_time)

        except Exception as e:
            return self._handle_execution_error(job_id, e, start_time)

        finally:
            # Clean up to prevent memory leak
            self.cleanup_environment(job_id)

    def _step_validate_build(self, job_id: str, repo_path: str) -> BuildConfig:
        """Step 1: Validate build folder."""
        self._log.info(job_id, "validation", "Validating build folder", progress=10)

        validator = BuildValidator(repo_path)
        config = validator.validate()

        logger.info(f"Build validated: {len(config.datasets)} datasets")
        self._log.info(
            job_id, "validation",
            f"Build validated: {len(config.datasets)} datasets",
            metadata={"script_file": config.script_file, "epsilon": config.epsilon},
            progress=15
        )

        return config

    def _step_get_public_key(self, job_id: str) -> tuple:
        """Step 2: Get public key from enclave."""
        self._log.info(job_id, "enclave", "Requesting public key", progress=20)

        try:
            public_key, session_id = self._enclave_client.get_public_key(job_id)
            self._log.info(job_id, "enclave", f"Received public key (session: {session_id[:20]}...)", progress=25)
            return public_key, session_id
        except Exception as e:
            self._log.error(job_id, "enclave", f"Failed to get public key: {e}", error=e)
            raise

    def _step_fetch_from_middleware(
        self, job_id: str, request: JobExecutionRequest,
        build_config: BuildConfig, public_key: str, mode: str = MiddlewareModes.LEGACY
    ) -> Optional[MiddlewareResponse]:
        """Step 3: Fetch from middleware (mode-dependent).

        In legacy mode: returns MiddlewareResponse with encrypted_csv.
        In direct_db mode: returns MiddlewareResponse with encrypted_credentials + sql_query.
        Returns None if no datasets configured.
        """
        if not build_config.datasets:
            logger.info("No datasets, skipping middleware fetch")
            return None

        self._log.info(job_id, "middleware", f"Fetching for {len(build_config.datasets)} dataset(s) (mode={mode})", progress=30)

        try:
            dataset = build_config.datasets[0]
            middleware_request = MiddlewareRequest(
                dataset_id=dataset.dataset_id,
                archetype_id=dataset.archetype_id,
                public_key=public_key,
                job_id=job_id,
                workspace_id=request.workspace_id,
                metadata={'archetype_path': dataset.archetype_path, 'import_path': dataset.import_path}
            )

            response = self._middleware_client.fetch_encrypted_csv(middleware_request, mode=mode)

            if not response.success:
                raise BuildValidationError(f"Middleware fetch failed: {response.error}")

            if response.is_direct_db:
                if not response.encrypted_credentials or not response.sql_query:
                    raise BuildValidationError("Middleware returned incomplete direct_db response")
                self._log.info(
                    job_id, "middleware",
                    f"Received direct_db response (credentials={len(response.encrypted_credentials)} chars)",
                    progress=40
                )
            elif response.is_proxy:
                if not response.proxy_info:
                    raise BuildValidationError("Middleware returned proxy mode but no proxy_info")
                self._log.info(
                    job_id, "middleware",
                    f"Proxy mode: port={response.proxy_info.get('assigned_port')}, status={response.proxy_info.get('status')}",
                    progress=40
                )
            else:
                if not response.encrypted_csv:
                    raise BuildValidationError("Middleware returned empty CSV")
                self._log.info(
                    job_id, "middleware",
                    f"Received encrypted CSV ({len(response.encrypted_csv)} chars)",
                    progress=40
                )

            return response

        except BuildValidationError:
            raise
        except Exception as e:
            self._log.error(job_id, "middleware", f"Failed to fetch from middleware: {e}", error=e)
            raise BuildValidationError(f"Failed to fetch from middleware: {e}")

    def _step_fetch_from_proxy(
        self, job_id: str, request: JobExecutionRequest,
        public_key: str, proxy_info: ProxyInfo
    ):
        """Step 5a (proxy): Fetch encrypted CSV through proxy tunnel.

        Gets attestation from enclave, then sends query through the proxy tunnel
        to the data owner's database. Returns (ProxyResponse, timings_dict) where
        timings_dict decomposes the proxy ingestion path into sub-stages for
        per-execution evaluation (Rule 2 decomposition).
        """
        self._log.info(job_id, "proxy", "Fetching data through proxy tunnel", progress=85)

        if not self._proxy_client:
            raise RuntimeError("Proxy client not configured but proxy mode requested")

        timings = {}

        try:
            # Stage A: Get attestation document from enclave (with public key bound in user_data).
            t_attest = time.time()
            attestation_doc = ""
            try:
                user_data_json = json.dumps({"public_key": public_key})
                attestation_response = self._enclave_client._send_to_enclave({
                    'operation': EnclaveOperations.GET_ATTESTATION,
                    'user_data': user_data_json
                })
                if attestation_response.get('attestation'):
                    attestation_doc = attestation_response['attestation'].get('attestation_document', '')
                    logger.info(f"[PROXY] Attestation with public key binding: doc_len={len(attestation_doc)}")
                else:
                    logger.warning(f"[PROXY] No attestation in response: {list(attestation_response.keys())}")
            except Exception as e:
                logger.warning(f"[PROXY] Could not get attestation: {e}")
            timings['get_attestation_ms'] = round((time.time() - t_attest) * 1000, 2)

            if not attestation_doc:
                raise RuntimeError(
                    "Attestation document is required for proxy mode but could not be obtained. "
                    "The proxy must verify enclave identity before data is released."
                )

            middleware_request = MiddlewareRequest(
                dataset_id=proxy_info.dataset_id,
                archetype_id=proxy_info.archetype_id,
                public_key=public_key,
                job_id=job_id,
                workspace_id=request.workspace_id
            )

            # Stage B: Proxy health check.
            t_health = time.time()
            proxy_info_dict = proxy_info.model_dump()
            if not self._proxy_client.health_check(proxy_info_dict):
                logger.warning(f"[PROXY] Health check failed for port {proxy_info.assigned_port}, proceeding anyway")
            timings['proxy_health_ms'] = round((time.time() - t_health) * 1000, 2)

            # Stage C: Proxy query over tunnel (includes server-side work + network transit).
            t_query = time.time()
            proxy_response = self._proxy_client.fetch_encrypted_csv(
                request=middleware_request,
                public_key=public_key,
                attestation_doc=attestation_doc,
                proxy_info=proxy_info_dict
            )
            proxy_query_total_ms = round((time.time() - t_query) * 1000, 2)
            timings['proxy_query_total_ms'] = proxy_query_total_ms

            # Stage D: Extract proxy server-side sub-stages from response metadata.
            meta = proxy_response.metadata or {}
            proxy_server_total_ms = float(meta.get('total_duration_ms', 0) or 0)
            timings['proxy_server_total_ms'] = proxy_server_total_ms
            timings['proxy_parse_ms'] = float(meta.get('parse_duration_ms', 0) or 0)
            timings['proxy_hmac_verify_ms'] = float(meta.get('hmac_verify_duration_ms', 0) or 0)
            timings['proxy_attestation_verify_ms'] = float(meta.get('attestation_verify_duration_ms', 0) or 0)
            timings['proxy_sql_validate_ms'] = float(meta.get('sql_validate_duration_ms', 0) or 0)
            timings['proxy_db_query_ms'] = float(meta.get('query_duration_ms', 0) or 0)
            timings['proxy_encrypt_ms'] = float(meta.get('encryption_duration_ms', 0) or 0)
            # Network transit = client-observed round-trip minus server-observed wall clock.
            timings['proxy_network_rtt_ms'] = round(proxy_query_total_ms - proxy_server_total_ms, 2)

            logger.info(f"[PROXY-TIMING] job={job_id} {timings}")

            self._log.info(
                job_id, "proxy",
                f"Received encrypted CSV from proxy ({len(proxy_response.encrypted_csv)} chars)",
                progress=90
            )

            return proxy_response, timings

        except Exception as e:
            self._log.error(job_id, "proxy", f"Failed to fetch from proxy: {e}", error=e)
            raise

    def _step_zip_and_encrypt(self, job_id: str, repo_path: str, public_key: str) -> str:
        """Step 4: Zip and encrypt build folder."""
        build_path = os.path.join(repo_path, 'build')

        # Zip
        self._log.info(job_id, "zip", "Compressing build folder", progress=50)
        try:
            zip_result = self._zip_service.zip_directory(build_path)
            self._log.info(
                job_id, "zip",
                f"Compressed: {zip_result.compressed_size} bytes",
                metadata={"files_count": zip_result.files_count},
                progress=60
            )
        except Exception as e:
            self._log.error(job_id, "zip", f"Failed to zip: {e}", error=e)
            raise

        # Encrypt
        self._log.info(job_id, "encryption", "Encrypting with enclave public key", progress=70)
        try:
            encrypted_data = self._enclave_client.encrypt_zip_data(zip_result.data, public_key)
            self._log.info(job_id, "encryption", f"Encrypted: {len(encrypted_data)} bytes", progress=80)
            return encrypted_data
        except Exception as e:
            self._log.error(job_id, "encryption", f"Failed to encrypt: {e}", error=e)
            raise

    def _step_execute_in_enclave(
        self, job_id: str, session_id: str,
        encrypted_zip: str, encrypted_csv: Optional[str]
    ) -> tuple:
        """Step 5: Execute in enclave. Returns (success, output, attestation, enclave_timing)."""
        self._log.info(job_id, "enclave", "Sending to enclave for execution", progress=85)

        try:
            success, output, attestation = self._enclave_client.send_encrypted_data_to_enclave(
                session_id, encrypted_zip, encrypted_csv
            )
            logger.info(f"Enclave execution completed: success={success}")
            # Extract enclave-internal timing if available
            enclave_timing = None
            if isinstance(attestation, dict) and 'timing' in attestation:
                enclave_timing = attestation.pop('timing')
            if attestation:
                logger.info(f"Attestation received: {attestation.get('attestation', {}).get('attestation_document_length', 0)} bytes")
            return success, output, attestation, enclave_timing
        except Exception as e:
            self._log.error(job_id, "enclave", f"Enclave execution failed: {e}", error=e)
            raise

    def _step_execute_in_enclave_db_fetch(
        self, job_id: str, session_id: str,
        encrypted_zip: str, encrypted_credentials: str, sql_query: str
    ) -> tuple:
        """Step 5 (direct_db): Execute in enclave with DB fetch. Returns (success, output, attestation, enclave_timing)."""
        self._log.info(job_id, "enclave", "Sending to enclave for DB fetch execution", progress=85)

        try:
            success, output, attestation = self._enclave_client.send_encrypted_data_with_db_fetch(
                session_id, encrypted_zip, encrypted_credentials, sql_query
            )
            logger.info(f"Enclave DB fetch execution completed: success={success}")
            enclave_timing = None
            if isinstance(attestation, dict) and 'timing' in attestation:
                enclave_timing = attestation.pop('timing')
            if attestation:
                logger.info(f"Attestation received (db_fetch_inside_enclave)")
            return success, output, attestation, enclave_timing
        except Exception as e:
            self._log.error(job_id, "enclave", f"Enclave DB fetch execution failed: {e}", error=e)
            raise

    def _create_result(
        self, job_id: str, success: bool, output: str,
        execution_time: float, attestation: Optional[Dict] = None,
        step_timing: Optional[Dict] = None
    ) -> ExecutionResult:
        """Create execution result."""
        status = JobStatus.SUCCESS if success else JobStatus.FAILED

        # Pull JAC / ATL artifacts stashed by Step 4b. Keyed by operational
        # job_id so result construction doesn't need to know about
        # job_id_committed. Missing keys are fine — pre-ATL jobs and
        # ATL_ENABLED=false paths leave the dict empty.
        job_state = self._active_jobs.get(job_id, {})
        commitment_hash_bytes = job_state.get('commitment_hash')
        commitment_hash_hex = (
            commitment_hash_bytes.hex()
            if isinstance(commitment_hash_bytes, bytes) else commitment_hash_bytes
        )

        result = ExecutionResult(
            job_id=job_id,
            status=status,
            execution_time=execution_time,
            output=output if success else None,
            error=output if not success else None,
            enclave_cid=getattr(self._enclave_client, 'enclave_cid', None),
            attestation=attestation,
            step_timing=step_timing,
            timestamp=datetime.now(timezone.utc),
            job_id_committed=job_state.get('job_id_committed'),
            researcher_nonce=job_state.get('researcher_nonce'),
            signed_jac=job_state.get('signed_jac'),
            commitment_receipt=job_state.get('commitment_receipt'),
            commitment_hash=commitment_hash_hex,
            ha_receipt=job_state.get('ha_receipt'),
            is_non_compliant=job_state.get('is_non_compliant', False),
        )

        if result.is_success:
            self._log.info(
                job_id, "completed",
                f"Job executed successfully in {execution_time:.2f}s",
                progress=100
            )
        else:
            self._log.error(
                job_id, "failed",
                f"Enclave returned failure after {execution_time:.2f}s",
                error=Exception(output or "Unknown error")
            )

        logger.info(f"Job {job_id} completed: {result.status.value}")
        return result

    def _handle_validation_error(self, job_id: str, error: BuildValidationError, start_time: float) -> ExecutionResult:
        """Handle build validation errors."""
        execution_time = time.time() - start_time
        logger.error(f"Job {job_id} rejected: {error}")

        self._log.error(job_id, "validation", f"Build validation failed: {error}", error=error)

        return ExecutionResult(
            job_id=job_id,
            status=JobStatus.REJECTED,
            execution_time=execution_time,
            output=None,
            error=f"Build validation failed: {error}",
            enclave_cid=None,
            timestamp=datetime.now(timezone.utc)
        )

    def _handle_execution_error(self, job_id: str, error: Exception, start_time: float) -> ExecutionResult:
        """Handle general execution errors."""
        execution_time = time.time() - start_time
        logger.error(f"Job {job_id} failed: {error}", exc_info=True)

        self._log.error(job_id, "execution", f"Job failed: {error}", error=error)

        return ExecutionResult(
            job_id=job_id,
            status=JobStatus.FAILED,
            execution_time=execution_time,
            output=None,
            error=str(error),
            enclave_cid=getattr(self._enclave_client, 'enclave_cid', None),
            timestamp=datetime.now(timezone.utc)
        )

    def prepare_environment(self, request: JobExecutionRequest) -> Dict[str, Any]:
        """Prepare execution environment."""
        job_id = request.job_id
        self._active_jobs[job_id] = {
            'status': 'preparing',
            'start_time': time.time(),
            'request': request
        }

        if not os.path.exists(request.repo_path):
            raise FileNotFoundError(f"Repository not found: {request.repo_path}")

        workspace_dir = f"/app/artifacts/{job_id}"
        os.makedirs(workspace_dir, exist_ok=True)
        self._active_jobs[job_id]['status'] = 'ready'

        return {
            'job_id': job_id,
            'workspace_dir': workspace_dir,
            'repo_path': request.repo_path,
            'status': 'ready'
        }

    def cleanup_environment(self, job_id: str) -> None:
        """Clean up after job execution."""
        if job_id in self._active_jobs:
            del self._active_jobs[job_id]

        workspace_dir = f"/app/artifacts/{job_id}"
        if os.path.exists(workspace_dir):
            shutil.rmtree(workspace_dir, ignore_errors=True)

    def get_status(self, job_id: str) -> Dict[str, Any]:
        """Get execution status for a job."""
        if job_id in self._active_jobs:
            job_info = self._active_jobs[job_id]
            return {
                'job_id': job_id,
                'status': job_info['status'],
                'runtime': time.time() - job_info['start_time'],
                'active': True
            }
        return {'job_id': job_id, 'status': 'unknown', 'active': False}

    @property
    def is_ready(self) -> bool:
        """Check if executor is ready."""
        try:
            if hasattr(self._enclave_client, 'health_check'):
                return self._enclave_client.health_check()
            return True
        except Exception:
            logger.warning("Executor health check failed, reporting not ready")
            return False
