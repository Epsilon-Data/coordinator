"""
Secure executor implementation with enclave integration
"""
import time
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path

from interfaces import IExecutor, IEnclaveClient, IStorageManager
from models.execution_models import JobExecutionRequest, ExecutionResult
from config import Settings
from exceptions import (
    ExecutionError, ValidationError, EnclaveExecutionError,
    TimeoutError, StorageError
)
from utils import get_logger, measure_time, retry
from utils.validators import validate_job_id, validate_repository_path

logger = get_logger(__name__)


class SecureExecutor(IExecutor):
    """Secure job executor using Nitro Enclaves"""
    
    def __init__(
        self,
        enclave_client: IEnclaveClient,
        storage_manager: IStorageManager,
        settings: Settings
    ):
        """
        Initialize secure executor
        
        Args:
            enclave_client: Configured enclave client
            storage_manager: Configured storage manager
            settings: Application settings
        """
        self._enclave_client = enclave_client
        self._storage_manager = storage_manager
        self._settings = settings
        self._active_jobs: Dict[str, Dict[str, Any]] = {}
        
        logger.info("SecureExecutor initialized")
    
    @measure_time
    def execute(self, request: JobExecutionRequest) -> ExecutionResult:
        """
        Execute a job request securely in the enclave
        
        Args:
            request: Job execution request
            
        Returns:
            Execution result
        """
        start_time = time.time()
        job_id = request.job_id
        
        try:
            logger.error(" SECURE EXECUTOR STARTING ")
            logger.error(f"Starting execution for job {job_id}")
            logger.error(f"Request: {request}")

            # Validate the request
            self._validate_request(request)
            
            # Prepare execution environment
            environment = self.prepare_environment(request)
            
            # Mark job as active
            self._active_jobs[job_id] = {
                'status': 'running',
                'start_time': datetime.utcnow(),
                'environment': environment
            }
            
            # Execute the job
            success, output = self._execute_job(request)
            
            # Calculate execution time
            execution_time = time.time() - start_time
            
            # Create result
            result = ExecutionResult(
                job_id=job_id,
                status='success' if success else 'failed',
                execution_time=execution_time,
                output=output if success else None,
                error=output if not success else None,
                enclave_cid=getattr(self._enclave_client, 'enclave_cid', None),
                timestamp=datetime.utcnow()
            )
            
            # Save execution artifacts
            self._save_execution_artifacts(job_id, result, environment)
            
            logger.info(
                f"Job {job_id} completed with status: {result.status} "
                f"in {execution_time:.2f}s"
            )
            
            return result
            
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"Job {job_id} failed after {execution_time:.2f}s: {e}")
            
            # Return error result
            return ExecutionResult(
                job_id=job_id,
                status='failed',
                execution_time=execution_time,
                error=str(e),
                timestamp=datetime.utcnow()
            )
        finally:
            # Clean up
            self.cleanup_environment(job_id)
            self._active_jobs.pop(job_id, None)
    
    def prepare_environment(self, request: JobExecutionRequest) -> Dict[str, Any]:
        """
        Prepare execution environment for the job
        
        Args:
            request: Job execution request
            
        Returns:
            Environment metadata
        """
        logger.debug(f"Preparing environment for job {request.job_id}")
        
        # Get repository path
        repo_path = self._storage_manager.get_repository_path(request.job_id)
        
        # Validate paths exist
        if not repo_path.exists():
            raise ValidationError(f"Repository not found: {repo_path}")
        
        script_path = repo_path / request.script_path
        if not script_path.exists():
            raise ValidationError(f"Script not found: {script_path}")
        
        # Prepare data if specified
        data_path = None
        if request.data_path:
            data_path = repo_path / request.data_path
            if not data_path.exists():
                raise ValidationError(f"Data file not found: {data_path}")
        
        environment = {
            'repo_path': str(repo_path),
            'script_path': str(script_path),
            'data_path': str(data_path) if data_path else None,
            'workspace_id': request.workspace_id,
            'prepared_at': datetime.utcnow().isoformat()
        }
        
        logger.debug(f"Environment prepared for job {request.job_id}")
        return environment
    
    @retry(max_attempts=2, delay=1.0)
    def _execute_job(self, request: JobExecutionRequest) -> tuple[bool, str]:
        """
        Execute the actual job in the enclave
        
        Args:
            request: Job execution request
            
        Returns:
            Tuple of (success, output/error)
        """
        job_id = request.job_id
        
        try:
            # Check enclave health
            if not self._enclave_client.health_check():
                raise EnclaveExecutionError("Enclave health check failed")
            
            # For bundle execution mode, we don't load script content separately
            # The script is inside the encrypted bundle
            logger.error("BUNDLE EXECUTION MODE: Script is in the bundle, not loading separately")
            script_content = ""  # Empty script triggers bundle mode
            
            # Load or prepare data (this creates the encrypted bundle)
            logger.error("CALLING _prepare_data_content")
            data_content = self._prepare_data_content(request)
            logger.error(f"DATA CONTENT RETURNED: {len(data_content)} chars")
            
            # Execute in enclave
            logger.info(f"EXECUTING JOB {job_id} IN ENCLAVE")
            logger.info(f"Script content length: {len(script_content)} chars")
            logger.info(f"Data content length: {len(data_content)} chars")
            logger.info(f"Data already encrypted: True (encrypted by dataset manager)")
            
            # Check if data_content is a file path or JSON metadata
            if data_content.startswith('/') and Path(data_content).exists():
                # data_content is a file path to encrypted bundle
                logger.error(f"Loading encrypted bundle from file: {data_content}")
                with open(data_content, 'r') as f:
                    encrypted_metadata = f.read()
                
                success, output = self._enclave_client.execute_script(
                    script_content=script_content,
                    data=encrypted_metadata,
                    script_path=request.script_path,
                    data_already_encrypted=True  # Data is already encrypted by dataset manager
                )
            else:
                # data_content is JSON metadata (legacy mode)
                logger.error(f"Using JSON metadata directly (legacy mode)")
                success, output = self._enclave_client.execute_script(
                    script_content=script_content,
                    data=data_content,
                    script_path=request.script_path,
                    data_already_encrypted=True  # Data is already encrypted by dataset manager
                )
            
            if not success:
                raise EnclaveExecutionError(f"Enclave execution failed: {output}")
            
            logger.info(f"Job {job_id} executed successfully in enclave")
            return success, output
            
        except Exception as e:
            logger.error(f"Job execution failed: {e}")
            raise
    
    def _load_script_content(self, request: JobExecutionRequest) -> str:
        """Load script content from repository"""
        repo_path = self._storage_manager.get_repository_path(request.job_id)
        script_path = repo_path / request.script_path
        
        logger.info(f"LOADING SCRIPT from: {script_path}")
        logger.info(f"Repository path: {repo_path}")
        logger.info(f"Script path: {request.script_path}")
        
        try:
            with open(script_path, 'r', encoding='utf-8') as f:
                content = f.read()
                logger.info(f"SCRIPT LOADED successfully ({len(content)} chars)")
                logger.info(f"Script preview: {content[:200]}...")
                return content
        except Exception as e:
            logger.error(f"FAILED TO LOAD SCRIPT: {e}")
            logger.info(f"Directory contents: {list(repo_path.iterdir()) if repo_path.exists() else 'Repo path does not exist'}")
            raise ExecutionError(f"Failed to load script: {e}")
    
    def _prepare_data_content(self, request: JobExecutionRequest) -> str:
        """Prepare data content for execution"""
        try:
            logger.info("CALLING DATASET MANAGER TO PREPARE DATA")
            from services.dataset_manager import DatasetManager
            
            dataset_manager = DatasetManager(self._settings)
            result = dataset_manager.prepare_execution_data(request)
            logger.error(f"DATASET MANAGER RETURNED: {len(result)} chars")
            return result
        except Exception as e:
            logger.error(f"FAILED TO PREPARE DATA: {e}")
            logger.error(f"Exception type: {type(e)}")
            # Return simple fallback data for now
            fallback_data = '{"method": "direct", "ciphertext": "fallback_data"}'
            logger.info(f"USING FALLBACK DATA: {fallback_data}")
            return fallback_data
    
    def _save_execution_artifacts(
        self,
        job_id: str,
        result: ExecutionResult,
        environment: Dict[str, Any]
    ) -> None:
        """Save execution artifacts to storage"""
        try:
            # Save execution result
            result_json = str(result.to_dict()).encode('utf-8')
            self._storage_manager.save_artifact(
                job_id, 'execution_result.json', result_json, 'result'
            )
            
            # Save environment metadata
            env_json = str(environment).encode('utf-8')
            self._storage_manager.save_artifact(
                job_id, 'environment.json', env_json, 'metadata'
            )
            
            # Save output if available
            if result.output:
                output_bytes = result.output.encode('utf-8')
                self._storage_manager.save_artifact(
                    job_id, 'output.txt', output_bytes, 'output'
                )
            
            logger.debug(f"Saved artifacts for job {job_id}")
            
        except Exception as e:
            logger.error(f"Failed to save artifacts for job {job_id}: {e}")
            # Don't fail the job if artifact saving fails
    
    def cleanup_environment(self, job_id: str) -> None:
        """Clean up after job execution"""
        logger.debug(f"Cleaning up environment for job {job_id}")
        
        # Remove from active jobs
        self._active_jobs.pop(job_id, None)
        
        # Additional cleanup can be added here
        logger.debug(f"Cleanup completed for job {job_id}")
    
    def get_status(self, job_id: str) -> Dict[str, Any]:
        """Get execution status for a job"""
        if job_id in self._active_jobs:
            job_info = self._active_jobs[job_id].copy()
            job_info['job_id'] = job_id
            return job_info
        else:
            return {
                'job_id': job_id,
                'status': 'not_found',
                'message': 'Job not currently active'
            }
    
    @property
    def is_ready(self) -> bool:
        """Check if executor is ready to accept jobs"""
        try:
            # Check enclave health
            enclave_healthy = self._enclave_client.health_check()
            
            # Check storage
            storage_usage = self._storage_manager.get_storage_usage()
            storage_healthy = storage_usage.get('total_size_mb', 0) < 1000  # 1GB limit
            
            return enclave_healthy and storage_healthy
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    def _validate_request(self, request: JobExecutionRequest) -> None:
        """Validate job execution request"""
        validate_job_id(request.job_id)
        
        # Validate repository path
        repo_path = self._storage_manager.get_repository_path(request.job_id)
        validate_repository_path(str(repo_path))