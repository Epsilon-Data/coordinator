"""
KMS decryption module for the enclave
Handles both direct and envelope decryption using kmstool-enclave-cli
"""
import subprocess
import base64
import os
import logging
from typing import Tuple, Dict, Any, Union
from cryptography.fernet import Fernet

from config import KMSTOOL_PATH, KMS_REGION, KMS_PROXY_PORT

logger = logging.getLogger(__name__)


class KMSDecryptor:
    """Handles KMS decryption operations within the enclave"""
    
    def decrypt_with_kms(self, ciphertext: str, credentials: Dict[str, str], 
                         return_bytes: bool = False) -> Tuple[bool, Union[str, bytes]]:
        """
        Decrypt ciphertext using kmstool-enclave-cli
        
        Args:
            ciphertext: Base64-encoded ciphertext
            credentials: AWS credentials dict
            return_bytes: If True, return bytes instead of decoded string
            
        Returns:
            Tuple of (success, decrypted_data or error_message)
        """
        try:
            # Prepare command
            cmd = [
                KMSTOOL_PATH, "decrypt",
                "--region", KMS_REGION,
                "--proxy-port", str(KMS_PROXY_PORT),
                "--aws-access-key-id", credentials['access_key_id'],
                "--aws-secret-access-key", credentials['secret_access_key'],
                "--aws-session-token", credentials['session_token'],
                "--ciphertext", ciphertext
            ]

            logger.info("Executing KMS decrypt command...")

            # Run the command
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env={**os.environ, 'LD_LIBRARY_PATH': '/app'}
            )

            if result.returncode == 0:
                # Parse output
                output = result.stdout.strip()
                logger.info(f"Raw kmstool output length: {len(output)}")

                # Look for the PLAINTEXT: prefix
                if "PLAINTEXT:" in output:
                    parts = output.split("PLAINTEXT:")
                    if len(parts) >= 2:
                        decrypted_b64 = parts[-1].strip()
                        try:
                            # Decode from base64
                            decrypted_bytes = base64.b64decode(decrypted_b64)
                            if return_bytes:
                                logger.info(f"Successfully decrypted {len(decrypted_bytes)} bytes")
                                return True, decrypted_bytes
                            else:
                                decrypted = decrypted_bytes.decode('utf-8')
                                logger.info(f"Successfully decrypted {len(decrypted)} characters")
                                return True, decrypted
                        except Exception as decode_error:
                            logger.error(f"Base64 decode error: {str(decode_error)}")
                            return False, f"Base64 decode error: {str(decode_error)}"
                    else:
                        error_msg = "Invalid output format - no data after PLAINTEXT:"
                        logger.error(error_msg)
                        return False, error_msg
                else:
                    error_msg = f"No PLAINTEXT: prefix found in output"
                    logger.error(error_msg)
                    return False, error_msg
            else:
                error_msg = result.stderr if result.stderr else "Unknown error"
                logger.error(f"Decryption failed: {error_msg}")
                return False, error_msg

        except Exception as e:
            logger.error(f"Exception during decryption: {str(e)}")
            return False, str(e)

    def decrypt_envelope(self, encrypted_data_key: str, encrypted_data: str, 
                        credentials: Dict[str, str]) -> Tuple[bool, bytes]:
        """
        Decrypt using envelope encryption
        
        Args:
            encrypted_data_key: Base64-encoded encrypted data key
            encrypted_data: Base64-encoded encrypted data
            credentials: AWS credentials dict
            
        Returns:
            Tuple of (success, decrypted_bytes or error_message)
        """
        try:
            # First decrypt the data key using KMS
            logger.info("Decrypting data key with KMS...")
            success, plaintext_key = self.decrypt_with_kms(
                encrypted_data_key, credentials, return_bytes=True
            )
            if not success:
                return False, f"Failed to decrypt data key: {plaintext_key}"

            # Then decrypt the data using the plaintext key
            logger.info("Decrypting data with envelope key...")
            encrypted_data_bytes = base64.b64decode(encrypted_data)
            fernet = Fernet(base64.urlsafe_b64encode(plaintext_key[:32]))
            decrypted_data = fernet.decrypt(encrypted_data_bytes)
            
            logger.info(f"Envelope decrypted {len(decrypted_data)} bytes")
            return True, decrypted_data

        except Exception as e:
            logger.error(f"Envelope decryption failed: {str(e)}")
            return False, str(e).encode()

    def decrypt_data(self, encryption_info: Dict[str, Any], 
                     credentials: Dict[str, str]) -> Tuple[bool, bytes]:
        """
        Decrypt data based on encryption method
        
        Args:
            encryption_info: Dict with 'method' and encryption data
            credentials: AWS credentials dict
            
        Returns:
            Tuple of (success, decrypted_bytes or error_message)
        """
        try:
            method = encryption_info.get('method', 'direct')
            
            if method == 'direct':
                return self.decrypt_with_kms(
                    encryption_info['ciphertext'], credentials, return_bytes=True
                )
            elif method == 'envelope':
                return self.decrypt_envelope(
                    encryption_info['encrypted_data_key'],
                    encryption_info['encrypted_data'],
                    credentials
                )
            else:
                return False, f"Unknown encryption method: {method}".encode()
                
        except Exception as e:
            return False, str(e).encode()