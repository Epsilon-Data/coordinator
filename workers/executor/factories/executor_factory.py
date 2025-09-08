"""
Factory for creating executor instances
"""
from typing import Optional

from interfaces import IExecutor, IEnclaveClient, IStorageManager
from config import Settings, get_settings
from exceptions import ConfigurationError
from utils import get_logger
from factories.enclave_factory import EnclaveClientFactory
from factories.storage_factory import StorageManagerFactory

logger = get_logger(__name__)


class ExecutorFactory:
    """Factory for creating job executor instances"""
    
    @staticmethod
    def create_executor(
        settings: Optional[Settings] = None,
        enclave_client: Optional[IEnclaveClient] = None,
        storage_manager: Optional[IStorageManager] = None
    ) -> IExecutor:
        """
        Create a job executor with all dependencies
        
        Args:
            settings: Settings instance (uses default if None)
            enclave_client: Pre-configured enclave client (creates new if None)
            storage_manager: Pre-configured storage manager (creates new if None)
            
        Returns:
            Configured job executor
            
        Raises:
            ConfigurationError: If executor cannot be created
        """
        if settings is None:
            settings = get_settings()
        
        try:
            # Create enclave client if not provided
            if enclave_client is None:
                enclave_client = EnclaveClientFactory.create_client(settings)
            
            # Create storage manager if not provided
            if storage_manager is None:
                storage_manager = StorageManagerFactory.create_manager(settings)
            
            # Create the executor
            logger.info("Creating SecureExecutor with configured dependencies")
            return ExecutorFactory._create_secure_executor(
                settings, enclave_client, storage_manager
            )
            
        except Exception as e:
            raise ConfigurationError(f"Failed to create executor: {e}")
    
    @staticmethod
    def _create_secure_executor(
        settings: Settings,
        enclave_client: IEnclaveClient,
        storage_manager: IStorageManager
    ) -> IExecutor:
        """Create secure executor implementation"""
        from core.secure_executor import SecureExecutor
        
        logger.debug("Creating SecureExecutor instance")
        return SecureExecutor(
            enclave_client=enclave_client,
            storage_manager=storage_manager,
            settings=settings
        )
    
    @staticmethod
    def create_mock_executor(settings: Optional[Settings] = None) -> IExecutor:
        """Create a mock executor for testing"""
        if settings is None:
            settings = get_settings()
        
        # Create mock dependencies
        enclave_client = EnclaveClientFactory.create_mock_client()
        storage_manager = StorageManagerFactory.create_mock_manager()
        
        return ExecutorFactory._create_secure_executor(
            settings, enclave_client, storage_manager
        )