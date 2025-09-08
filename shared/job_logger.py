"""
Job Logger - Provides consistent logging interface for all workers
"""
import time
from datetime import datetime
from typing import Dict, Any, Optional
from .database import job_repository

class JobLogger:
    """Helper class for consistent job logging across all workers"""
    
    def __init__(self, worker_name: str):
        self.worker_name = worker_name
        self._start_times = {}  # Track start times for duration calculation
        
    def log_step_start(
        self, 
        job_id: str, 
        step_name: str, 
        step_type: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        progress: int = 0
    ) -> str:
        """Log the start of a step and track time"""
        # Track start time for duration calculation
        self._start_times[f"{job_id}_{step_name}"] = time.time()
        
        return job_repository.add_detailed_log(
            job_id=job_id,
            worker_name=self.worker_name,
            step_name=step_name,
            step_type=step_type,
            message=message,
            level="info",
            metadata=metadata,
            progress=progress
        )
        
    def log_step_progress(
        self,
        job_id: str,
        step_name: str,
        step_type: str,
        message: str,
        progress: int,
        metadata: Optional[Dict[str, Any]] = None,
        parent_log_id: Optional[str] = None
    ) -> str:
        """Log progress update for a step"""
        return job_repository.add_detailed_log(
            job_id=job_id,
            worker_name=self.worker_name,
            step_name=step_name,
            step_type=step_type,
            message=message,
            level="info",
            metadata=metadata,
            progress=progress,
            parent_log_id=parent_log_id
        )
        
    def log_step_complete(
        self,
        job_id: str,
        step_name: str,
        step_type: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        parent_log_id: Optional[str] = None
    ) -> str:
        """Log completion of a step with duration"""
        # Calculate duration if we have a start time
        duration_ms = None
        time_key = f"{job_id}_{step_name}"
        if time_key in self._start_times:
            duration_ms = int((time.time() - self._start_times[time_key]) * 1000)
            del self._start_times[time_key]
            
        return job_repository.add_detailed_log(
            job_id=job_id,
            worker_name=self.worker_name,
            step_name=step_name,
            step_type=step_type,
            message=message,
            level="info",
            metadata=metadata,
            progress=100,
            duration_ms=duration_ms,
            parent_log_id=parent_log_id
        )
        
    def log_step_error(
        self,
        job_id: str,
        step_name: str,
        step_type: str,
        message: str,
        error: Exception,
        metadata: Optional[Dict[str, Any]] = None,
        parent_log_id: Optional[str] = None
    ) -> str:
        """Log an error in a step"""
        error_details = {
            "error_type": type(error).__name__,
            "error_message": str(error),
            "error_traceback": getattr(error, '__traceback__', None)
        }
        
        # Clean up any tracked start time
        time_key = f"{job_id}_{step_name}"
        if time_key in self._start_times:
            del self._start_times[time_key]
            
        return job_repository.add_detailed_log(
            job_id=job_id,
            worker_name=self.worker_name,
            step_name=step_name,
            step_type=step_type,
            message=message,
            level="error",
            metadata=metadata,
            error_details=error_details,
            parent_log_id=parent_log_id
        )
        
    def log_warning(
        self,
        job_id: str,
        step_name: str,
        step_type: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        parent_log_id: Optional[str] = None
    ) -> str:
        """Log a warning"""
        return job_repository.add_detailed_log(
            job_id=job_id,
            worker_name=self.worker_name,
            step_name=step_name,
            step_type=step_type,
            message=message,
            level="warning",
            metadata=metadata,
            parent_log_id=parent_log_id
        )
        
    def log_debug(
        self,
        job_id: str,
        step_name: str,
        step_type: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        parent_log_id: Optional[str] = None
    ) -> str:
        """Log debug information"""
        return job_repository.add_detailed_log(
            job_id=job_id,
            worker_name=self.worker_name,
            step_name=step_name,
            step_type=step_type,
            message=message,
            level="debug",
            metadata=metadata,
            parent_log_id=parent_log_id
        )


# Common step types for consistency
class StepTypes:
    # Job lifecycle
    PENDING = "pending"
    QUEUED = "queued"
    
    # Clone worker steps
    CLONE_START = "clone_start"
    CLONE_PREPARING = "clone_preparing"
    CLONE_DOWNLOADING = "clone_downloading"
    CLONE_VALIDATING = "clone_validating"
    CLONED = "cloned"
    
    # AI worker steps
    AI_START = "ai_start"
    AI_ANALYZING = "ai_analyzing"
    AI_EVALUATING = "ai_evaluating"
    AI_APPROVED = "ai_approved"
    AI_REJECTED = "ai_rejected"
    
    # Execute worker steps
    EXECUTE_START = "execute_start"
    EXECUTE_PREPARING = "execute_preparing"
    EXECUTE_RUNNING = "execute_running"
    EXECUTE_COLLECTING = "execute_collecting"
    COMPLETED = "completed"
    
    # Error states
    FAILED = "failed"
    ERROR = "error"