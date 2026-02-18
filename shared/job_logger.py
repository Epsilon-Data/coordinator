import time
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Generator
from contextlib import contextmanager
from dataclasses import dataclass

from .db import job_repository

log = logging.getLogger(__name__)

@dataclass
class LogEntry:
    job_id: str
    step_type: str
    message: str
    level: str = "info"
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[Exception] = None
    duration_ms: Optional[int] = None
    progress: Optional[int] = None

class JobLogger:
    """
    Stores logs in job_logs table with consistent format.
    """

    def __init__(self, worker_name: str):
        self.worker_name = worker_name

    def info(
        self,
        job_id: str,
        step_type: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        progress: Optional[int] = None
    ) -> Optional[str]:
        """Log info message."""
        return self._save(LogEntry(
            job_id=job_id,
            step_type=step_type,
            message=message,
            level="info",
            metadata=metadata,
            progress=progress
        ))

    def warning(
        self,
        job_id: str,
        step_type: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Log warning message."""
        return self._save(LogEntry(
            job_id=job_id,
            step_type=step_type,
            message=message,
            level="warning",
            metadata=metadata
        ))

    def error(
        self,
        job_id: str,
        step_type: str,
        message: str,
        error: Optional[Exception] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Log error with optional exception details."""
        return self._save(LogEntry(
            job_id=job_id,
            step_type=step_type,
            message=message,
            level="error",
            error=error,
            metadata=metadata
        ))

    @contextmanager
    def step(
        self,
        job_id: str,
        step_type: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Generator[None, None, None]:

        start_time = time.time()

        # Log start
        self.info(job_id, step_type, f"Started: {message}", metadata, progress=0)

        try:
            yield
            # Log success with duration
            duration_ms = int((time.time() - start_time) * 1000)
            self._save(LogEntry(
                job_id=job_id,
                step_type=step_type,
                message=f"Completed: {message}",
                level="info",
                metadata=metadata,
                duration_ms=duration_ms,
                progress=100
            ))
        except Exception as e:
            # Log error with duration
            duration_ms = int((time.time() - start_time) * 1000)
            self._save(LogEntry(
                job_id=job_id,
                step_type=step_type,
                message=f"Failed: {message}",
                level="error",
                error=e,
                metadata=metadata,
                duration_ms=duration_ms
            ))
            raise

    def _save(self, entry: LogEntry) -> Optional[str]:
        """Save log entry to database."""
        try:
            # Build error details if exception provided
            error_details = None
            if entry.error:
                error_details = {
                    "type": type(entry.error).__name__,
                    "message": str(entry.error),
                    "traceback": "".join(traceback.format_exception(
                        type(entry.error), entry.error, entry.error.__traceback__
                    )),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                # Also log to console for visibility
                log.error(f"[{entry.job_id}] {entry.step_type}: {entry.message}", exc_info=entry.error)

            return job_repository.add_detailed_log(
                job_id=entry.job_id,
                worker_name=self.worker_name,
                step_name=entry.step_type,
                step_type=entry.step_type,
                message=entry.message,
                level=entry.level,
                metadata=entry.metadata,
                progress=entry.progress,
                duration_ms=entry.duration_ms,
                error_details=error_details
            )
        except Exception as e:
            log.exception(f"Failed to save log for job {entry.job_id}: {e}")
            return None

# Step types for consistency
class StepTypes:
    # Job lifecycle
    QUEUED = "queued"
    # Clone worker
    CLONE = "clone"
    # AI worker
    AI_ANALYSIS = "ai_analysis"
    AI_APPROVED = "ai_approved"
    AI_REJECTED = "ai_rejected"
    # Executor worker
    EXECUTION = "execution"
    VALIDATION = "validation"
    ENCRYPTION = "encryption"
    # Common
    ERROR = "error"
    COMPLETED = "completed"
