"""
Dataset Manager - Handles dataset preparation for secure execution
"""
import json
import yaml
import zipfile
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any

import boto3
from cryptography.fernet import Fernet
import base64

from config import Settings
from models.execution_models import JobExecutionRequest
from utils import get_logger

logger = get_logger(__name__)


class DatasetManager:
    """Manages dataset preparation for secure execution"""
    
    def __init__(self, settings: Settings):
        """
        Initialize Dataset Manager
        
        Args:
            settings: Application settings
        """
        self._settings = settings
        logger.info("DatasetManager initialized")
    
    def prepare_execution_data(self, request: JobExecutionRequest) -> str:
        """
        Prepare execution data for the job - create encrypted zip bundle
        
        Args:
            request: Job execution request
            
        Returns:
            JSON string containing encrypted bundle metadata
        """
        try:
            logger.error("NEW DATASET MANAGER CODE IS RUNNING!!!")
            logger.error(f"STEP 1: STARTING BUNDLE PREPARATION for job {request.job_id}")
            logger.error(f"Job Details: repo_path={request.repo_path}, script_path={request.script_path}")
            logger.error("=" * 80)
            
            # Step 1: Load repo from shared storage
            repo_path = Path(request.repo_path)
            if not repo_path.exists():
                logger.error(f" STEP 1 FAILED: Repository not found: {repo_path}")
                raise FileNotFoundError(f"Repository not found: {repo_path}")
            
            logger.error(f" STEP 1 SUCCESS: Found repository at: {repo_path}")
            logger.error(f" Repository contents: {list(repo_path.iterdir())}")
            
            # Step 2: Find dataset info from build.yml
            logger.error(" STEP 2: CHECKING BUILD CONFIG")
            dataset_info = self._get_dataset_info_from_build_yml(repo_path)
            logger.error(f" STEP 2 SUCCESS: Dataset info: {dataset_info}")
            
            # Step 3: Replace and encrypt CSV files in archetypes
            logger.error(" STEP 3: PROCESSING CSV FILES")
            self._replace_and_encrypt_csv_files(repo_path, dataset_info)
            logger.error(" STEP 3 SUCCESS: CSV files processed")
            
            # Step 4: Create zip bundle of entire repo
            logger.error(" STEP 4: CREATING ENCRYPTED BUNDLE")
            encrypted_bundle_data = self._create_and_encrypt_bundle(repo_path, request.job_id)
            logger.error(" STEP 4 SUCCESS: Encrypted bundle created")
            
            logger.error("=" * 80)
            logger.error(f" BUNDLE PREPARATION COMPLETE for job {request.job_id}")
            logger.error(f" Bundle size: {len(encrypted_bundle_data)} chars")
            logger.error("=" * 80)
            return encrypted_bundle_data
            
        except Exception as e:
            logger.error(f"Failed to prepare execution data: {e}")
            raise
    
    def _get_dataset_info_from_build_yml(self, repo_path: Path) -> Dict[str, Any]:
        """Extract dataset information from build.yml"""
        build_yml_path = repo_path / "build" / "build.yml"
        
        logger.error(f" Looking for build.yml at: {build_yml_path}")
        
        if not build_yml_path.exists():
            logger.error(f"  build.yml not found at {build_yml_path}")
            logger.error(f" Build directory contents: {list((repo_path / 'build').iterdir()) if (repo_path / 'build').exists() else 'Build dir does not exist'}")
            default_config = {"script_file": "example_analysis.py"}
            logger.error(f" Using default config: {default_config}")
            return default_config
        
        try:
            with open(build_yml_path, 'r') as f:
                build_config = yaml.safe_load(f)
                logger.error(f" Successfully loaded build config: {build_config}")
                logger.error(f" Script file: {build_config.get('analysis', {}).get('script_file', 'example_analysis.py')}")
                logger.error(f"  Dataset ID: {build_config.get('dataset', {}).get('id', 'Not specified')}")
                return build_config
        except Exception as e:
            logger.error(f" Failed to parse build.yml: {e}")
            default_config = {"script_file": "example_analysis.py"}
            logger.error(f" Using default config after error: {default_config}")
            return default_config
    
    def _replace_and_encrypt_csv_files(self, repo_path: Path, dataset_info: Dict[str, Any]) -> None:
        """Replace CSV files in archetypes with real data and encrypt them"""
        archetypes_path = repo_path / "archetypes"
        
        if not archetypes_path.exists():
            logger.error(f" Archetypes directory not found: {archetypes_path}")
            logger.error(f" Repository contents: {list(repo_path.iterdir())}")
            logger.error("  No CSV files to encrypt - archetypes directory missing")
            return
        
        # Get dataset_id from build config if available
        datasets = dataset_info.get('datasets', [])
        dataset_id = None
        
        if datasets and len(datasets) > 0:
            dataset_id = datasets[0].get('dataset_id')
            logger.error(f" Found dataset_id from datasets array: {dataset_id}")
        else:
            # Fallback to old format
            dataset_id = dataset_info.get('dataset', {}).get('id') or dataset_info.get('dataset_id')
            logger.error(f"Using fallback dataset_id: {dataset_id}")
        
        if dataset_id:
            # Process specific dataset directory
            dataset_dir = archetypes_path / dataset_id
            logger.error(f"Checking for dataset directory: {dataset_dir}")
            logger.error(f"Dataset directory exists: {dataset_dir.exists()}")
            
            if dataset_dir.exists():
                logger.info(f"  Processing specific dataset: {dataset_id}")
                self._process_dataset_directory(dataset_dir, dataset_id)
            else:
                logger.error(f"  Dataset directory not found: {dataset_dir}")
                logger.error(f" Available directories in archetypes: {list(archetypes_path.iterdir()) if archetypes_path.exists() else 'N/A'}")
        else:
            # Process all dataset directories if no specific dataset_id
            logger.info("  No dataset_id found, processing all archetype directories")
            for dataset_dir in archetypes_path.iterdir():
                if dataset_dir.is_dir():
                    logger.info(f"  Processing dataset: {dataset_dir.name}")
                    self._process_dataset_directory(dataset_dir, dataset_dir.name)
    
    def _process_dataset_directory(self, dataset_dir: Path, dataset_id: str) -> None:
        """Process a single dataset directory"""
        # Find CSV files in this dataset
        logger.error(f" Scanning directory: {dataset_dir}")
        logger.error(f" Directory contents: {list(dataset_dir.iterdir()) if dataset_dir.exists() else 'Directory does not exist'}")
        
        # Find CSV files, excluding already encrypted ones
        all_csv_files = list(dataset_dir.glob("*.csv"))
        csv_files = [f for f in all_csv_files if not f.name.endswith('.csv.encrypted')]
        logger.error(f" Found {len(csv_files)} CSV files: {[f.name for f in csv_files]}")
        
        # Check if there are already encrypted CSV files that need to be included in bundle
        encrypted_csv_files = list(dataset_dir.glob("*.csv.encrypted"))
        if encrypted_csv_files:
            logger.error(f" Found {len(encrypted_csv_files)} already encrypted CSV files: {[f.name for f in encrypted_csv_files]}")
            logger.info(" Including already encrypted CSV files in bundle")
        
        for csv_file in csv_files:
            logger.info(f" Processing CSV: {csv_file.name} in dataset {dataset_id}")
            
            # Step 4: Replace with real dataset (for now, use existing CSV content)
            original_filename = csv_file.name
            real_dataset_content = self._get_real_dataset_content(csv_file, dataset_id)
            
            # Step 5: Encrypt the real dataset with same filename
            self._encrypt_csv_file_with_content(csv_file, real_dataset_content, original_filename)
    
    def _get_real_dataset_content(self, original_csv: Path, dataset_id: str) -> str:
        """Get real dataset content to replace the archetype CSV"""
        try:
            # For now, read the existing CSV as the "real" dataset
            # TODO: Replace with actual dataset loading logic from S3 or database
            with open(original_csv, 'r') as f:
                content = f.read()
            
            logger.info(f" Using existing CSV as real dataset for {dataset_id} ({len(content)} chars)")
            return content
            
        except Exception as e:
            logger.error(f"Failed to get real dataset for {dataset_id}: {e}")
            # Return dummy CSV content
            return "id,name,value\n1,sample,100\n"
    
    def _encrypt_csv_file_with_content(self, csv_file: Path, content: str, original_filename: str) -> None:
        """Encrypt CSV file content using KMS and preserve original filename"""
        try:
            logger.info(f" Encrypting CSV: {original_filename} ({len(content)} chars)")
            
            # Use environment variables for AWS credentials (already set in Docker)
            kms_client = boto3.client('kms', region_name=self._settings.aws.region)
            
            # Generate data key for envelope encryption
            response = kms_client.generate_data_key(
                KeyId=self._settings.aws.kms_key_arn,
                KeySpec='AES_256'
            )
            
            plaintext_key = response['Plaintext']
            encrypted_data_key = response['CiphertextBlob']
            
            # Encrypt CSV content with data key
            fernet = Fernet(base64.urlsafe_b64encode(plaintext_key[:32]))
            encrypted_content = fernet.encrypt(content.encode('utf-8'))
            
            # Store encrypted data as binary file (no Base64 bloat)
            encrypted_csv_path = csv_file.with_suffix('.csv.encrypted')
            with open(encrypted_csv_path, 'wb') as f:
                # Write header with key length, then encrypted key, then encrypted data
                f.write(len(encrypted_data_key).to_bytes(4, 'big'))
                f.write(encrypted_data_key)
                f.write(encrypted_content)
            
            # Create small metadata file
            metadata_path = csv_file.with_suffix('.csv.meta')
            metadata = {
                'method': 'envelope',
                'original_filename': original_filename,
                'encrypted_size': len(encrypted_content),
                'original_size': len(content)
            }
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f)
            
            # Remove original CSV file
            csv_file.unlink()
            
            logger.info(f" Encrypted CSV: {original_filename} -> {encrypted_csv_path.name} (reduced from ~{len(content)*2} to {len(encrypted_content)} bytes)")
            
        except Exception as e:
            logger.error(f"Failed to encrypt CSV {csv_file}: {e}")
            raise
    
    def _create_and_encrypt_bundle(self, repo_path: Path, job_id: str) -> str:
        """Create zip bundle of repo and encrypt it"""
        try:
            # Create output directory for encrypted bundle
            bundle_dir = Path(self._settings.storage.shared_storage_path) / "enclave_execution_results" / job_id
            bundle_dir.mkdir(parents=True, exist_ok=True)
            
            # Create temporary zip file
            temp_zip = tempfile.mktemp(suffix='.zip')
            
            logger.error(f" Creating zip bundle: {temp_zip}")
            
            # Zip the entire repository
            with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in repo_path.rglob('*'):
                    if file_path.is_file():
                        # Calculate relative path from repo root
                        relative_path = file_path.relative_to(repo_path)
                        zipf.write(file_path, relative_path)
                        
            logger.error(f" Created zip bundle with repo contents")
            
            # Read zip file as bytes
            with open(temp_zip, 'rb') as f:
                zip_bytes = f.read()
            
            # Clean up temp file
            Path(temp_zip).unlink()
            
            # Encrypt the zip bundle and save to file
            encrypted_bundle_path = bundle_dir / "encrypted_bundle.zip"
            encrypted_bundle_metadata = self._encrypt_and_save_zip_bundle(zip_bytes, encrypted_bundle_path)
            
            logger.error(f" Encrypted zip bundle saved to: {encrypted_bundle_path}")
            
            # Return the file path instead of metadata
            return str(encrypted_bundle_path)
            
        except Exception as e:
            logger.error(f"Failed to create bundle: {e}")
            raise
    
    def _encrypt_and_save_zip_bundle(self, zip_bytes: bytes, output_path: Path) -> Dict[str, Any]:
        """Encrypt zip bundle using KMS envelope encryption and save to file"""
        try:
            # Use environment variables for AWS credentials (already set in Docker)
            kms_client = boto3.client('kms', region_name=self._settings.aws.region)
            
            # Generate data key for envelope encryption
            response = kms_client.generate_data_key(
                KeyId=self._settings.aws.kms_key_arn,
                KeySpec='AES_256'
            )
            
            plaintext_key = response['Plaintext']
            encrypted_data_key = response['CiphertextBlob']
            
            # Encrypt zip data with data key
            fernet = Fernet(base64.urlsafe_b64encode(plaintext_key[:32]))
            encrypted_zip = fernet.encrypt(zip_bytes)
            
            # Create encryption metadata
            bundle_metadata = {
                'method': 'envelope',
                'encrypted_data_key': base64.b64encode(encrypted_data_key).decode('utf-8'),
                'encrypted_data': base64.b64encode(encrypted_zip).decode('utf-8')
            }
            
            # Save encrypted bundle metadata to file
            with open(output_path, 'w') as f:
                json.dump(bundle_metadata, f)
            
            logger.error(f" Saved encrypted bundle to: {output_path}")
            return bundle_metadata
            
        except Exception as e:
            logger.error(f"Failed to encrypt zip bundle: {e}")
            raise
            
    def _encrypt_zip_bundle(self, zip_bytes: bytes) -> str:
        """Encrypt zip bundle using KMS envelope encryption (legacy method)"""
        try:
            # Use environment variables for AWS credentials (already set in Docker)
            kms_client = boto3.client('kms', region_name=self._settings.aws.region)
            
            # Generate data key for envelope encryption
            response = kms_client.generate_data_key(
                KeyId=self._settings.aws.kms_key_arn,
                KeySpec='AES_256'
            )
            
            plaintext_key = response['Plaintext']
            encrypted_data_key = response['CiphertextBlob']
            
            # Encrypt zip data with data key
            fernet = Fernet(base64.urlsafe_b64encode(plaintext_key[:32]))
            encrypted_zip = fernet.encrypt(zip_bytes)
            
            # Create encryption metadata
            bundle_metadata = {
                'method': 'envelope',
                'encrypted_data_key': base64.b64encode(encrypted_data_key).decode('utf-8'),
                'encrypted_data': base64.b64encode(encrypted_zip).decode('utf-8')
            }
            
            return json.dumps(bundle_metadata)
            
        except Exception as e:
            logger.error(f"Failed to encrypt zip bundle: {e}")
            raise