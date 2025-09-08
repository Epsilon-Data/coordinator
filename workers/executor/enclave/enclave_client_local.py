#!/usr/bin/env python3
"""
Local Enclave Client for testing without Nitro Enclaves
Uses real AWS KMS for encryption/decryption but simulates enclave execution
"""

import json
import base64
import boto3
import logging
import os
import sys
import tempfile
import subprocess
from typing import Dict, Any, Optional, Tuple
from cryptography.fernet import Fernet

from interfaces import IEnclaveClient
from config import get_settings
from exceptions import EnclaveConnectionError, EnclaveDecryptionError
from utils import get_logger

logger = get_logger(__name__)

class EnclaveClientLocal(IEnclaveClient):
    """Local client that simulates Nitro Enclave behavior using real KMS"""
    
    def __init__(self, enclave_cid: int = None):
        """Initialize local enclave client with real AWS credentials"""
        self._settings = get_settings()
        self.enclave_cid = enclave_cid or 999  # Fake CID for local testing
        self._connected = True
        
        # Debug: Print environment variables (remove in production)
        logger.info(f"AWS_ACCESS_KEY_ID present: {'AWS_ACCESS_KEY_ID' in os.environ}")
        logger.info(f"AWS_SECRET_ACCESS_KEY present: {'AWS_SECRET_ACCESS_KEY' in os.environ}")
        logger.info(f"AWS_KMS_KEY_ARN: {self._settings.aws.kms_key_arn}")
        logger.info(f"KMS_REGION: {self._settings.aws.region}")
        
        # Get credentials from environment
        if not os.environ.get('AWS_ACCESS_KEY_ID'):
            raise ValueError("AWS_ACCESS_KEY_ID not set in environment")
        if not os.environ.get('AWS_SECRET_ACCESS_KEY'):
            raise ValueError("AWS_SECRET_ACCESS_KEY not set in environment")
        if not self._settings.aws.kms_key_arn:
            raise ValueError("AWS_KMS_KEY_ARN not set in settings")
            
        self.credentials = {
            'access_key_id': os.environ['AWS_ACCESS_KEY_ID'],
            'secret_access_key': os.environ['AWS_SECRET_ACCESS_KEY'],
            'session_token': os.environ.get('AWS_SESSION_TOKEN', '')
        }
        
        # Create real KMS client
        self.kms_client = boto3.client(
            'kms',
            region_name=self._settings.aws.region,
            aws_access_key_id=self.credentials['access_key_id'],
            aws_secret_access_key=self.credentials['secret_access_key'],
            aws_session_token=self.credentials['session_token'] if self.credentials['session_token'] else None
        )
        
        logger.info(f"Initialized EnclaveClientLocal with KMS in {self._settings.aws.region}")
        logger.info(f"Using KMS Key: {self._settings.aws.kms_key_arn}")
        
    def encrypt_data(self, plaintext: str) -> Dict[str, Any]:
        """Encrypt data using real KMS (direct or envelope encryption based on size)"""
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
        """Encrypt large data using envelope encryption with real KMS"""
        try:
            logger.info(f"Using envelope encryption for {len(plaintext)} bytes")
            
            # Generate a data key using real KMS
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
    
    def _decrypt_data(self, encryption_info: Dict[str, Any]) -> bytes:
        """Decrypt data using real KMS (supports both direct and envelope)"""
        try:
            logger.error(f"🔓🔓🔓 DECRYPTING DATA WITH METHOD: {encryption_info.get('method')}")
            logger.error(f"🔓 Encryption info keys: {encryption_info.keys()}")
            
            if encryption_info.get('method') == 'direct':
                # Direct KMS decryption
                ciphertext = base64.b64decode(encryption_info['ciphertext'])
                response = self.kms_client.decrypt(
                    CiphertextBlob=ciphertext
                )
                plaintext = response['Plaintext']  # Return bytes, not decoded string
                logger.info("Direct KMS decrypted successfully")
                return plaintext
                
            elif encryption_info.get('method') == 'envelope':
                # Envelope decryption
                logger.error("🔓📦 Using envelope decryption")
                
                # First decrypt the data key using KMS
                encrypted_data_key = base64.b64decode(encryption_info['encrypted_data_key'])
                logger.error(f"🔑 Encrypted data key size: {len(encrypted_data_key)} bytes")
                
                response = self.kms_client.decrypt(
                    CiphertextBlob=encrypted_data_key
                )
                plaintext_key = response['Plaintext']
                logger.error(f"🔑 Plaintext key size: {len(plaintext_key)} bytes")
                
                # Then decrypt the data using the plaintext key
                encrypted_data = base64.b64decode(encryption_info['encrypted_data'])
                logger.error(f"📦 Encrypted data size: {len(encrypted_data)} bytes")
                
                fernet = Fernet(base64.urlsafe_b64encode(plaintext_key[:32]))
                plaintext = fernet.decrypt(encrypted_data)  # Return bytes, not decoded string
                
                logger.error(f"✅ Envelope decrypted successfully - plaintext size: {len(plaintext)} bytes")
                logger.error(f"🔍 First 50 bytes: {plaintext[:50]}")
                
                # Check if it looks like zip data
                if plaintext.startswith(b'PK'):
                    logger.error("✅ Data starts with PK - looks like valid zip")
                else:
                    logger.error("❌ Data does NOT start with PK - not valid zip format")
                    logger.error(f"❌ Actual start: {plaintext[:10]}")
                
                return plaintext
                
            else:
                raise ValueError(f"Unknown encryption method: {encryption_info.get('method')}")
                
        except Exception as e:
            logger.error(f"Decryption failed: {str(e)}")
            raise
    
    def send_to_enclave(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate enclave processing locally"""
        logger.info(f"Local enclave simulation processing request: {request_data['operation']}")
        
        try:
            operation = request_data.get('operation', '')
            
            if operation == 'execute_script_envelope':
                # Check if this is bundle mode (empty encrypted script)
                encrypted_script = request_data['encrypted_script']
                if encrypted_script.get('ciphertext') == '':
                    # Bundle mode - script is in the encrypted data (bundle)
                    logger.info("📦 Bundle mode detected - extracting script from bundle")
                    script_plaintext = ""  # Will be found in bundle
                else:
                    # Normal mode - decrypt the script
                    script_bytes = self._decrypt_data(encrypted_script)
                    script_plaintext = script_bytes.decode('utf-8')
                
                # Decrypt the data (this is the bundle in bundle mode, or dataset in normal mode)
                logger.info("🔓 DECRYPTING DATA...")
                data_bytes = self._decrypt_data(request_data['encrypted_data'])
                logger.info(f"📊 Decrypted data type: {type(data_bytes)}")
                logger.info(f"📊 Decrypted data length: {len(data_bytes)}")
                
                # For bundle mode, data_bytes is the zip file (binary)
                # For normal mode, convert to string
                if encrypted_script.get('ciphertext') == '':
                    # Bundle mode - pass binary data directly
                    logger.info("🎯 BUNDLE MODE: Passing binary data to execution")
                    return self._execute_script_locally(script_plaintext, data_bytes)
                else:
                    # Normal mode - convert to string
                    logger.info("📝 NORMAL MODE: Converting to string")
                    data_plaintext = data_bytes.decode('utf-8')
                    return self._execute_script_locally(script_plaintext, data_plaintext)
                
            elif operation == 'decrypt':
                # Simple decrypt operation
                plaintext = self._decrypt_data({
                    'method': 'direct',
                    'ciphertext': request_data['ciphertext']
                })
                return {
                    'status': 'success',
                    'operation': 'decrypt',
                    'plaintext': plaintext
                }
                
            elif operation == 'decrypt_envelope':
                # Envelope decrypt operation
                plaintext = self._decrypt_data({
                    'method': 'envelope',
                    'encrypted_data_key': request_data['encrypted_data_key'],
                    'encrypted_data': request_data['encrypted_data']
                })
                return {
                    'status': 'success',
                    'operation': 'decrypt_envelope',
                    'plaintext': plaintext
                }
                
            elif operation == 'health_check':
                return {'status': 'healthy', 'message': 'Local enclave simulation is running'}
                
            else:
                return {
                    'status': 'error',
                    'message': f'Unknown operation: {operation}'
                }
                
        except Exception as e:
            logger.error(f"Local enclave processing failed: {str(e)}")
            return {
                'status': 'error',
                'message': str(e)
            }
    
    def _execute_script_locally(self, script_content: str, data_content) -> Dict[str, Any]:
        """Execute Python script locally with data"""
        logger.info("🐍 EXECUTING SCRIPT LOCALLY (simulating enclave execution)")
        logger.info(f"📊 Data content type: {type(data_content)}")
        logger.info(f"📊 Data content length: {len(data_content)}")
        
        # Check if data_content is binary (bundle mode) or string (normal mode)
        is_binary_data = isinstance(data_content, bytes)
        logger.info(f"🔍 Is binary data: {is_binary_data}")
        
        if is_binary_data:
            logger.info("📦 Binary data detected - this is the decrypted zip bundle")
            logger.info(f"📊 Bundle size: {len(data_content)} bytes")
            
            # Bundle mode: data_content is the decrypted zip file bytes
            try:
                import zipfile
                import tempfile
                import shutil
                
                # Create temporary directory for bundle extraction
                temp_bundle_dir = tempfile.mkdtemp(prefix="enclave_bundle_")
                logger.info(f"📁 Created temporary bundle directory: {temp_bundle_dir}")
                
                try:
                    # Write zip data to temporary file
                    temp_zip = os.path.join(temp_bundle_dir, "bundle.zip")
                    logger.info(f"📝 Writing zip data to: {temp_zip}")
                    
                    with open(temp_zip, 'wb') as f:
                        f.write(data_content)
                    
                    logger.info(f"✅ Wrote {len(data_content)} bytes to zip file")
                    
                    # Verify the file was written correctly
                    if not os.path.exists(temp_zip):
                        raise FileNotFoundError(f"Zip file was not created: {temp_zip}")
                    
                    zip_file_size = os.path.getsize(temp_zip)
                    logger.info(f"📊 Zip file size on disk: {zip_file_size} bytes")
                    
                    # Extract zip to bundle directory
                    logger.info(f"📦 Attempting to extract zip file...")
                    with zipfile.ZipFile(temp_zip, 'r') as zipf:
                        zipf.extractall(temp_bundle_dir)
                        extracted_files = zipf.namelist()
                    
                    logger.info(f"📋 Extracted {len(extracted_files)} files from bundle")
                    logger.info(f"📄 Files: {', '.join(extracted_files[:10])}{' ...' if len(extracted_files) > 10 else ''}")
                    
                    # Step 4: Decrypt CSV files in archetypes directories
                    logger.info("🔓 Decrypting CSV files in bundle...")
                    archetype_dirs = os.path.join(temp_bundle_dir, "archetypes")
                    if os.path.exists(archetype_dirs):
                        for item in os.listdir(archetype_dirs):
                            dataset_dir = os.path.join(archetype_dirs, item)
                            if os.path.isdir(dataset_dir):
                                # Look for CSV files (both legacy .csv and new .csv.encrypted)
                                csv_files = [f for f in os.listdir(dataset_dir) if f.endswith('.csv') or f.endswith('.csv.encrypted')]
                                logger.error(f"🔍 Found CSV files in {dataset_dir}: {csv_files}")
                                
                                for csv_file in csv_files:
                                    csv_path = os.path.join(dataset_dir, csv_file)
                                    logger.error(f"🔐 Decrypting CSV: {csv_path}")
                                    
                                    if csv_file.endswith('.csv.encrypted'):
                                        # Handle new binary encrypted format
                                        self._decrypt_binary_csv_file(csv_path)
                                    else:
                                        # Handle legacy JSON format
                                        # Debug: Show content before decryption
                                        with open(csv_path, 'r') as f:
                                            content = f.read()
                                            logger.error(f"📄 CSV content before decryption ({len(content)} chars): {content[:200]}...")
                                        
                                        self._decrypt_csv_file(csv_path)
                                        
                                        # Debug: Show content after decryption
                                        with open(csv_path, 'r') as f:
                                            content = f.read()
                                            logger.error(f"📄 CSV content after decryption ({len(content)} chars): {content[:200]}...")
                    
                    # Find the main script file from build.yml
                    build_yml_path = os.path.join(temp_bundle_dir, "build", "build.yml")
                    main_script_name = "example_analysis.py"  # default
                    
                    if os.path.exists(build_yml_path):
                        try:
                            import yaml
                            with open(build_yml_path, 'r') as f:
                                build_config = yaml.safe_load(f)
                                main_script_name = build_config.get('analysis', {}).get('script_file', 'example_analysis.py')
                                logger.info(f"📋 Found script from build.yml: {main_script_name}")
                        except Exception as e:
                            logger.warning(f"Could not parse build.yml: {e}")
                    
                    # Look for the script file
                    main_script_candidates = [main_script_name, 'build/' + main_script_name, 'example_analysis.py', 'test.py']
                    main_script_path = None
                    
                    for candidate in main_script_candidates:
                        candidate_path = os.path.join(temp_bundle_dir, candidate)
                        if os.path.exists(candidate_path):
                            main_script_path = candidate_path
                            logger.info(f"🎯 Found main script: {candidate}")
                            break
                    
                    if not main_script_path:
                        # Create the script from script_content if provided
                        if script_content.strip():
                            main_script_path = os.path.join(temp_bundle_dir, "main_script.py")
                            with open(main_script_path, 'w') as f:
                                f.write(script_content)
                            logger.info(f"📝 Created main script from content: main_script.py")
                        else:
                            raise FileNotFoundError("No script found in bundle and no script_content provided")
                    
                    # Execute script from the bundle directory (so imports work)
                    env = os.environ.copy()
                    env['PYTHONPATH'] = temp_bundle_dir + ':' + env.get('PYTHONPATH', '')
                    
                    # Change to bundle directory for execution
                    original_cwd = os.getcwd()
                    os.chdir(temp_bundle_dir)
                    
                    try:
                        logger.info(f"🏃 Executing script: {main_script_path}")
                        result = subprocess.run(
                            [sys.executable, main_script_path],
                            capture_output=True,
                            text=True,
                            timeout=60,
                            env=env,
                            cwd=temp_bundle_dir
                        )
                        
                        logger.info(f"🏁 Script execution completed with return code: {result.returncode}")
                        if result.stdout:
                            logger.info(f"📤 STDOUT: {result.stdout}")
                        if result.stderr:
                            logger.info(f"⚠️  STDERR: {result.stderr}")
                        
                    finally:
                        os.chdir(original_cwd)
                    
                finally:
                    # Clean up temporary directory
                    shutil.rmtree(temp_bundle_dir, ignore_errors=True)
                    logger.info(f"🧹 Cleaned up temporary bundle directory")
                    
            except Exception as e:
                logger.error(f"❌ Error during bundle processing: {str(e)}")
                return {
                    'status': 'error',
                    'operation': 'execute_script_envelope',
                    'message': f'Bundle processing error: {str(e)}'
                }
        else:
            # Normal mode: data_content is string
            logger.info("📄 String data detected - normal execution mode")
            
            # Log decrypted contents (first few lines for security)  
            script_preview = '\n'.join(script_content.split('\n')[:5])
            data_preview = data_content[:200] + "..." if len(data_content) > 200 else data_content
            
            logger.info(f"📜 Decrypted script content (first 5 lines):\n{script_preview}")
            logger.info(f"📊 Decrypted data content ({len(data_content)} chars):\n{data_preview}")
            
            # Execute as normal script with string data
            result = self._execute_single_script(script_content, data_content)
            
        # Process result (common for both modes)
        if result.returncode == 0:
            output = result.stdout
            if result.stderr:
                output += f"\n--- STDERR ---\n{result.stderr}"
            
            return {
                'status': 'success',
                'operation': 'execute_script_envelope',
                'output': output
            }
        else:
            error_output = f"Script failed with exit code {result.returncode}\n"
            error_output += f"STDOUT: {result.stdout}\n"
            error_output += f"STDERR: {result.stderr}"
            
            return {
                'status': 'error',
                'operation': 'execute_script_envelope',
                'message': error_output
            }

    def _execute_single_script(self, script_content: str, data_content: str):
        """Execute a single script file (fallback method)"""
        try:
            # Create temporary script file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(script_content)
                script_path = f.name
            
            # Execute script with data in environment
            env = os.environ.copy()
            env['ENCLAVE_DATA'] = data_content
            
            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=60,
                env=env
            )
            
            # Clean up
            os.unlink(script_path)
            return result
            
        except subprocess.TimeoutExpired:
            return type('Result', (), {
                'returncode': -1,
                'stdout': '',
                'stderr': 'Script execution timed out (60 seconds)'
            })()
        except Exception as e:
            return type('Result', (), {
                'returncode': -1,
                'stdout': '',
                'stderr': f'Script execution error: {str(e)}'
            })()
            
    def execute_script(self, script_content: str, data: str, script_path: str = None, 
                      data_already_encrypted: bool = False) -> Tuple[bool, str]:
        """Execute script with data (compatible with EnclaveClient interface)"""
        try:
            logger.info("=" * 80)
            logger.info(f"🚀 ENCLAVE EXECUTION STARTING")
            logger.info(f"📝 Script content length: {len(script_content)} chars")
            logger.info(f"📊 Data length: {len(data)} chars")
            logger.info(f"🔒 Data already encrypted: {data_already_encrypted}")
            logger.info(f"📂 Script path: {script_path}")
            logger.info("=" * 80)
            
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
                logger.info("🎯 BUNDLE EXECUTION MODE DETECTED - script is in the encrypted bundle")
                logger.info("📦 Creating empty encrypted script for bundle mode")
                # For bundle execution, we pass empty encrypted script
                encrypted_script = {'method': 'direct', 'ciphertext': ''}
            else:
                logger.info("📝 NORMAL EXECUTION MODE - encrypting provided script")
                logger.info(f"🔍 Script preview: {script_content[:100]}...")
                # Encrypt script (normal mode)
                encrypted_script = self.encrypt_data(script_content)
            
            # Prepare request (same as real enclave client)
            request = {
                'operation': 'execute_script_envelope',
                'encrypted_data': encrypted_data,
                'encrypted_script': encrypted_script,
                'credentials': self.credentials,
                'script_path': script_path or 'script.py'
            }
            
            # Process locally instead of sending to enclave
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
            
    def _decrypt_csv_file(self, csv_file_path: str):
        """Decrypt a CSV file in place using KMS (supports both direct and envelope decryption)"""
        try:
            # Read the encrypted file content
            with open(csv_file_path, 'r') as f:
                content = f.read()
            
            # Try to parse as JSON metadata
            try:
                encryption_metadata = json.loads(content)
                
                if encryption_metadata.get('method') == 'direct':
                    # Direct KMS decryption
                    logger.info(f"🔓 Using direct KMS decryption for {csv_file_path}")
                    logger.info(f"🔐 Ciphertext length: {len(encryption_metadata['ciphertext'])} chars")
                    ciphertext = base64.b64decode(encryption_metadata['ciphertext'])
                    logger.info(f"🔐 Decoded ciphertext length: {len(ciphertext)} bytes")
                    
                    response = self.kms_client.decrypt(CiphertextBlob=ciphertext)
                    plaintext = response['Plaintext']
                    logger.info(f"🔓 Decrypted plaintext length: {len(plaintext)} bytes")
                    
                elif encryption_metadata.get('method') == 'envelope':
                    # Envelope decryption
                    logger.info(f"📦 Using envelope decryption for {csv_file_path}")
                    
                    # First decrypt the data key
                    encrypted_data_key = base64.b64decode(encryption_metadata['encrypted_data_key'])
                    response = self.kms_client.decrypt(CiphertextBlob=encrypted_data_key)
                    plaintext_key = response['Plaintext']
                    
                    # Then decrypt the data using the plaintext key
                    encrypted_data = base64.b64decode(encryption_metadata['encrypted_data'])
                    fernet = Fernet(base64.urlsafe_b64encode(plaintext_key[:32]))
                    plaintext = fernet.decrypt(encrypted_data)
                    
                else:
                    logger.warning(f"Unknown encryption method: {encryption_metadata.get('method')}")
                    return
                    
            except json.JSONDecodeError:
                # Not JSON metadata, assume file is already decrypted
                logger.info(f"📄 CSV file appears to be already decrypted: {csv_file_path}")
                return
            
            # Write decrypted content back to file
            with open(csv_file_path, 'wb') as f:
                f.write(plaintext)
                
            # Log first few lines of decrypted content for debugging
            decrypted_preview = plaintext.decode('utf-8')[:200] + "..." if len(plaintext) > 200 else plaintext.decode('utf-8')
            logger.info(f"✅ Successfully decrypted CSV: {csv_file_path}")
            logger.info(f"📄 Decrypted content preview: {decrypted_preview}")
            
        except Exception as e:
            logger.error(f"❌ Failed to decrypt CSV file {csv_file_path}: {e}")
            # For testing, continue without decryption
            logger.warning("Continuing without decryption for testing")

    def _decrypt_binary_csv_file(self, encrypted_csv_path: str):
        """Decrypt a binary encrypted CSV file and restore original CSV"""
        import json
        from pathlib import Path
        
        try:
            csv_path = Path(encrypted_csv_path)
            logger.error(f"🔓 Decrypting binary encrypted CSV: {csv_path}")
            
            # Read metadata file
            meta_path = csv_path.with_suffix('.meta')
            if not meta_path.exists():
                logger.error(f"❌ Metadata file not found: {meta_path}")
                return
            
            with open(meta_path, 'r') as f:
                metadata = json.load(f)
            
            # Read binary encrypted file
            with open(encrypted_csv_path, 'rb') as f:
                # Read key length (4 bytes)
                key_length = int.from_bytes(f.read(4), 'big')
                
                # Read encrypted data key
                encrypted_data_key = f.read(key_length)
                
                # Read encrypted content
                encrypted_content = f.read()
            
            # Decrypt data key using KMS
            kms_response = self.kms_client.decrypt(
                CiphertextBlob=encrypted_data_key
            )
            plaintext_key = kms_response['Plaintext']
            
            # Decrypt content using data key
            from cryptography.fernet import Fernet
            import base64
            fernet = Fernet(base64.urlsafe_b64encode(plaintext_key[:32]))
            decrypted_content = fernet.decrypt(encrypted_content)
            
            # Restore original CSV file
            original_filename = metadata.get('original_filename', csv_path.name.replace('.encrypted', ''))
            original_csv_path = csv_path.parent / original_filename
            
            with open(original_csv_path, 'wb') as f:
                f.write(decrypted_content)
            
            # Clean up encrypted files
            csv_path.unlink()
            meta_path.unlink()
            
            # Log success with preview
            decrypted_preview = decrypted_content.decode('utf-8')[:200]
            if len(decrypted_content) > 200:
                decrypted_preview += "..."
            
            logger.error(f"✅ Successfully decrypted binary CSV: {original_csv_path}")
            logger.error(f"📄 Decrypted content preview: {decrypted_preview}")
            
        except Exception as e:
            logger.error(f"❌ Failed to decrypt binary CSV {encrypted_csv_path}: {e}")
            raise

    def health_check(self) -> bool:
        """Check if local simulation is working"""
        try:
            # Test KMS access
            self.kms_client.describe_key(KeyId=self._settings.aws.kms_key_arn)
            return True
        except Exception:
            return False
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected to enclave"""
        return self._connected
    
    def connect(self) -> None:
        """Establish connection to the local simulation"""
        try:
            # Test KMS access
            self.kms_client.describe_key(KeyId=self._settings.aws.kms_key_arn)
            self._connected = True
            logger.info(f"Connected to local enclave simulation")
        except Exception as e:
            self._connected = False
            logger.error(f"Failed to connect to local simulation: {e}")
            raise EnclaveConnectionError(f"Failed to connect to local simulation: {e}")
    
    def disconnect(self) -> None:
        """Disconnect from the local simulation"""
        self._connected = False
        logger.info("Disconnected from local enclave simulation")