"""
Script executor module for running Python scripts in the enclave
"""
import os
import sys
import subprocess
import tempfile
import logging
from typing import Tuple, Dict, Any

from config import SCRIPT_EXECUTION_TIMEOUT
from bundle_executor import BundleExecutor

logger = logging.getLogger(__name__)


class ScriptExecutor:
    """Handles execution of Python scripts with data"""
    
    def __init__(self, kms_decryptor):
        self.kms_decryptor = kms_decryptor
        self.bundle_executor = BundleExecutor(kms_decryptor)
        
    def execute_script_with_bundle(self, encrypted_data: Dict[str, Any],
                                  encrypted_script: Dict[str, Any],
                                  credentials: Dict[str, str],
                                  script_path: str = 'script.py') -> Tuple[bool, str]:
        """
        Execute script with optional bundle support
        
        Args:
            encrypted_data: Encrypted data (bundle or dataset)
            encrypted_script: Encrypted script (empty for bundle mode)
            credentials: AWS credentials
            script_path: Script path for logging
            
        Returns:
            Tuple of (success, output or error message)
        """
        try:
            # Check if this is bundle mode (empty encrypted script)
            is_bundle_mode = encrypted_script.get('ciphertext') == ''
            
            if is_bundle_mode:
                logger.info("📦 Bundle mode detected - extracting script from bundle")
                script_plaintext = ""  # Will be found in bundle
            else:
                # Normal mode - decrypt the script
                success, script_bytes = self.kms_decryptor.decrypt_data(
                    encrypted_script, credentials
                )
                if not success:
                    return False, f"Failed to decrypt script: {script_bytes.decode()}"
                script_plaintext = script_bytes.decode('utf-8')

            # Decrypt the data
            success, data_bytes = self.kms_decryptor.decrypt_data(
                encrypted_data, credentials
            )
            if not success:
                return False, f"Failed to decrypt data: {data_bytes.decode()}"

            # Execute based on mode
            if is_bundle_mode:
                # Bundle mode - data_bytes is the zip file
                return self.bundle_executor.execute_bundle(
                    data_bytes, script_plaintext, credentials
                )
            else:
                # Normal mode - execute single script
                data_plaintext = data_bytes.decode('utf-8')
                return self.execute_single_script(script_plaintext, data_plaintext)

        except Exception as e:
            logger.error(f"Script execution failed: {str(e)}")
            return False, str(e)

    def execute_single_script(self, script_content: str, data_content: str) -> Tuple[bool, str]:
        """
        Execute a single script file with data
        
        Args:
            script_content: Python script content
            data_content: Data to pass to the script
            
        Returns:
            Tuple of (success, output or error message)
        """
        script_file = None
        try:
            # Create temporary script file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(script_content)
                script_file = f.name
            
            # Execute script with data in environment
            env = os.environ.copy()
            env['ENCLAVE_DATA'] = data_content
            
            logger.info(f"🏃 Executing single script: {script_file}")
            result = subprocess.run(
                [sys.executable, script_file],
                capture_output=True,
                text=True,
                timeout=SCRIPT_EXECUTION_TIMEOUT,
                env=env
            )
            
            logger.info(f"🏁 Script execution completed with return code: {result.returncode}")
            
            # Format output
            if result.returncode == 0:
                output = result.stdout
                if result.stderr:
                    output += f"\n--- STDERR ---\n{result.stderr}"
                return True, output
            else:
                error_output = f"Script failed with exit code {result.returncode}\n"
                error_output += f"STDOUT: {result.stdout}\n"
                error_output += f"STDERR: {result.stderr}"
                return False, error_output
            
        except subprocess.TimeoutExpired:
            return False, f'Script execution timed out ({SCRIPT_EXECUTION_TIMEOUT} seconds)'
        except Exception as e:
            return False, f'Script execution error: {str(e)}'
        finally:
            # Clean up temporary file
            if script_file and os.path.exists(script_file):
                os.unlink(script_file)