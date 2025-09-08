"""
API endpoints for retrieving job logs
Can be integrated with FastAPI or Flask
"""
from typing import Optional, List, Dict, Any
from .database import job_repository


class JobLogsAPI:
    """API for accessing job logs"""
    
    @staticmethod
    def get_job_logs(
        job_id: str,
        step_type: Optional[str] = None,
        level: Optional[str] = None,
        limit: Optional[int] = 100,
        offset: Optional[int] = 0
    ) -> Dict[str, Any]:
        """
        Get detailed logs for a job
        
        Args:
            job_id: The job ID
            step_type: Optional filter by step type (clone, ai_analysis, execution, etc.)
            level: Optional filter by log level (info, warning, error, etc.)
            limit: Maximum number of logs to return
            offset: Offset for pagination
            
        Returns:
            Dictionary with logs and metadata
        """
        try:
            logs = job_repository.get_job_logs(
                job_id=job_id,
                step_type=step_type,
                level=level,
                limit=limit,
                offset=offset
            )
            
            return {
                "success": True,
                "job_id": job_id,
                "logs": logs,
                "count": len(logs),
                "filters": {
                    "step_type": step_type,
                    "level": level
                }
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "job_id": job_id
            }
            
    @staticmethod
    def get_job_logs_summary(job_id: str) -> Dict[str, Any]:
        """
        Get summarized logs for a job (grouped by step type)
        
        Args:
            job_id: The job ID
            
        Returns:
            Dictionary with summary data
        """
        try:
            summary = job_repository.get_job_logs_summary(job_id)
            
            return {
                "success": True,
                "job_id": job_id,
                "summary": summary,
                "total_steps": len(summary)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "job_id": job_id
            }
            
    @staticmethod
    def get_latest_job_status(job_id: str) -> Dict[str, Any]:
        """
        Get the latest status for each step of a job
        
        Args:
            job_id: The job ID
            
        Returns:
            Dictionary with latest status for each step
        """
        try:
            latest_logs = job_repository.get_latest_job_logs(job_id)
            
            # Format for UI display
            status_by_step = {}
            for log in latest_logs:
                status_by_step[log['step_type']] = {
                    "message": log['message'],
                    "level": log['level'],
                    "progress": log.get('progress', 0),
                    "timestamp": log['created_at'],
                    "metadata": log.get('metadata', {})
                }
                
            return {
                "success": True,
                "job_id": job_id,
                "status_by_step": status_by_step,
                "latest_update": max(log['created_at'] for log in latest_logs) if latest_logs else None
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "job_id": job_id
            }
            
    @staticmethod
    def get_job_timeline(job_id: str) -> Dict[str, Any]:
        """
        Get a timeline view of job execution
        
        Args:
            job_id: The job ID
            
        Returns:
            Timeline data for visualization
        """
        try:
            # Get all logs ordered by time
            logs = job_repository.get_job_logs(job_id=job_id)
            
            # Get summary for durations
            summary = job_repository.get_job_logs_summary(job_id)
            
            # Build timeline
            timeline = []
            for step in summary:
                timeline.append({
                    "step_type": step['step_type'],
                    "started_at": step['step_started_at'],
                    "completed_at": step['step_completed_at'],
                    "duration_ms": step.get('total_duration_ms', 0),
                    "has_errors": bool(step.get('has_errors', 0)),
                    "error_messages": step.get('error_messages', ''),
                    "final_progress": step.get('final_progress', 100)
                })
                
            return {
                "success": True,
                "job_id": job_id,
                "timeline": timeline,
                "total_duration_ms": sum(s.get('total_duration_ms', 0) for s in summary)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "job_id": job_id
            }
            
    @staticmethod
    def get_job_errors(job_id: str) -> Dict[str, Any]:
        """
        Get all errors for a job
        
        Args:
            job_id: The job ID
            
        Returns:
            Dictionary with error logs
        """
        try:
            error_logs = job_repository.get_job_logs(
                job_id=job_id,
                level="error"
            )
            
            return {
                "success": True,
                "job_id": job_id,
                "errors": error_logs,
                "error_count": len(error_logs)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "job_id": job_id
            }