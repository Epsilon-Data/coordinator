"""
Abstract interface for job executors
"""
from abc import ABC, abstractmethod
from typing import Dict, Any
from models.execution_models import JobExecutionRequest, ExecutionResult


class IExecutor(ABC):
    """Abstract interface for job execution"""
    
    @abstractmethod
    def execute(self, request: JobExecutionRequest) -> ExecutionResult:
        """
        Execute a job request
        
        Args:
            request: Job execution request
            
        Returns:
            Execution result
        """
        pass
    
    @abstractmethod
    def prepare_environment(self, request: JobExecutionRequest) -> Dict[str, Any]:
        """
        Prepare execution environment
        
        Args:
            request: Job execution request
            
        Returns:
            Environment metadata
        """
        pass
    
    @abstractmethod
    def cleanup_environment(self, job_id: str) -> None:
        """
        Clean up after job execution
        
        Args:
            job_id: Job identifier
        """
        pass
    
    @abstractmethod
    def get_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get execution status for a job
        
        Args:
            job_id: Job identifier
            
        Returns:
            Status information
        """
        pass
    
    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """Check if executor is ready to accept jobs"""
        pass