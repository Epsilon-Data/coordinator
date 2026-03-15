"""
Interface definitions for executor worker.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field as dc_field
from typing import Dict, Any, Optional, Tuple

from pydantic import BaseModel, Field

from workers.executor.models import JobExecutionRequest, ExecutionResult


@dataclass
class ProxyResponse:
    """Response from proxy tunnel query."""
    encrypted_csv: str
    metadata: dict = dc_field(default_factory=dict)


# Middleware models
class MiddlewareRequest(BaseModel):
    """Request object for middleware."""
    dataset_id: str = Field(..., min_length=1, description="Dataset identifier")
    archetype_id: str = Field(..., min_length=1, description="Archetype identifier")
    public_key: str = Field(..., description="RSA public key for encryption")
    job_id: str = Field(..., min_length=1, description="Job identifier")
    workspace_id: Optional[str] = Field(default=None, description="Workspace identifier")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    model_config = {"frozen": False, "extra": "ignore"}


class MiddlewareResponse(BaseModel):
    """Response object from middleware."""
    success: bool = Field(..., description="Whether the request succeeded")
    encrypted_csv: Optional[str] = Field(default=None, description="Encrypted CSV data (legacy mode)")
    csv_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = Field(default=None, description="Error message if failed")
    request_id: Optional[str] = Field(default=None, description="Request tracking ID")
    # direct_db mode fields
    mode: str = Field(default="legacy", description="Response mode: 'legacy', 'direct_db', or 'proxy'")
    encrypted_credentials: Optional[str] = Field(default=None, description="Encrypted DB credentials (direct_db mode)")
    sql_query: Optional[str] = Field(default=None, description="SQL query string (direct_db mode)")
    # proxy mode fields
    proxy_info: Optional[Dict[str, Any]] = Field(default=None, description="Proxy tunnel info (proxy mode)")

    model_config = {"frozen": False, "extra": "ignore"}

    @property
    def is_error(self) -> bool:
        """Check if response is an error."""
        return not self.success or self.error is not None

    @property
    def is_direct_db(self) -> bool:
        """Check if this is a direct_db mode response."""
        return self.mode == "direct_db"

    @property
    def is_proxy(self) -> bool:
        """Check if this is a proxy mode response."""
        return self.mode == "proxy"


class IExecutor(ABC):
    """Interface for job execution."""

    @abstractmethod
    def execute(self, request: JobExecutionRequest) -> ExecutionResult:
        """Execute a job request."""
        pass

    @abstractmethod
    def prepare_environment(self, request: JobExecutionRequest) -> Dict[str, Any]:
        """Prepare execution environment."""
        pass

    @abstractmethod
    def cleanup_environment(self, job_id: str) -> None:
        """Clean up after job execution."""
        pass

    @abstractmethod
    def get_status(self, job_id: str) -> Dict[str, Any]:
        """Get execution status for a job."""
        pass

    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """Check if executor is ready to accept jobs."""
        pass


class IEnclaveClient(ABC):
    """Interface for enclave client."""

    @abstractmethod
    def get_public_key(self, job_id: str) -> Tuple[str, str]:
        """Get public key from enclave. Returns (public_key, session_id)."""
        pass

    @abstractmethod
    def encrypt_zip_data(self, zip_data: bytes, public_key: str) -> str:
        """Encrypt zip file data with public key. Returns base64 encoded data."""
        pass

    @abstractmethod
    def send_encrypted_data_to_enclave(
        self,
        session_id: str,
        encrypted_zip: str,
        encrypted_csv: Optional[str] = None
    ) -> Tuple[bool, str, Optional[Dict]]:
        """Send encrypted data to enclave. Returns (success, output/error, attestation)."""
        pass

    @abstractmethod
    def send_encrypted_data_with_db_fetch(
        self,
        session_id: str,
        encrypted_zip: str,
        encrypted_credentials: str,
        sql_query: str
    ) -> Tuple[bool, str, Optional[Dict]]:
        """Send encrypted data to enclave with direct DB fetch. Returns (success, output/error, attestation)."""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Check if the enclave is healthy."""
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if client is connected to enclave."""
        pass

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the enclave."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the enclave."""
        pass


class IMiddlewareClient(ABC):
    """Interface for middleware client."""

    @abstractmethod
    def fetch_encrypted_csv(self, request: MiddlewareRequest, mode: str = "legacy") -> MiddlewareResponse:
        """Fetch data from middleware. Mode: 'legacy' (encrypted CSV) or 'direct_db' (encrypted credentials + SQL)."""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Check if middleware is accessible."""
        pass

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """Check if middleware is configured."""
        pass
