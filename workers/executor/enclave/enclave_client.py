#!/usr/bin/env python3
"""
Enclave client for communicating with Nitro Enclave
Handles vsock communication and encryption/decryption operations
"""

import json
import socket
import base64
import boto3
import requests
import os
from typing import Dict, Any, Optional, Tuple
from cryptography.fernet import Fernet

from interfaces import IEnclaveClient
from config import get_settings
from exceptions import EnclaveConnectionError, EnclaveDecryptionError
from utils import get_logger

logger = get_logger(__name__)


class EnclaveClient(IEnclaveClient):
    """Client for communicating with Nitro Enclave"""
    
    def __init__(self, enclave_cid: int = None):
        """
        Initialize Nitro Enclave client
        
        Args:
            enclave_cid: Enclave CID (discovered if not provided)
        """
        self._settings = get_settings()
        self.enclave_cid = enclave_cid or self._get_enclave_cid()
        self.credentials = self._get_instance_credentials()
        self.kms_client = self._create_kms_client()
        self._connected = True
        
    def _get_enclave_cid(self) -> int:
        """Get the CID of the running enclave"""
        try:
            import subprocess
            result = subprocess.run(
                ['nitro-cli', 'describe-enclaves'],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                enclaves = json.loads(result.stdout)
                if enclaves and len(enclaves) > 0:
                    cid = enclaves[0]['EnclaveCID']
                    logger.info(f"Found enclave with CID: {cid}")
                    return cid
                    
            logger.warning("No running enclaves found")
            raise RuntimeError("No running enclaves found")
            
        except Exception as e:
            logger.error(f"Could not get enclave CID: {str(e)}")
            raise
            
    def _get_instance_credentials(self) -> Dict[str, str]:
        """Get EC2 instance credentials from metadata service"""
        try:
            # Get token for IMDSv2
            token_response = requests.put(
                "http://169.254.169.254/latest/api/token",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
                timeout=2
            )
            token = token_response.text
            
            # Get role name
            role_response = requests.get(
                "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
                headers={"X-aws-ec2-metadata-token": token},
                timeout=2
            )
            role_name = role_response.text.strip()
            
            # Get credentials
            creds_response = requests.get(
                f"http://169.254.169.254/latest/meta-data/iam/security-credentials/{role_name}",
                headers={"X-aws-ec2-metadata-token": token},
                timeout=2
            )
            
            creds = creds_response.json()
            logger.info(f"Retrieved credentials for role: {role_name}")
            
            return {
                'access_key_id': creds['AccessKeyId'],
                'secret_access_key': creds['SecretAccessKey'],
                'session_token': creds['Token']
            }
            
        except Exception as e:
            logger.error(f"Failed to get instance credentials: {str(e)}")
            raise
            
    def _create_kms_client(self):
        """Create KMS client with instance credentials"""
        return boto3.client(
            'kms',
            region_name=self._settings.aws.region,
            aws_access_key_id=self.credentials['access_key_id'],
            aws_secret_access_key=self.credentials['secret_access_key'],
            aws_session_token=self.credentials['session_token']
        )
        
    def encrypt_data(self, plaintext: str) -> Dict[str, Any]:
        """Encrypt data using KMS (direct or envelope encryption based on size)"""
        try:
            # Check if data is small enough for direct KMS encryption
            if len(plaintext.encode('utf-8')) <= 4096:
                # Direct KMS encryption
                response = self.kms_client.encrypt(
                    KeyId=self._settings.aws.kms_key_arn,
                    Plaintext=plaintext.encode('utf-8')
                )
                
                ciphertext = base64.b64encode(response['CiphertextBlob']).decode('utf-8')
                logger.info(f"Direct KMS encrypted {len(plaintext)} bytes")
                
                return {
                    'method': 'direct',
                    'ciphertext': ciphertext
                }
            else:
                # Use envelope encryption for large data
                return self._envelope_encrypt(plaintext)
                
        except Exception as e:
            logger.error(f"Encryption failed: {str(e)}")
            raise
            
    def _envelope_encrypt(self, plaintext: str) -> Dict[str, Any]:
        """Encrypt large data using envelope encryption"""
        try:
            logger.info(f"Using envelope encryption for {len(plaintext)} bytes")
            
            # Generate a data key
            response = self.kms_client.generate_data_key(
                KeyId=self._settings.aws.kms_key_arn,
                KeySpec='AES_256'
            )
            
            # Extract plaintext and encrypted data key
            plaintext_key = response['Plaintext']
            encrypted_data_key = response['CiphertextBlob']
            
            # Use the plaintext key to encrypt the data
            fernet = Fernet(base64.urlsafe_b64encode(plaintext_key[:32]))
            encrypted_data = fernet.encrypt(plaintext.encode('utf-8'))
            
            # Return the encrypted data key and encrypted data
            return {
                'method': 'envelope',
                'encrypted_data_key': base64.b64encode(encrypted_data_key).decode('utf-8'),
                'encrypted_data': base64.b64encode(encrypted_data).decode('utf-8')
            }
            
        except Exception as e:
            logger.error(f"Envelope encryption failed: {str(e)}")
            raise
            
    def send_to_enclave(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Send request to enclave and get response"""
        try:
            # Create vsock connection
            sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
            sock.settimeout(300)  # 5 minute timeout for execution
            
            logger.info(f"Connecting to enclave CID {self.enclave_cid} port {self._settings.enclave.vsock_port}...")
            sock.connect((self.enclave_cid, self._settings.enclave.vsock_port))
            
            # Send request
            request_json = json.dumps(request_data)
            sock.send(request_json.encode())
            logger.info(f"Sent {len(request_json)} bytes to enclave")
            
            # Receive response (larger buffer for execution output)
            response_data = sock.recv(4194304).decode()  # 4MB buffer
            response = json.loads(response_data)
            
            sock.close()
            return response
            
        except socket.timeout:
            logger.error("Connection to enclave timed out")
            raise
        except Exception as e:
            logger.error(f"Failed to communicate with enclave: {str(e)}")
            raise
            
    def execute_script(self, script_content: str, data: str, script_path: str = None, 
                      data_already_encrypted: bool = False) -> Tuple[bool, str]:
        """Execute script with data in enclave (compatible with EnclaveClientLocal interface)
        
        Args:
            script_content: The Python script to execute (empty for bundle mode)
            data: The data to pass to the script (either plaintext or encrypted JSON metadata)
            script_path: Optional script path for logging
            data_already_encrypted: If True, data contains encrypted metadata and shouldn't be re-encrypted
        """
        try:
            # Handle data based on whether it's already encrypted
            if data_already_encrypted:
                # Data is already encrypted metadata (JSON string), parse it
                try:
                    encrypted_data = json.loads(data)
                except json.JSONDecodeError:
                    # If not valid JSON, treat as plain encrypted data
                    encrypted_data = {'method': 'direct', 'ciphertext': data}
            else:
                # Encrypt the data
                encrypted_data = self.encrypt_data(data)
            
            # Check if this is a bundle execution (empty script_content means script is in bundle)
            if not script_content.strip():
                logger.info("📦 Bundle execution mode - script is in the encrypted bundle")
                # For bundle execution, we pass empty encrypted script
                encrypted_script = {'method': 'direct', 'ciphertext': ''}
            else:
                # Encrypt script (normal mode)
                encrypted_script = self.encrypt_data(script_content)
            
            # Prepare request for enclave
            request = {
                'operation': 'execute_script_envelope',
                'encrypted_data': encrypted_data,
                'encrypted_script': encrypted_script,
                'credentials': self.credentials,
                'script_path': script_path or 'script.py'
            }
            
            # Send to enclave for decryption and execution
            response = self.send_to_enclave(request)
            
            if response['status'] == 'success':
                logger.info("Script execution successful!")
                return True, response['output']
            else:
                error_msg = response.get('message', 'Unknown error')
                logger.error(f"Script execution failed: {error_msg}")
                return False, error_msg
                
        except Exception as e:
            logger.error(f"Failed to execute script: {str(e)}")
            return False, str(e)
            
    def health_check(self) -> bool:
        """Check if enclave is responsive"""
        try:
            request = {
                'operation': 'health_check'
            }
            response = self.send_to_enclave(request)
            return response.get('status') == 'healthy'
        except Exception:
            return False
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected to enclave"""
        return self._connected
    
    def connect(self) -> None:
        """Establish connection to the enclave"""
        try:
            self.enclave_cid = self._get_enclave_cid()
            self._connected = True
            logger.info(f"Connected to enclave CID: {self.enclave_cid}")
        except Exception as e:
            self._connected = False
            logger.error(f"Failed to connect to enclave: {e}")
            raise EnclaveConnectionError(f"Failed to connect to enclave: {e}")
    
    def disconnect(self) -> None:
        """Disconnect from the enclave"""
        self._connected = False
        logger.info("Disconnected from enclave")