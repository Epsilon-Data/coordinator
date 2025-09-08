"""
CSV decryption module for handling encrypted CSV files in bundles
"""
import json
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)


class CSVDecryptor:
    """Handles decryption of CSV files within bundles"""
    
    def __init__(self, kms_decryptor):
        self.kms_decryptor = kms_decryptor
        
    def decrypt_csv_file(self, csv_file_path: str, credentials: Dict[str, str]):
        """
        Decrypt a CSV file in place using KMS
        Supports both direct, envelope, and new binary format decryption
        
        Args:
            csv_file_path: Path to the encrypted CSV file
            credentials: AWS credentials for KMS decryption
        """
        csv_path = Path(csv_file_path)
        
        try:
            # Check if this is a new binary encrypted file (.csv.encrypted)
            if csv_path.suffix == '.encrypted' and csv_path.name.endswith('.csv.encrypted'):
                self._decrypt_binary_csv(csv_path, credentials)
                return
            
            # Legacy JSON format handling
            # Read the encrypted file content
            with open(csv_file_path, 'r') as f:
                content = f.read()
            
            # Try to parse as JSON metadata
            try:
                encryption_metadata = json.loads(content)
                
                if encryption_metadata.get('method') in ['direct', 'envelope']:
                    logger.info(f"🔓 Using {encryption_metadata.get('method')} KMS decryption for {csv_file_path}")
                    
                    # Decrypt using KMS
                    success, plaintext_bytes = self.kms_decryptor.decrypt_data(
                        encryption_metadata, credentials
                    )
                    
                    if not success:
                        logger.error(f"Failed to decrypt {csv_file_path}: {plaintext_bytes}")
                        return
                    
                    # Write decrypted content back to file
                    with open(csv_file_path, 'wb') as f:
                        f.write(plaintext_bytes)
                    
                    # Log success with preview
                    preview_len = min(200, len(plaintext_bytes))
                    preview = plaintext_bytes.decode('utf-8')[:preview_len]
                    if len(plaintext_bytes) > preview_len:
                        preview += "..."
                    
                    logger.info(f"✅ Successfully decrypted CSV: {csv_file_path}")
                    logger.info(f"📄 Decrypted content preview: {preview}")
                else:
                    logger.warning(f"Unknown encryption method: {encryption_metadata.get('method')}")
                    
            except json.JSONDecodeError:
                # Not JSON metadata, assume file is already decrypted
                logger.info(f"📄 CSV file appears to be already decrypted: {csv_file_path}")
                
        except Exception as e:
            logger.error(f"❌ Failed to decrypt CSV file {csv_file_path}: {e}")
            # Continue without failing the entire process
            logger.warning("Continuing without decryption for this file")
    
    def _decrypt_binary_csv(self, encrypted_csv_path: Path, credentials: Dict[str, str]):
        """Decrypt a binary encrypted CSV file and restore original CSV"""
        try:
            logger.info(f"🔓 Decrypting binary encrypted CSV: {encrypted_csv_path}")
            
            # Read metadata file
            meta_path = encrypted_csv_path.with_suffix('.meta')
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
            
            # Create envelope metadata for KMS decryptor
            envelope_metadata = {
                'method': 'envelope',
                'encrypted_data_key': encrypted_data_key,
                'encrypted_data': encrypted_content
            }
            
            # Decrypt using KMS
            success, plaintext_bytes = self.kms_decryptor.decrypt_data(
                envelope_metadata, credentials
            )
            
            if not success:
                logger.error(f"Failed to decrypt binary CSV {encrypted_csv_path}: {plaintext_bytes}")
                return
            
            # Restore original CSV file
            original_filename = metadata.get('original_filename', encrypted_csv_path.name.replace('.encrypted', ''))
            original_csv_path = encrypted_csv_path.parent / original_filename
            
            with open(original_csv_path, 'wb') as f:
                f.write(plaintext_bytes)
            
            # Clean up encrypted files
            encrypted_csv_path.unlink()
            meta_path.unlink()
            
            # Log success with preview
            preview_len = min(200, len(plaintext_bytes))
            preview = plaintext_bytes.decode('utf-8')[:preview_len]
            if len(plaintext_bytes) > preview_len:
                preview += "..."
            
            logger.info(f"✅ Successfully decrypted binary CSV: {original_csv_path}")
            logger.info(f"📄 Decrypted content preview: {preview}")
            
        except Exception as e:
            logger.error(f"❌ Failed to decrypt binary CSV {encrypted_csv_path}: {e}")
            raise