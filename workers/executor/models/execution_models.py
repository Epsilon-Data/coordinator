"""
Data models for job execution
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from datetime import datetime


@dataclass
class JobExecutionRequest:
    """Request to execute a job"""
    job_id: str
    repo_path: str
    script_path: str
    data_path: Optional[str] = None
    workspace_id: Optional[str] = None
    ai_decision: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    
    @classmethod
    def from_message(cls, message: Dict[str, Any]) -> 'JobExecutionRequest':
        """Create from RabbitMQ message"""
        return cls(
            job_id=message['job_id'],
            repo_path=message['repo_path'],
            script_path=message.get('script_path', 'example_analysis.py'),
            data_path=message.get('data_path'),
            workspace_id=message.get('workspace_id'),
            ai_decision=message.get('ai_decision', {}),
            metadata=message.get('metadata', {})
        )
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'JobExecutionRequest':
        """Create from dictionary (polling API response)"""
        return cls(
            job_id=data['job_id'],
            repo_path=data['repo_path'],
            script_path=data.get('script_path', 'example_analysis.py'),
            data_path=data.get('data_path'),
            workspace_id=data.get('workspace_id'),
            ai_decision=data.get('ai_decision', {}),
            metadata=data.get('metadata', {})
        )


@dataclass
class ExecutionResult:
    """Result of job execution"""
    job_id: str
    status: str  # 'success', 'failed', 'timeout'
    execution_time: float
    output: Optional[str] = None
    error: Optional[str] = None
    artifacts: List[str] = None
    logs: Optional[str] = None
    enclave_cid: Optional[int] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.artifacts is None:
            self.artifacts = []
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
            
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            'job_id': self.job_id,
            'status': self.status,
            'execution_time': self.execution_time,
            'output': self.output,
            'error': self.error,
            'artifacts': self.artifacts,
            'logs': self.logs,
            'enclave_cid': self.enclave_cid,
            'timestamp': self.timestamp.isoformat(),
            'execution_type': 'nitro_enclave'
        }


# EnclaveConfig moved to config/settings.py
# Import from there if needed:
# from config.settings import EnclaveConfig