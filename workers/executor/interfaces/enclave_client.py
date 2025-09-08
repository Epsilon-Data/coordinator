"""
Abstract interface for enclave clients
"""
from abc import ABC, abstractmethod
from typing import Tuple, Dict, Any, Optional


class IEnclaveClient(ABC):
    """Abstract interface for enclave client implementations"""
    
    @abstractmethod
    def encrypt_data(self, plaintext: str) -> Dict[str, Any]:
        """
        Encrypt data for the enclave
        
        Args:
            plaintext: Data to encrypt
            
        Returns:
            Dictionary containing encryption metadata
        """
        pass
    
    @abstractmethod
    def execute_script(
        self,
        script_content: str,
        data: str,
        script_path: Optional[str] = None,
        data_already_encrypted: bool = False
    ) -> Tuple[bool, str]:
        """
        Execute a script in the enclave
        
        Args:
            script_content: Python script content
            data: Data for the script
            script_path: Optional script path for logging
            data_already_encrypted: Whether data is already encrypted
            
        Returns:
            Tuple of (success, output/error message)
        """
        pass
    
    @abstractmethod
    def health_check(self) -> bool:
        """
        Check if the enclave is healthy
        
        Returns:
            True if healthy, False otherwise
        """
        pass
    
    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if client is connected to enclave"""
        pass
    
    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the enclave"""
        pass
    
    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the enclave"""
        pass