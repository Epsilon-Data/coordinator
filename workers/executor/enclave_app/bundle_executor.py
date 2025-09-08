"""
Bundle executor module for running scripts from encrypted bundles
"""
import os
import sys
import json
import zipfile
import tempfile
import shutil
import subprocess
import logging
from typing import Tuple, Optional
from pathlib import Path

from config import TEMP_DIR_PREFIX, SCRIPT_EXECUTION_TIMEOUT
from csv_decryptor import CSVDecryptor

logger = logging.getLogger(__name__)


class BundleExecutor:
    """Handles execution of scripts from encrypted bundles"""
    
    def __init__(self, kms_decryptor):
        self.kms_decryptor = kms_decryptor
        self.csv_decryptor = CSVDecryptor(kms_decryptor)
        
    def execute_bundle(self, bundle_data: bytes, script_content: str,
                      credentials: dict) -> Tuple[bool, str]:
        """
        Execute script from extracted bundle
        
        Args:
            bundle_data: Decrypted zip file bytes
            script_content: Optional script content (empty for bundle mode)
            credentials: AWS credentials for CSV decryption
            
        Returns:
            Tuple of (success, output or error message)
        """
        logger.info("🐍 Executing script from bundle (inside enclave)")
        logger.info(f"📊 Bundle size: {len(bundle_data)} bytes")
        
        temp_bundle_dir = None
        try:
            # Create temporary directory for bundle extraction
            temp_bundle_dir = tempfile.mkdtemp(prefix=TEMP_DIR_PREFIX)
            logger.info(f"📁 Created temporary bundle directory: {temp_bundle_dir}")
            
            # Extract bundle
            extracted_files = self._extract_bundle(bundle_data, temp_bundle_dir)
            logger.info(f"📋 Extracted {len(extracted_files)} files from bundle")
            
            # Decrypt CSV files
            self._decrypt_csv_files(temp_bundle_dir, credentials)
            
            # Find and execute main script
            main_script_path = self._find_main_script(temp_bundle_dir, script_content)
            if not main_script_path:
                return False, "No script found in bundle and no script_content provided"
                
            # Execute the script
            return self._execute_script(main_script_path, temp_bundle_dir)
            
        except Exception as e:
            logger.error(f"❌ Error during bundle processing: {str(e)}")
            return False, f"Bundle processing error: {str(e)}"
        finally:
            # Clean up temporary directory
            if temp_bundle_dir and os.path.exists(temp_bundle_dir):
                shutil.rmtree(temp_bundle_dir, ignore_errors=True)
                logger.info("🧹 Cleaned up temporary bundle directory")
    
    def _extract_bundle(self, bundle_data: bytes, target_dir: str) -> list:
        """Extract zip bundle to target directory"""
        temp_zip = os.path.join(target_dir, "bundle.zip")
        
        # Write zip data to temporary file
        with open(temp_zip, 'wb') as f:
            f.write(bundle_data)
        
        # Extract zip
        with zipfile.ZipFile(temp_zip, 'r') as zipf:
            zipf.extractall(target_dir)
            extracted_files = zipf.namelist()
            
        # Log first few files
        preview = ', '.join(extracted_files[:10])
        if len(extracted_files) > 10:
            preview += ' ...'
        logger.info(f"📄 Files: {preview}")
        
        return extracted_files
    
    def _decrypt_csv_files(self, bundle_dir: str, credentials: dict):
        """Decrypt all CSV files in archetypes directories"""
        logger.info("🔓 Decrypting CSV files in bundle...")
        
        archetype_dirs = os.path.join(bundle_dir, "archetypes")
        if not os.path.exists(archetype_dirs):
            logger.info("No archetypes directory found, skipping CSV decryption")
            return
            
        # Walk through archetypes subdirectories
        for dataset_name in os.listdir(archetype_dirs):
            dataset_dir = os.path.join(archetype_dirs, dataset_name)
            if not os.path.isdir(dataset_dir):
                continue
                
            # Find and decrypt CSV files (both legacy .csv and new .csv.encrypted)
            csv_files = [f for f in os.listdir(dataset_dir) if f.endswith('.csv') or f.endswith('.csv.encrypted')]
            for csv_file in csv_files:
                csv_path = os.path.join(dataset_dir, csv_file)
                logger.info(f"🔐 Decrypting CSV: {csv_path}")
                self.csv_decryptor.decrypt_csv_file(csv_path, credentials)
    
    def _find_main_script(self, bundle_dir: str, script_content: str) -> Optional[str]:
        """Find the main script to execute"""
        # Try to get script name from build.yml
        main_script_name = self._get_script_from_build_yml(bundle_dir)
        
        # List of candidates to try
        candidates = [
            main_script_name,
            f'build/{main_script_name}',
            'example_analysis.py',
            'build/example_analysis.py',
            'test.py',
            'build/test.py'
        ]
        
        # Look for existing script
        for candidate in candidates:
            candidate_path = os.path.join(bundle_dir, candidate)
            if os.path.exists(candidate_path):
                logger.info(f"🎯 Found main script: {candidate}")
                return candidate_path
        
        # Create script from content if provided
        if script_content.strip():
            script_path = os.path.join(bundle_dir, "main_script.py")
            with open(script_path, 'w') as f:
                f.write(script_content)
            logger.info("📝 Created main script from content: main_script.py")
            return script_path
            
        return None
    
    def _get_script_from_build_yml(self, bundle_dir: str) -> str:
        """Parse build.yml to find script file name"""
        build_yml_path = os.path.join(bundle_dir, "build", "build.yml")
        default_script = "example_analysis.py"
        
        if not os.path.exists(build_yml_path):
            return default_script
            
        try:
            # Simple YAML parsing
            with open(build_yml_path, 'r') as f:
                content = f.read()
                for line in content.split('\n'):
                    if 'script_file:' in line:
                        script_name = line.split('script_file:')[1].strip().strip('"\'')
                        logger.info(f"📋 Found script from build.yml: {script_name}")
                        return script_name
        except Exception as e:
            logger.warning(f"Could not parse build.yml: {e}")
            
        return default_script
    
    def _execute_script(self, script_path: str, working_dir: str) -> Tuple[bool, str]:
        """Execute the script and return results"""
        # Setup environment
        env = os.environ.copy()
        env['PYTHONPATH'] = working_dir + ':' + env.get('PYTHONPATH', '')
        
        # Save current directory
        original_cwd = os.getcwd()
        os.chdir(working_dir)
        
        try:
            logger.info(f"🏃 Executing script: {script_path}")
            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=SCRIPT_EXECUTION_TIMEOUT,
                env=env,
                cwd=working_dir
            )
            
            logger.info(f"🏁 Script execution completed with return code: {result.returncode}")
            
            # Log output
            if result.stdout:
                logger.info(f"📤 STDOUT preview: {result.stdout[:200]}...")
            if result.stderr:
                logger.info(f"⚠️  STDERR preview: {result.stderr[:200]}...")
            
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
            error_msg = f'Script execution timed out ({SCRIPT_EXECUTION_TIMEOUT} seconds)'
            logger.error(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f'Script execution error: {str(e)}'
            logger.error(error_msg)
            return False, error_msg
        finally:
            os.chdir(original_cwd)