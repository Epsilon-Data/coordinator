"""
Factory for creating enclave client instances
"""
from typing import Optional
from interfaces import IEnclaveClient
from config import Settings, get_settings
from exceptions import ConfigurationError
from utils import get_logger

logger = get_logger(__name__)


class EnclaveClientFactory:
    """Factory for creating appropriate enclave client instances"""
    
    @staticmethod
    def create_client(
        settings: Optional[Settings] = None,
        force_local: bool = False
    ) -> IEnclaveClient:
        """
        Create an enclave client based on configuration
        
        Args:
            settings: Settings instance (uses default if None)
            force_local: Force use of local client for testing
            
        Returns:
            Configured enclave client
            
        Raises:
            ConfigurationError: If client cannot be created
        """
        if settings is None:
            settings = get_settings()
        
        try:
            if force_local or settings.enclave.use_local_client:
                logger.info("Creating local enclave client for testing")
                return EnclaveClientFactory._create_local_client(settings)
            else:
                logger.info("Creating Nitro Enclave client")
                return EnclaveClientFactory._create_nitro_client(settings)
                
        except Exception as e:
            raise ConfigurationError(
                f"Failed to create enclave client: {e}",
                details={'force_local': force_local, 'use_local': settings.enclave.use_local_client}
            )
    
    @staticmethod
    def _create_local_client(settings: Settings) -> IEnclaveClient:
        """Create local enclave client"""
        from enclave.enclave_client_local import EnclaveClientLocal
        
        # Validate local client requirements
        if not settings.aws.kms_key_arn:
            raise ConfigurationError("AWS_KMS_KEY_ARN is required for local client")
        
        if not settings.aws.access_key_id:
            raise ConfigurationError("AWS_ACCESS_KEY_ID is required for local client")
        
        if not settings.aws.secret_access_key:
            raise ConfigurationError("AWS_SECRET_ACCESS_KEY is required for local client")
        
        logger.debug("Creating EnclaveClientLocal instance")
        return EnclaveClientLocal()
    
    @staticmethod
    def _create_nitro_client(settings: Settings) -> IEnclaveClient:
        """Create Nitro Enclave client"""
        from enclave.enclave_client import EnclaveClient
        
        # Validate Nitro client requirements
        if not settings.enclave.eif_path:
            raise ConfigurationError("Enclave EIF path is required for Nitro client")
        
        logger.debug("Creating EnclaveClient instance")
        return EnclaveClient()
    
    @staticmethod
    def create_mock_client() -> IEnclaveClient:
        """Create a mock client for testing"""
        return MockEnclaveClient()


class MockEnclaveClient(IEnclaveClient):
    """Mock enclave client for testing"""
    
    def __init__(self):
        self._connected = True
        logger.debug("MockEnclaveClient initialized")
    
    def encrypt_data(self, plaintext: str) -> dict:
        """Mock encryption - returns plaintext base64 encoded"""
        import base64
        return {
            'method': 'mock',
            'ciphertext': base64.b64encode(plaintext.encode()).decode()
        }
    
    def execute_script(self, script_content: str, data: str, script_path: str = None,
                      data_already_encrypted: bool = False) -> tuple:
        """Mock script execution"""
        logger.info(f"Mock execution of script: {script_path or 'inline'}")
        
        # Simulate successful execution
        output = f"Mock execution completed\nScript length: {len(script_content)}\nData length: {len(data)}"
        return True, output
    
    def health_check(self) -> bool:
        """Mock health check"""
        return self._connected
    
    @property
    def is_connected(self) -> bool:
        """Check mock connection status"""
        return self._connected
    
    def connect(self) -> None:
        """Mock connect"""
        self._connected = True
        logger.debug("Mock client connected")
    
    def disconnect(self) -> None:
        """Mock disconnect"""
        self._connected = False
        logger.debug("Mock client disconnected")