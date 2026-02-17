"""
Data models for executor worker using Pydantic v2.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, Optional, List

from pydantic import BaseModel, Field, field_validator


class JobStatus(str, Enum):
    """Job execution status."""
    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    EXECUTING = "executing"
    PENDING = "pending"


class JobExecutionRequest(BaseModel):
    """Request to execute a job."""
    job_id: str = Field(..., min_length=1, description="Unique job identifier")
    repo_path: str = Field(..., min_length=1, description="Path to cloned repository")
    script_path: str = Field(default="example_analysis.py", description="Script to execute")
    data_path: Optional[str] = Field(default=None, description="Path to data file")
    workspace_id: Optional[str] = Field(default=None, description="Workspace identifier")
    ai_decision: Optional[Dict[str, Any]] = Field(default_factory=dict)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    model_config = {"frozen": False, "extra": "ignore"}

    @field_validator("job_id", "repo_path")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v


class ExecutionResult(BaseModel):
    """Result of job execution."""
    job_id: str = Field(..., description="Job identifier")
    status: JobStatus = Field(..., description="Execution status")
    execution_time: float = Field(..., ge=0, description="Execution time in seconds")
    output: Optional[str] = Field(default=None, description="Execution output")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    artifacts: List[str] = Field(default_factory=list, description="Generated artifact paths")
    logs: Optional[str] = Field(default=None, description="Execution logs")
    enclave_cid: Optional[int] = Field(default=None, description="Enclave CID used")
    attestation: Optional[Dict[str, Any]] = Field(default=None, description="Enclave attestation document")
    step_timing: Optional[Dict[str, Any]] = Field(default=None, description="Per-step execution timing metrics")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": False, "extra": "ignore"}

    @property
    def is_success(self) -> bool:
        """Check if execution succeeded."""
        return self.status == JobStatus.SUCCESS

    @property
    def is_failure(self) -> bool:
        """Check if execution failed."""
        return self.status in (JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.REJECTED)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "execution_time": self.execution_time,
            "output": self.output,
            "error": self.error,
            "artifacts": self.artifacts,
            "logs": self.logs,
            "enclave_cid": self.enclave_cid,
            "attestation": self.attestation,
            "step_timing": self.step_timing,
            "timestamp": self.timestamp.isoformat(),
            "execution_type": "nitro_enclave",
        }


class DatasetConfig(BaseModel):
    """Configuration for a dataset from build.yml."""
    dataset_id: str = Field(..., min_length=1, description="Dataset identifier")
    archetype_id: str = Field(..., min_length=1, description="Archetype identifier")
    import_path: str = Field(default="", description="Python import path")
    function_name: str = Field(default="", description="Function name to call")
    archetype_path: str = Field(default="", description="Path to archetype file")

    model_config = {"frozen": True, "extra": "ignore"}


class BuildConfig(BaseModel):
    """Parsed build configuration from build.yml."""
    version: str = Field(default="1.0", description="Build config version")
    script_file: str = Field(..., min_length=1, description="Main script file")
    datasets: List[DatasetConfig] = Field(default_factory=list, description="Dataset configurations")
    epsilon: float = Field(default=1.0, gt=0, description="Privacy epsilon value")
    timeout: int = Field(default=300, gt=0, le=3600, description="Execution timeout in seconds")
    environment: str = Field(default="enclave", description="Execution environment")

    model_config = {"frozen": True, "extra": "ignore"}

    @field_validator("epsilon")
    @classmethod
    def validate_epsilon(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Epsilon must be positive")
        return v

    @property
    def has_datasets(self) -> bool:
        """Check if build has datasets configured."""
        return len(self.datasets) > 0
