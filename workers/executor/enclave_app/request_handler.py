"""
Request handler for processing enclave client requests
"""
import json
import logging
from typing import Dict, Any, Optional

from kms_decrypt import KMSDecryptor
from script_executor import ScriptExecutor

logger = logging.getLogger(__name__)


class RequestHandler:
    """Handles incoming requests from enclave clients"""
    
    def __init__(self):
        self.kms_decryptor = KMSDecryptor()
        self.script_executor = ScriptExecutor(self.kms_decryptor)
        
    def handle_request(self, request_data: str) -> Dict[str, Any]:
        """
        Process a request and return response
        
        Args:
            request_data: JSON string containing the request
            
        Returns:
            Response dictionary
        """
        try:
            # Parse request
            request = self._parse_request(request_data)
            if request is None:
                return self._error_response("Invalid JSON request")
            
            # Validate request
            validation_error = self._validate_request(request)
            if validation_error:
                return self._error_response(validation_error)
            
            # Route to appropriate handler
            operation = request['operation']
            
            if operation == 'decrypt':
                return self._handle_decrypt(request)
            elif operation == 'execute_script_envelope':
                return self._handle_execute_script(request)
            elif operation == 'health_check':
                return self._handle_health_check()
            else:
                return self._error_response(f"Unknown operation: {operation}")
                
        except Exception as e:
            logger.error(f"Request handling error: {str(e)}")
            return self._error_response(f"Server error: {str(e)}")
    
    def _parse_request(self, request_data: str) -> Optional[Dict[str, Any]]:
        """Parse JSON request data"""
        try:
            return json.loads(request_data)
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {str(e)}")
            return None
    
    def _validate_request(self, request: Dict[str, Any]) -> Optional[str]:
        """Validate request has required fields"""
        operation = request.get('operation')
        
        if not operation:
            return "Missing required field: operation"
        
        if operation == 'decrypt':
            required_fields = ['ciphertext', 'credentials']
        elif operation == 'execute_script_envelope':
            required_fields = ['encrypted_data', 'encrypted_script', 'credentials']
        elif operation == 'health_check':
            required_fields = []
        else:
            return None  # Unknown operation will be handled elsewhere
        
        for field in required_fields:
            if field not in request:
                return f"Missing required field: {field}"
                
        return None
    
    def _handle_decrypt(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle simple decryption request"""
        logger.info("Processing decrypt operation...")
        
        success, result = self.kms_decryptor.decrypt_with_kms(
            request['ciphertext'],
            request['credentials']
        )
        
        if success:
            return {
                "status": "success",
                "operation": "decrypt",
                "plaintext": result
            }
        else:
            return {
                "status": "error",
                "operation": "decrypt",
                "message": result
            }
    
    def _handle_execute_script(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle script execution request"""
        logger.info("Processing execute_script_envelope operation...")
        
        success, result = self.script_executor.execute_script_with_bundle(
            request['encrypted_data'],
            request['encrypted_script'],
            request['credentials'],
            request.get('script_path', 'script.py')
        )
        
        if success:
            return {
                "status": "success",
                "operation": "execute_script_envelope",
                "output": result
            }
        else:
            return {
                "status": "error",
                "operation": "execute_script_envelope",
                "message": result
            }
    
    def _handle_health_check(self) -> Dict[str, Any]:
        """Handle health check request"""
        logger.info("Processing health check...")
        return {
            "status": "success",
            "operation": "health_check",
            "message": "Enclave server is healthy"
        }
    
    def _error_response(self, message: str) -> Dict[str, Any]:
        """Create error response"""
        return {
            "status": "error",
            "message": message
        }