"""
SQLAlchemy 2.0 database implementation for Epsilon Coordinator
Maintains backward compatibility with existing database.py interface
"""
import hashlib
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
import json

from sqlalchemy import create_engine, select, update, and_, text, func
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from ..config import config
from .models import Base, JobRequest, Workspace, JobLog, JobOutput

logger = logging.getLogger(__name__)

# error_message shown while the error text of a real-data run is held for review.
# Must stay byte-identical to HELD_ERROR_MESSAGE in ResearchWorkspace shared/schema.ts.
HELD_ERROR_MESSAGE = "Execution failed. Error details are held until a reviewer approves their release."

# Real-data output is only ever stored in job_outputs (complete_job_with_held_output),
# never in job_requests.execution_output.
EXECUTION_OUTPUT_FIELDS = ('ai_result_path', 'execution_result', 'execution_result_path', 'execution_output')

# update_job_status keyword -> job_requests column
FIELD_MAPPING = {
    'repo_path': 'result_metadata',
    'repo_metadata': 'logs',
    'ai_confidence_score': 'validation_status',
    'ai_reasoning': 'validation_decision',
    'ai_risks': 'ai_logs',
    'error_message': 'error_message',
    'attestation': 'attestation',  # Enclave attestation document
    'verification_receipt': 'verification_receipt',  # Server-side verification result
    'execution_metrics': 'execution_metrics',  # Per-step timing metrics
    'enclave_version': 'enclave_version',
    'enclave_pcr0': 'enclave_pcr0'
}


class ProjectChangedError(Exception):
    """The job already has a different project or archetype recorded (RG025)."""


def _reject_execution_output(fields: Dict[str, Any]) -> None:
    passed = [key for key in EXECUTION_OUTPUT_FIELDS if key in fields]
    if passed:
        raise ValueError(
            f"{', '.join(passed)} would write execution_output; "
            "hold real-data output with complete_job_with_held_output instead"
        )


def _merge_execution_metrics(existing: Optional[str], new: Any) -> Optional[str]:
    """Merge new metrics into the stored JSON object: existing keys kept, new keys win.

    Keeps keys written by other workers (e.g. the AI worker's ai_decision). A stored
    value that is not a JSON object is replaced.
    """
    if isinstance(new, str):
        try:
            parsed = json.loads(new)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            new = parsed
    if new is None:
        return existing
    if not isinstance(new, dict):
        return json.dumps(new) if isinstance(new, list) else str(new)

    try:
        merged = json.loads(existing) if existing else {}
    except (json.JSONDecodeError, TypeError):
        merged = {}
    if not isinstance(merged, dict):
        merged = {}
    merged.update(new)
    return json.dumps(merged)


