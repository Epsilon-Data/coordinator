"""
Executor worker implementing high-level logic:
fetch job -> load repo -> get public_key -> zip & encrypt -> send to enclave
"""
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from sqlalchemy import text

try:
    from epsilon_verifier import verify_attestation
except ImportError:
    verify_attestation = None

from shared.db import db, job_repository
from shared.base_worker import ExecutorWorkerBase
from workers.executor.utils import get_logger, setup_logging
from workers.executor.interfaces import IExecutor
from workers.executor.models import JobExecutionRequest, JobStatus
from workers.executor.settings import get_settings, validate_and_raise
from workers.executor.exceptions import ConfigurationError
from workers.executor.factories import ExecutorFactory
from workers.executor.atl_client import ATLClient
from workers.executor.jac import sign_jac, compute_script_hash, compute_context_hash


class ExecutorWorker(ExecutorWorkerBase):
    """Database polling worker for processing job execution requests."""

    def __init__(self, executor: Optional[IExecutor] = None):
        """
        Initialize executor worker.

        Args:
            executor: Optional job executor instance (if None, will create one)
        """
        super().__init__("ExecutorWorker")

        self._settings = get_settings()

        # Validate environment
        self._validate_environment()

        # Create executor if not provided
        if executor is None:
            executor = self._create_executor()

        self._executor = executor

        # Initialize ATL client (opt-in via ATL_ENABLED=true)
        self._atl_client = None
        if self._settings.atl.is_configured:
            self._atl_client = ATLClient.from_env()
            # Pass ATL client to executor for pre-execution nonce/context steps
            if self._atl_client and hasattr(self._executor, '_atl_client'):
                self._executor._atl_client = self._atl_client

        logger = get_logger(__name__)

        if self._atl_client:
            logger.info("[ATL] Attestation Transparency Log integration enabled (url=%s)", self._settings.atl.url)
        else:
            logger.info("[ATL] ATL integration disabled (ATL_ENABLED=false or not configured)")

        # Check if executor is ready — wait instead of crashing
        if not self._executor.is_ready:
            logger.warning("Enclave not ready, will retry on each polling cycle")

        logger.info(f"ExecutorWorker initialized for worker {self._settings.worker_id} in polling mode")

        # Reset orphaned executing jobs back to cloned on boot
        self._recover_orphaned_jobs()

        # Stamp boot_ready_at in boot_events table for boot time measurement
        self._stamp_boot_ready()

    def _recover_orphaned_jobs(self) -> None:
        """Reset jobs stuck in 'executing' back to 'cloned' on boot.

        Handles the case where the enclave goes offline mid-execution
        and jobs get orphaned in the 'executing' state.
        """
        logger = get_logger(__name__)
        try:
            from shared.db.models import JobRequest
            from sqlalchemy import select
            with db.get_session() as session:
                orphaned = session.execute(
                    select(JobRequest).where(JobRequest.status == 'executing')
                ).scalars().all()
                for job in orphaned:
                    job.status = 'cloned'
                    job.updated_at = datetime.now(timezone.utc)
                    logger.warning(f"[BOOT] Recovered orphaned job {job.job_id} → cloned")
                if not orphaned:
                    logger.info("[BOOT] No orphaned jobs found")
        except Exception as e:
            logger.warning(f"[BOOT] Could not recover orphaned jobs: {e}")

    def _stamp_boot_ready(self) -> None:
        """Update the most recent boot_events row with boot_ready_at timestamp."""
        try:
            with db.get_session() as session:
                session.execute(
                    text(
                        "UPDATE boot_events SET boot_ready_at = NOW(), "
                        "boot_duration_ms = EXTRACT(EPOCH FROM (NOW() - boot_requested_at))::integer * 1000 "
                        "WHERE id = (SELECT id FROM boot_events WHERE boot_ready_at IS NULL ORDER BY id DESC LIMIT 1)"
                    )
                )
            get_logger(__name__).info("[BOOT] Stamped boot_ready_at in boot_events")
        except Exception as e:
            get_logger(__name__).warning(f"[BOOT] Could not stamp boot_ready_at: {e}")

    def _validate_environment(self) -> None:
        """Validate the runtime environment configuration."""
        logger = get_logger(__name__)
        logger.info("Validating environment configuration...")

        try:
            validate_and_raise(self._settings)
            logger.info("[SUCCESS] Environment validation passed")

            # Log key configuration
            logger.info(f"Worker ID: {self._settings.worker_id}")
            logger.info(f"Environment: {self._settings.environment}")
            logger.info(f"Use local enclave: {self._settings.enclave.use_local_client}")
            logger.info(f"Storage path: {self._settings.storage.shared_storage_path}")

        except Exception as e:
            logger.error(f"[ERROR] Configuration validation failed: {e}")
            raise ConfigurationError(f"Configuration validation failed: {e}")

    def _create_executor(self) -> IExecutor:
        """Create executor instance."""
        logger = get_logger(__name__)
        logger.info("Creating production executor")
        return ExecutorFactory.create_executor(self._settings)

    def _verify_attestation(self, job_id: str, attestation: Any, logger) -> Optional[str]:
        """Verify attestation document and return a JSON verification receipt."""
        try:
            if verify_attestation is None:
                raise ImportError("epsilon_verifier not installed")

            # Extract base64 attestation document from the stored JSON
            att_doc = attestation
            if isinstance(att_doc, str):
                att_doc = json.loads(att_doc)

            b64_doc = ""
            if isinstance(att_doc, dict):
                inner = att_doc.get("attestation", {})
                if isinstance(inner, dict):
                    b64_doc = inner.get("attestation_document", "")

            if not b64_doc:
                logger.warning(f"[ATTESTATION] No attestation_document found in attestation for {job_id}")
                return json.dumps({
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                    "valid": False,
                    "error": "No attestation_document found in attestation payload",
                })

            vr = verify_attestation(
                attestation_doc=b64_doc,
                expected_pcr0=os.environ.get("EXPECTED_PCR0"),
                expected_pcr1=os.environ.get("EXPECTED_PCR1"),
                expected_pcr2=os.environ.get("EXPECTED_PCR2"),
            )

            return json.dumps({
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "valid": vr.valid,
                "checks": {
                    "syntax_valid": vr.syntax_valid,
                    "certificate_chain_valid": vr.certificate_chain_valid,
                    "signature_valid": vr.aws_signature_valid,
                    "pcr_verified": vr.pcr_verified,
                    "output_verified": vr.output_verified,
                },
                "pcrs": {"pcr0": vr.pcr0, "pcr1": vr.pcr1, "pcr2": vr.pcr2},
                "module_id": vr.module_id,
                "timestamp": vr.timestamp.isoformat() if vr.timestamp else None,
                "verifier_version": "1.0.0",
                "error": vr.error,
                "timing": vr.timing,
            })
        except Exception as e:
            logger.error(f"[ATTESTATION] Verification failed for {job_id}: {e}")
            return json.dumps({
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "valid": False,
                "error": str(e),
            })

    def _submit_to_atl(self, job_id: str, attestation: Any, job: Dict[str, Any], logger) -> Optional[Dict[str, Any]]:
        """Submit a High-Assurance entry to the ATL after successful execution.

        Extracts the raw attestation document and submits it as an HA entry.
        Returns the ATL response (containing inclusion receipt) or None on failure.
        """
        try:
            import base64

            # Extract the raw attestation document bytes
            att_doc = attestation
            if isinstance(att_doc, str):
                att_doc = json.loads(att_doc)

            b64_doc = ""
            if isinstance(att_doc, dict):
                inner = att_doc.get("attestation", {})
                if isinstance(inner, dict):
                    b64_doc = inner.get("attestation_document", "")

            if not b64_doc:
                logger.warning(f"[ATL] No attestation_document for ATL submission, job {job_id}")
                return None

            raw_attestation = base64.b64decode(b64_doc)

            # Extract nonce from attestation proof data (if available)
            nonce = b'\x00' * 32  # fallback
            proof = att_doc.get("attestation", {}).get("proof", {})
            if proof and proof.get("nonce"):
                nonce = base64.b64decode(proof["nonce"])

            # Submit HA entry
            success, result = self._atl_client.submit_ha_entry(
                job_id=job_id,
                tee_platform="aws-nitro",
                attestation_doc=raw_attestation,
                nonce=nonce,
            )

            if success:
                logger.info(f"[ATL] HA entry submitted: leaf={result.get('leaf_index')}")
                return result
            else:
                logger.warning(f"[ATL] HA entry submission failed for job {job_id}")
                return None

        except Exception as e:
            logger.error(f"[ATL] Error submitting to ATL for job {job_id}: {e}")
            return None

    def process_job(self, job: Dict[str, Any]) -> bool:
        """
        High-level logic: fetch job -> load repo -> get public_key -> zip & encrypt -> send to enclave
        """
        job_id = job['job_id']
        logger = get_logger(__name__)

        try:
            logger.info(f"[JOB] ========== PROCESSING JOB {job_id} ==========")
            logger.info(f"[JOB] Job details: {job}")
            logger.info(f"[JOB] Starting job processing pipeline...")

            # Step 1: Load cloned repository
            if '/' in job_id or '\\' in job_id or '..' in job_id:
                raise ValueError(f"Invalid job_id: {job_id}")
            repo_path = f"{self._settings.storage.shared_storage_path}/repositories/{job_id}"
            if not os.path.exists(repo_path):
                raise FileNotFoundError(f"Repository not found: {repo_path}")

            logger.info(f"[REPO] Found repository at {repo_path}")

            # Step 2: Check enclave readiness before changing status
            if not self._executor.is_ready:
                logger.warning(f"[JOB] Enclave not ready — skipping job {job_id}, will retry next cycle")
                return False

            # Step 3: Mark job as executing (claimed)
            job_repository.update_job_status(job_id=job_id, status='executing')
            logger.info(f"[JOB] Job {job_id} status → executing")

            # Step 4: Create job request
            job_request = JobExecutionRequest(
                job_id=job_id,
                repo_path=repo_path,
                workspace_id=job['workspace_id'],
                ai_decision={
                    'commit_sha': job['commit_sha'],
                    'github_repo': job['github_repo'],
                    'github_branch': job['github_branch']
                },
                metadata={
                    'user_id': job['user_id'],
                    'commit_message': job['commit_message'],
                    'commit_author': job['commit_author'],
                    'created_at': str(job['created_at'])
                }
            )

            # Step 5: Execute using high-level logic (get public_key, zip, encrypt, send to enclave)
            result = self._executor.execute(job_request)

            # Update job status based on result
            if result.is_success:
                # Verify attestation document if present
                verification_receipt = None
                if result.attestation:
                    verification_receipt = self._verify_attestation(job_id, result.attestation, logger)

                # Submit to ATL if enabled and attestation is present
                atl_receipt = None
                if self._atl_client and result.attestation:
                    atl_receipt = self._submit_to_atl(job_id, result.attestation, job, logger)

                # Extract enclave version and PCR0 for metadata tracking
                enclave_version = os.environ.get("ENCLAVE_VERSION", "unknown")
                enclave_pcr0 = None
                if verification_receipt:
                    try:
                        vr_data = json.loads(verification_receipt) if isinstance(verification_receipt, str) else verification_receipt
                        enclave_pcr0 = vr_data.get("pcrs", {}).get("pcr0")
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        pass

                job_repository.update_job_status(
                    job_id=job_id,
                    status=JobStatus.SUCCESS.value,
                    execution_result=result.output,
                    attestation=result.attestation,
                    verification_receipt=verification_receipt,
                    execution_metrics=result.step_timing,
                    enclave_version=enclave_version,
                    enclave_pcr0=enclave_pcr0
                )
                logger.info(f"[SUCCESS] Job {job_id} completed")
                if result.attestation:
                    logger.info(f"[ATTESTATION] Stored attestation for job {job_id}")
                if verification_receipt:
                    logger.info(f"[ATTESTATION] Verification receipt stored for job {job_id}")
                if atl_receipt:
                    logger.info(f"[ATL] Inclusion receipt stored for job {job_id}")
                return True
            elif result.status == JobStatus.REJECTED:
                # Build validation failed - no build folder or invalid build.yml
                job_repository.update_job_status(
                    job_id=job_id,
                    status=JobStatus.REJECTED.value,
                    error_message=result.error
                )
                logger.warning(f"[REJECTED] Job {job_id} rejected: {result.error}")
                return False
            else:
                # Submit Low-Assurance failure entry to ATL
                if self._atl_client:
                    error_class = result.status.value if result.status else "unknown"
                    self._atl_client.submit_la_entry(
                        job_id=job_id,
                        error_class=error_class,
                        error_detail=result.error or "unknown error",
                    )
                    logger.info(f"[ATL] Failure entry submitted for job {job_id}")

                job_repository.update_job_status(
                    job_id=job_id,
                    status=JobStatus.FAILED.value,
                    error_message=result.error
                )
                logger.error(f"[FAILED] Job {job_id} failed: {result.error}")
                return False

        except Exception as e:
            logger.error(f"[ERROR] Job {job_id} failed: {str(e)}")
            # Submit LA entry for unexpected failures
            if self._atl_client:
                try:
                    self._atl_client.submit_la_entry(
                        job_id=job_id,
                        error_class=type(e).__name__,
                        error_detail=str(e),
                    )
                except Exception:
                    pass
            job_repository.update_job_status(job_id=job_id, status=JobStatus.FAILED.value, error_message=str(e))
            return False


def main() -> None:
    """Main entry point."""
    # Set up logging
    logger = setup_logging(
        name='epsilon.executor',
        level=os.getenv('LOG_LEVEL', 'INFO')
    )

    logger.info("[START] Starting Epsilon Executor Worker")
    logger.info("=" * 60)

    try:
        # Create and start worker
        worker = ExecutorWorker()
        logger.info("[WORKER] Starting worker loop...")
        worker.run()

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except ConfigurationError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("[WORKER] Executor worker stopped")


if __name__ == "__main__":
    main()
