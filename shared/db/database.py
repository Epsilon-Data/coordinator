"""
SQLAlchemy 2.0 database implementation for Epsilon Coordinator
Maintains backward compatibility with existing database.py interface
"""
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
import json

from sqlalchemy import create_engine, select, update, and_
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError

from ..config import config
from .models import Base, JobRequest, Workspace, JobLog

logger = logging.getLogger(__name__)


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
        """Update job status and additional fields"""
        with self.db.get_session() as session:
            # Get the job first
            job = session.get(JobRequest, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found")

            # Update status
            job.status = status
            job.updated_at = datetime.now(timezone.utc)

            # Set completed_at for terminal statuses
            if status in ('success', 'failed', 'rejected'):
                job.completed_at = datetime.now(timezone.utc)

            # Handle additional fields with mapping to maintain compatibility
            field_mapping = {
                'repo_path': 'result_metadata',
                'repo_metadata': 'logs',
                'ai_confidence_score': 'validation_status',
                'ai_reasoning': 'validation_decision',
                'ai_risks': 'ai_logs',
                'ai_result_path': 'execution_output',  # Use execution_output for AI result path
                'execution_result': 'execution_output',
                'execution_result_path': 'execution_output',  # Use execution_output for result paths
                'error_message': 'error_message',
                'attestation': 'attestation',  # Enclave attestation document
                'verification_receipt': 'verification_receipt',  # Server-side verification result
                'execution_metrics': 'execution_metrics',  # Per-step timing metrics
                'enclave_version': 'enclave_version',
                'enclave_pcr0': 'enclave_pcr0'
            }

            for key, value in kwargs.items():
                if key in field_mapping:
                    db_field = field_mapping[key]
                    # Convert complex objects to string
                    if isinstance(value, (dict, list)):
                        setattr(job, db_field, json.dumps(value))
                    else:
                        setattr(job, db_field, str(value) if value is not None else None)
                elif hasattr(job, key):
                    # Direct field mapping
                    setattr(job, key, value)

            session.flush()
            session.refresh(job)

            logger.info(f"Updated job {job_id} status to {status}")

            # Convert to dict for backward compatibility
            return self._job_to_dict(job)

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

    def fetch_cloned_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch cloned jobs for AI agent worker"""
        return self.fetch_jobs_by_status_with_workspace('cloned', limit, claim_status='analyzing')

    def fetch_ai_approved_jobs(self, limit: int = 1) -> List[Dict[str, Any]]:
        """Fetch AI approved jobs for executor worker"""
        return self.fetch_jobs_by_status_with_workspace('ai_approved', limit, claim_status='executing')

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