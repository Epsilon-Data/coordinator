"""
SQLAlchemy 2.0 models for Epsilon Coordinator database tables
"""
from datetime import datetime
from typing import Optional, Dict, Any
import json

from sqlalchemy import String, DateTime, Text, Integer, func, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all database models"""
    pass


class Workspace(Base):
    """Workspace model"""
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    github_repo: Mapped[str] = mapped_column(String, nullable=False)
    github_branch: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    job_requests: Mapped[list["JobRequest"]] = relationship("JobRequest", back_populates="workspace")


class JobRequest(Base):
    """Job request model matching existing job_requests table"""
    __tablename__ = "job_requests"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String, ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")

    # Commit information
    commit_sha: Mapped[Optional[str]] = mapped_column(String)
    commit_message: Mapped[Optional[str]] = mapped_column(Text)
    commit_author: Mapped[Optional[str]] = mapped_column(String)

    # Metadata fields (mapped to existing schema)
    result_metadata: Mapped[Optional[str]] = mapped_column(Text)  # Used for repo_path, ai_result_path, etc.
    logs: Mapped[Optional[str]] = mapped_column(Text)  # Used for repo_metadata
    ai_logs: Mapped[Optional[str]] = mapped_column(Text)  # Used for worker logs and AI risks

    # AI Analysis fields
    validation_status: Mapped[Optional[str]] = mapped_column(String)  # Used for AI confidence score
    validation_decision: Mapped[Optional[str]] = mapped_column(Text)  # Used for AI reasoning

    # Execution fields
    execution_output: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    attestation: Mapped[Optional[str]] = mapped_column(Text)  # JSON: enclave attestation document
    verification_receipt: Mapped[Optional[str]] = mapped_column(Text)  # JSON: server-side verification result
    execution_metrics: Mapped[Optional[str]] = mapped_column(Text)  # JSON: per-step timing metrics

    # Enclave metadata
    enclave_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    enclave_pcr0: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # JAC / ATL: cryptographically committed identity bound into the public log.
    # job_id_committed = SHA-256(researcher_nonce || operator_nonce). Distinct
    # from the operational job_id primary key so deployed integrations are not
    # affected. See migration 44be9b128f17.
    job_id_committed: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    researcher_nonce: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="job_requests")
    logs_entries: Mapped[list["JobLog"]] = relationship("JobLog", back_populates="job_request")

    # Helper properties for cleaner access
    @property
    def repo_path(self) -> Optional[str]:
        """Get repository path from result_metadata"""
        return self.result_metadata

    @repo_path.setter
    def repo_path(self, value: Optional[str]):
        """Set repository path in result_metadata"""
        self.result_metadata = value

    @property
    def repo_metadata(self) -> Optional[Dict[str, Any]]:
        """Get repository metadata from logs field"""
        if self.logs:
            try:
                return json.loads(self.logs)
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    @repo_metadata.setter
    def repo_metadata(self, value: Optional[Dict[str, Any]]):
        """Set repository metadata in logs field"""
        if value is not None:
            self.logs = json.dumps(value)
        else:
            self.logs = None

    @property
    def ai_confidence_score(self) -> Optional[float]:
        """Get AI confidence score from validation_status"""
        if self.validation_status:
            try:
                return float(self.validation_status)
            except (ValueError, TypeError):
                return None
        return None

    @ai_confidence_score.setter
    def ai_confidence_score(self, value: Optional[float]):
        """Set AI confidence score in validation_status"""
        if value is not None:
            self.validation_status = str(value)
        else:
            self.validation_status = None

    @property
    def ai_reasoning(self) -> Optional[str]:
        """Get AI reasoning from validation_decision"""
        return self.validation_decision

    @ai_reasoning.setter
    def ai_reasoning(self, value: Optional[str]):
        """Set AI reasoning in validation_decision"""
        self.validation_decision = value


class JobLog(Base):
    """Detailed job log entries"""
    __tablename__ = "job_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, ForeignKey("job_requests.job_id"), nullable=False)
    worker_name: Mapped[str] = mapped_column(String, nullable=False)
    step_name: Mapped[str] = mapped_column(String, nullable=False)
    step_type: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[str] = mapped_column(String, nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # JSON fields
    log_metadata: Mapped[Optional[str]] = mapped_column(Text)  # JSON string
    error_details: Mapped[Optional[str]] = mapped_column(Text)  # JSON string

    # Progress tracking
    progress: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    parent_log_id: Mapped[Optional[str]] = mapped_column(String)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    job_request: Mapped["JobRequest"] = relationship("JobRequest", back_populates="logs_entries")

    # Helper properties
    @property
    def metadata_dict(self) -> Optional[Dict[str, Any]]:
        """Get metadata as dictionary"""
        if self.log_metadata:
            try:
                return json.loads(self.log_metadata)
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    @metadata_dict.setter
    def metadata_dict(self, value: Optional[Dict[str, Any]]):
        """Set metadata from dictionary"""
        if value is not None:
            self.log_metadata = json.dumps(value)
        else:
            self.log_metadata = None

    @property
    def error_details_dict(self) -> Optional[Dict[str, Any]]:
        """Get error details as dictionary"""
        if self.error_details:
            try:
                return json.loads(self.error_details)
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    @error_details_dict.setter
    def error_details_dict(self, value: Optional[Dict[str, Any]]):
        """Set error details from dictionary"""
        if value is not None:
            self.error_details = json.dumps(value)
        else:
            self.error_details = None