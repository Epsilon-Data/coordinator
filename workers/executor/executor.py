"""
Secure executor with Zero Trust enclave integration.

Flow:
1. Validate build configuration
2. Get public key from enclave
3. Fetch encrypted CSV from middleware
4. Package and encrypt build folder
5. Send encrypted payloads to enclave for execution
"""
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from workers.executor.settings import Settings
from workers.executor.interfaces import IEnclaveClient, IExecutor, IMiddlewareClient, MiddlewareRequest, MiddlewareResponse, ProxyResponse
from workers.executor.models import JobExecutionRequest, ExecutionResult, BuildConfig, JobStatus
from workers.executor.exceptions import BuildValidationError
from workers.executor.services import BuildValidator, ZipService
from workers.executor.utils import get_logger
from shared.job_logger import JobLogger

logger = get_logger(__name__)


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
        proxy_client=None
    ):
        self._enclave_client = enclave_client
        self._settings = settings
        self._middleware_client = middleware_client
        self._proxy_client = proxy_client
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
            mode = "direct_db" if use_direct_db else "legacy"
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

            # Step 5: Execute in enclave (branch on mode)
            t0 = time.time()
            if middleware_response and middleware_response.is_proxy:
                # Proxy mode: fetch encrypted CSV through proxy tunnel, then use legacy enclave path
                proxy_info = dict(middleware_response.proxy_info or {})
                # Normalize camelCase → snake_case (middleware may return either)
                if 'assignedPort' in proxy_info and 'assigned_port' not in proxy_info:
                    proxy_info['assigned_port'] = proxy_info['assignedPort']
                if 'proxyToken' in proxy_info and 'proxy_token' not in proxy_info:
                    proxy_info['proxy_token'] = proxy_info['proxyToken']
                # Ensure dataset info is available (from build config as fallback)
                if not proxy_info.get('dataset_id') and build_config.datasets:
                    proxy_info['dataset_id'] = build_config.datasets[0].dataset_id
                    proxy_info['archetype_id'] = build_config.datasets[0].archetype_id
                proxy_response = self._step_fetch_from_proxy(
                    job_id, request, public_key, proxy_info
                )
                step_timing['proxy_fetch_ms'] = round((time.time() - t0) * 1000, 2)
                t0 = time.time()
                success, output, attestation, enclave_timing = self._step_execute_in_enclave(
                    job_id, session_id, encrypted_zip, proxy_response.encrypted_csv
                )
            elif middleware_response and middleware_response.is_direct_db:
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
        build_config: BuildConfig, public_key: str, mode: str = "legacy"
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
                    f"Proxy mode: port={response.proxy_info.get('assignedPort')}, status={response.proxy_info.get('status')}",
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
        public_key: str, proxy_info: dict
    ) -> ProxyResponse:
        """Step 5a (proxy): Fetch encrypted CSV through proxy tunnel.

        Gets attestation from enclave, then sends query through the proxy tunnel
        to the data owner's database. Returns ProxyResponse with encrypted CSV.
        """
        self._log.info(job_id, "proxy", "Fetching data through proxy tunnel", progress=85)

        if not self._proxy_client:
            raise RuntimeError("Proxy client not configured but proxy mode requested")

        try:
            import hashlib
            import base64

            # Get attestation document with public key hash bound to it.
            # This lets the proxy verify:
            #   1. The attestation is from a real Nitro enclave (AWS root cert)
            #   2. The public key hash in user_data matches the public key we send
            #   3. Therefore the public key was generated inside the enclave
            attestation_doc = ""
            try:
                public_key_hash = hashlib.sha256(public_key.encode()).digest()
                attestation_response = self._enclave_client._send_to_enclave({
                    'operation': 'get_attestation',
                    'user_data': base64.b64encode(public_key_hash).decode()
                })
                if attestation_response.get('attestation'):
                    attestation_doc = attestation_response['attestation'].get('attestation_document', '')
                logger.info(f"[PROXY] Attestation with public key binding generated")
            except Exception as e:
                logger.warning(f"[PROXY] Could not get attestation document: {e}")

            # Build middleware request for proxy
            dataset = None
            if hasattr(request, 'datasets') and request.datasets:
                dataset = request.datasets[0]

            middleware_request = MiddlewareRequest(
                dataset_id=proxy_info.get('dataset_id', ''),
                archetype_id=proxy_info.get('archetype_id', ''),
                public_key=public_key,
                job_id=job_id,
                workspace_id=request.workspace_id
            )

            # Health check before query
            if not self._proxy_client.health_check(proxy_info):
                logger.warning(f"[PROXY] Health check failed for port {proxy_info.get('assigned_port')}, proceeding anyway")

            proxy_response = self._proxy_client.fetch_encrypted_csv(
                request=middleware_request,
                public_key=public_key,
                attestation_doc=attestation_doc,
                proxy_info=proxy_info
            )

            self._log.info(
                job_id, "proxy",
                f"Received encrypted CSV from proxy ({len(proxy_response.encrypted_csv)} chars)",
                progress=90
            )

            return proxy_response

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
        result = ExecutionResult(
            job_id=job_id,
            status=status,
            execution_time=execution_time,
            output=output if success else None,
            error=output if not success else None,
            enclave_cid=getattr(self._enclave_client, 'enclave_cid', None),
            attestation=attestation,
            step_timing=step_timing,
            timestamp=datetime.now(timezone.utc)
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
        import shutil

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