class Database:
    """SQLAlchemy 2.0 Database connection manager"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = create_engine(
            database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_timeout=30,
            connect_args={"connect_timeout": 10, "options": "-c statement_timeout=30000"},
            # Bound values (e.g. held output) never appear in SQLAlchemy errors or logs
            hide_parameters=True,
            echo=False  # Set to True for SQL debugging
        )
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine
        )

    @contextmanager
    def get_session(self):
        """Get a database session with automatic commit/rollback"""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self):
        """Close the database engine"""
        self.engine.dispose()


class JobRepository:
    """Repository for job-related database operations using SQLAlchemy 2.0"""

    def __init__(self, db: Database):
        self.db = db

    def update_job_status(
        self,
        job_id: str,
        status: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Update job status and additional fields.

        execution_metrics is merged into the stored JSON object; release_status
        is set directly. Real-data output cannot be written here (ValueError).
        """
        _reject_execution_output(kwargs)
        with self.db.get_session() as session:
            # Get the job first, row-locked so the metrics merge can't lose a concurrent write
            job = session.get(JobRequest, job_id, with_for_update={'key_share': True})
            if not job:
                raise ValueError(f"Job {job_id} not found")

            # Update status
            job.status = status
            job.updated_at = datetime.now(timezone.utc)

            # Set completed_at for terminal statuses
            if status in ('success', 'failed', 'rejected'):
                job.completed_at = datetime.now(timezone.utc)

            self._apply_job_fields(job, kwargs)

            session.flush()
            session.refresh(job)

            logger.info(f"Updated job {job_id} status to {status}")

            # Convert to dict for backward compatibility
            return self._job_to_dict(job)

    def complete_job_with_held_output(
        self,
        job_id: str,
        *,
        status: str,
        kind: str,
        content: str,
        attested_output_hash: Optional[str] = None,
        error_message: Optional[str] = None,
        **fields
    ) -> Dict[str, Any]:
        """Finish a real-data run, holding its result or error text for review.

        One transaction on the locked job row. Once the release is decided the
        new run is discarded: the held output, attestation, receipts, error_message
        and output_sha256 stay as reviewed, and status follows the held output.
        A held result is never replaced by an error; otherwise an undecided row
        is replaced. Writing a result also sets error_message (normally clearing
        an earlier held message). release_status becomes 'pending' unless it is
        final (released/withheld). Other fields are applied as in
        update_job_status. execution_output is never written.
        """
        if kind not in ('result', 'error'):
            raise ValueError(f"Unknown held output kind: {kind}")
        _reject_execution_output(fields)
        for key in ('release_status', 'output_sha256'):
            if key in fields:
                raise ValueError(f"{key} is set by complete_job_with_held_output itself")
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

        with self.db.get_session() as session:
            job = session.get(JobRequest, job_id, with_for_update=True)
            if not job:
                raise ValueError(f"Job {job_id} not found")

            # A final status without a decision is a job released before the gate existed
            decided = job.release_status in ('released', 'withheld') or session.execute(
                text("SELECT 1 FROM release_decisions WHERE job_id = :job_id"), {'job_id': job_id}
            ).first() is not None
            held = session.get(JobOutput, job_id)

            if decided:
                logger.warning(
                    f"Job {job_id} release already decided ({job.release_status}); "
                    "the new run is discarded and the reviewed output, attestation and receipts are kept"
                )
            elif held is not None and held.kind == 'result' and kind == 'error':
                logger.warning(f"Job {job_id} already holds a result; not replacing it with an error")
            else:
                if held is None:
                    held = JobOutput(job_id=job_id)
                    session.add(held)
                else:
                    held.created_at = func.now()
                held.kind = kind
                held.content = content
                held.content_sha256 = content_sha256
                held.attested_output_hash = attested_output_hash
                if kind == 'result':
                    job.output_sha256 = content_sha256
                    # A held message from an earlier failed run must not linger next to the result
                    job.error_message = error_message

            now = datetime.now(timezone.utc)
            job.updated_at = now
            job.completed_at = now
            if decided:
                # The status describes the output that was reviewed, not the discarded run
                job.status = {'result': 'success', 'error': 'failed'}.get(held.kind, status) if held else status
            else:
                job.status = status
                # A held output must always be reviewable
                if job.release_status not in ('released', 'withheld'):
                    job.release_status = 'pending'
                if error_message is not None:
                    job.error_message = error_message
                self._apply_job_fields(job, fields)

            session.flush()
            session.refresh(job)

            logger.info(f"Completed job {job_id} with status {status}; {kind} output held for review")
            return self._job_to_dict(job)

    def record_job_project(self, job_id: str, project_id: str, archetype_id: str) -> None:
        """Record the job's platform project and archetype, before real data is requested.

        Recording the same values again changes nothing. A recorded value cannot change:
        the database refuses (RG025), raised here as ProjectChangedError.
        """
        try:
            with self.db.get_session() as session:
                job = session.get(JobRequest, job_id)
                if not job:
                    raise ValueError(f"Job {job_id} not found")
                job.project_id = project_id
                job.archetype_id = archetype_id
                job.updated_at = datetime.now(timezone.utc)
                session.flush()
        except DBAPIError as e:
            if getattr(e.orig, 'pgcode', None) == 'RG025':
                raise ProjectChangedError(f"Job {job_id} already has another project or archetype recorded") from e
            raise
        logger.info(f"Recorded project {project_id} (archetype {archetype_id}) for job {job_id}")

    def get_submitter_sub(self, job_id: str) -> Optional[str]:
        """users.external_id of the job's submitter (their Keycloak sub), or None if the job or user is missing."""
        with self.db.get_session() as session:
            row = session.execute(
                text("SELECT u.external_id FROM job_requests jr JOIN users u ON u.id = jr.user_id "
                     "WHERE jr.job_id = :job_id"),
                {'job_id': job_id},
            ).first()
            return row[0] if row else None

    @staticmethod
    def _apply_job_fields(job: JobRequest, fields: Dict[str, Any]) -> None:
        """Set update_job_status keyword fields, with mapping to maintain compatibility"""
        for key, value in fields.items():
            if key == 'execution_metrics':
                job.execution_metrics = _merge_execution_metrics(job.execution_metrics, value)
            elif key in FIELD_MAPPING:
                db_field = FIELD_MAPPING[key]
                # Convert complex objects to string
                if isinstance(value, (dict, list)):
                    setattr(job, db_field, json.dumps(value))
                else:
                    setattr(job, db_field, str(value) if value is not None else None)
            elif hasattr(job, key):
                # Direct field mapping
                setattr(job, key, value)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job by job_id"""
        with self.db.get_session() as session:
            job = session.get(JobRequest, job_id)
            if job:
                return self._job_to_dict(job)
            return None

    def add_job_log(
        self,
        job_id: str,
        worker_name: str,
        message: str,
        level: str = "info",
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Add a log entry for a job by appending to ai_logs field"""
        with self.db.get_session() as session:
            job = session.get(JobRequest, job_id)

            if job:
                # Create log entry
                log_entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "worker": worker_name,
                    "level": level,
                    "message": message,
                    "metadata": metadata or {}
                }

                # Append to existing logs
                current_logs = job.ai_logs or ""
                new_logs = current_logs + "\n" + json.dumps(log_entry) if current_logs else json.dumps(log_entry)

                job.ai_logs = new_logs
                job.updated_at = datetime.now(timezone.utc)

                logger.debug(f"Added log entry for job {job_id}: {message}")
            else:
                logger.warning(f"Job {job_id} not found for logging")

    def add_detailed_log(
        self,
        job_id: str,
        worker_name: str,
        step_name: str,
        step_type: str,
        message: str,
        level: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
        progress: Optional[int] = None,
        duration_ms: Optional[int] = None,
        error_details: Optional[Dict[str, Any]] = None,
        parent_log_id: Optional[str] = None
    ) -> str:
        """Add a detailed log entry to the job_logs table"""
        with self.db.get_session() as session:
            log_entry = JobLog(
                job_id=job_id,
                worker_name=worker_name,
                step_name=step_name,
                step_type=step_type,
                level=level,
                message=message,
                progress=progress,
                duration_ms=duration_ms,
                parent_log_id=parent_log_id
            )

            # Set metadata and error details using properties
            log_entry.metadata_dict = metadata
            log_entry.error_details_dict = error_details

            session.add(log_entry)
            session.flush()
            session.refresh(log_entry)

            log_id = str(log_entry.id)
            logger.debug(f"Added detailed log {log_id} for job {job_id}: {step_type} - {message}")
            return log_id

    def fetch_jobs_by_status_with_workspace(
        self,
        status: str,
        limit: int = 10,
        claim_status: Optional[str] = None,
        additional_fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch jobs by status with workspace information using row-level locking.

        If claim_status is provided, atomically updates matched rows to that status
        within the same transaction to prevent duplicate processing by concurrent workers.
        """
        with self.db.get_session() as session:
            # Build the query with join
            stmt = (
                select(JobRequest, Workspace)
                .join(Workspace, JobRequest.workspace_id == Workspace.id)
                .where(JobRequest.status == status)
                .order_by(JobRequest.created_at)
                .limit(limit)
                .with_for_update(of=JobRequest, skip_locked=True)
            )

            result = session.execute(stmt).all()

            jobs = []
            now = datetime.now(timezone.utc)
            for job_request, workspace in result:
                # Claim the row in the same transaction to prevent races
                if claim_status:
                    job_request.status = claim_status
                    job_request.updated_at = now

                job_dict = self._job_to_dict(job_request)
                # Add workspace fields
                job_dict['github_repo'] = workspace.github_repo
                job_dict['github_branch'] = workspace.github_branch
                jobs.append(job_dict)

            if jobs:
                logger.debug(f"Fetched {len(jobs)} jobs with status '{status}'"
                             + (f" → claimed as '{claim_status}'" if claim_status else ""))

            return jobs

    def fetch_pending_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch pending jobs for job-fetcher worker"""
        return self.fetch_jobs_by_status_with_workspace('pending', limit, claim_status='queued')

    def fetch_queued_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch queued jobs for clone worker"""
        return self.fetch_jobs_by_status_with_workspace('queued', limit, claim_status='cloning')

    def update_job_ai_results(
        self,
        job_id: str,
        ai_confidence_score: float = None,
        ai_reasoning: str = None,
        ai_risks: list = None,
        ai_result_path: str = None,
        ai_decision: str = None,
    ) -> None:
        """Store AI analysis results as metadata without changing job status."""
        with self.db.get_session() as session:
            job = session.get(JobRequest, job_id, with_for_update={'key_share': True})
            if not job:
                raise ValueError(f"Job {job_id} not found")

            job.updated_at = datetime.now(timezone.utc)

            if ai_confidence_score is not None:
                job.validation_status = str(ai_confidence_score)
            if ai_reasoning is not None:
                job.validation_decision = ai_reasoning
            if ai_risks is not None:
                job.ai_logs = json.dumps(ai_risks) if isinstance(ai_risks, list) else str(ai_risks)
            if ai_decision is not None:
                # The AI decision (ai_approved/ai_rejected) is advisory only — it does
                # not affect execution or release. Also kept in execution_metrics for
                # readers that predate the ai_decision column.
                job.ai_decision = ai_decision
                existing = job.execution_metrics
                try:
                    metrics = json.loads(existing) if existing else {}
                except (json.JSONDecodeError, TypeError):
                    metrics = {}
                metrics['ai_decision'] = ai_decision
                job.execution_metrics = json.dumps(metrics)

            session.commit()
            logger.info(f"Stored AI results for job {job_id}: decision={ai_decision}")

    def fetch_cloned_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch cloned jobs for AI agent worker that haven't been analyzed yet."""
        with self.db.get_session() as session:
            stmt = (
                select(JobRequest, Workspace)
                .join(Workspace, JobRequest.workspace_id == Workspace.id)
                .where(JobRequest.status.in_(['cloned', 'executing', 'success']))
                .where(JobRequest.validation_status.is_(None))  # not yet analyzed by AI
                .order_by(JobRequest.created_at)
                .limit(limit)
            )
            result = session.execute(stmt).all()

            if not result:
                return []

            jobs = []
            for job, workspace in result:
                job_dict = self._job_to_dict(job)
                job_dict['github_repo'] = workspace.github_repo
                job_dict['github_branch'] = workspace.github_branch
                jobs.append(job_dict)

            return jobs

    def fetch_ai_approved_jobs(self, limit: int = 1) -> List[Dict[str, Any]]:
        """Fetch AI approved jobs for executor worker (legacy)"""
        return self.fetch_jobs_by_status_with_workspace('ai_approved', limit, claim_status='executing')

    def fetch_cloned_jobs_for_execution(self, limit: int = 1) -> List[Dict[str, Any]]:
        """Fetch cloned jobs for executor worker (AI runs independently)"""
        return self.fetch_jobs_by_status_with_workspace('cloned', limit, claim_status='executing')

    def _job_to_dict(self, job: JobRequest) -> Dict[str, Any]:
        """Convert JobRequest model to dictionary for backward compatibility"""
        return {
            'job_id': job.job_id,
            'workspace_id': str(job.workspace_id) if job.workspace_id else None,
            'user_id': str(job.user_id) if job.user_id else None,
            'status': job.status,
            'commit_sha': job.commit_sha,
            'commit_message': job.commit_message,
            'commit_author': job.commit_author,
            'result_metadata': job.result_metadata,
            'logs': job.logs,
            'ai_logs': job.ai_logs,
            'validation_status': job.validation_status,
            'validation_decision': job.validation_decision,
            'execution_output': job.execution_output,
            'error_message': job.error_message,
            'attestation': job.attestation,
            'verification_receipt': job.verification_receipt,
            'execution_metrics': job.execution_metrics,
            'enclave_version': job.enclave_version,
            'enclave_pcr0': job.enclave_pcr0,
            'job_id_committed': job.job_id_committed,
            'researcher_nonce': job.researcher_nonce,
            'release_status': job.release_status,
            'ai_decision': job.ai_decision,
            'output_sha256': job.output_sha256,
            'project_id': job.project_id,
            'archetype_id': job.archetype_id,
            'created_at': job.created_at,
            'updated_at': job.updated_at,
            # Convenience properties
            'repo_path': job.repo_path,
            'repo_metadata': job.repo_metadata,
            'ai_confidence_score': job.ai_confidence_score,
            'ai_reasoning': job.ai_reasoning
        }


# Global instances - maintains the same interface as the old system
db = Database(config.database_url)
job_repository = JobRepository(db)