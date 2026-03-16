"""
Services for executor worker: build validation, zipping, and cryptography.
"""
import os
import base64
import logging
import yaml
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Set, Tuple

from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from workers.executor.models import BuildConfig, DatasetConfig
from workers.executor.exceptions import BuildValidationError

logger = logging.getLogger(__name__)


# Encryption constants
AES_KEY_SIZE = 32  # 256 bits
IV_SIZE = 16  # 128 bits for AES-CBC
RSA_KEY_SIZE = 2048
RSA_PUBLIC_EXPONENT = 65537
AES_BLOCK_SIZE = 128  # bits


# =============================================================================
# Build Validator
# =============================================================================
class BuildValidator:
    """
    Validates build folder structure and parses build.yml configuration.

    Usage:
        validator = BuildValidator(repo_path="/path/to/repo")
        config = validator.validate()  # Returns BuildConfig or raises BuildValidationError
    """

    REQUIRED_FOLDERS = ["build", "build/generated"]
    REQUIRED_FILES = ["build/build.yml"]

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.build_path = self.repo_path / "build"
        self.build_yml_path = self.build_path / "build.yml"
        self.generated_path = self.build_path / "generated"

    def validate(self) -> BuildConfig:
        """Validate build folder and return parsed configuration."""
        self._validate_folder_structure()
        build_data = self._load_build_yml()
        return self._parse_build_config(build_data)

    def _validate_folder_structure(self) -> None:
        if not self.build_path.exists():
            raise BuildValidationError(
                f"Build folder not found at {self.build_path}. "
                "Please run 'epsilon build' command first.",
                details={"path": str(self.build_path)}
            )

        if not self.build_path.is_dir():
            raise BuildValidationError(
                f"'{self.build_path}' is not a directory",
                details={"path": str(self.build_path)}
            )

        if not self.build_yml_path.exists():
            raise BuildValidationError(
                f"build.yml not found at {self.build_yml_path}. "
                "Please run 'epsilon build' command first.",
                details={"path": str(self.build_yml_path)}
            )

        if not self.generated_path.exists():
            raise BuildValidationError(
                f"Generated folder not found at {self.generated_path}",
                details={"path": str(self.generated_path)}
            )

        logger.debug(f"Build folder structure validated: {self.build_path}")

    def _load_build_yml(self) -> dict:
        try:
            with open(self.build_yml_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                if not isinstance(data, dict):
                    raise BuildValidationError("build.yml must contain a YAML dictionary")
                return data
        except yaml.YAMLError as e:
            raise BuildValidationError(f"Invalid YAML format in build.yml: {e}", details={"error": str(e)})
        except IOError as e:
            raise BuildValidationError(f"Cannot read build.yml: {e}", details={"error": str(e)})

    def _parse_build_config(self, build_data: dict) -> BuildConfig:
        try:
            version = build_data.get('version', '1.0')
            analysis = build_data.get('analysis', {})
            script_file = analysis.get('script_file', 'main.py')
            datasets = self._parse_datasets(build_data.get('datasets', []))
            privacy = build_data.get('privacy', {})
            epsilon = float(privacy.get('epsilon', 1.0))
            execution = build_data.get('execution', {})
            timeout = int(execution.get('timeout', 300))
            environment = execution.get('environment', 'enclave')

            return BuildConfig(
                version=version,
                script_file=script_file,
                datasets=datasets,
                epsilon=epsilon,
                timeout=timeout,
                environment=environment
            )
        except (KeyError, ValueError, TypeError) as e:
            raise BuildValidationError(f"Invalid build.yml configuration: {e}", details={"error": str(e)})

    def _parse_datasets(self, datasets_data: list) -> list:
        datasets = []
        for idx, ds in enumerate(datasets_data):
            if not isinstance(ds, dict):
                raise BuildValidationError(f"Dataset at index {idx} must be a dictionary")

            dataset_id = ds.get('dataset_id', '')
            archetype_id = ds.get('archetype_id', '')

            if not dataset_id:
                raise BuildValidationError(f"Dataset at index {idx} missing 'dataset_id'", details={"index": idx})
            if not archetype_id:
                raise BuildValidationError(
                    f"Dataset at index {idx} missing 'archetype_id'",
                    details={"index": idx, "dataset_id": dataset_id}
                )

            datasets.append(DatasetConfig(
                dataset_id=dataset_id,
                archetype_id=archetype_id,
                import_path=ds.get('import_path', ''),
                function_name=ds.get('function_name', ''),
                archetype_path=ds.get('archetype_path', '')
            ))
        return datasets



# =============================================================================
# Zip Service
# =============================================================================
@dataclass
class ZipResult:
    """Result of zip operation."""
    data: bytes
    files_count: int
    original_size: int
    compressed_size: int

    @property
    def compression_ratio(self) -> float:
        if self.original_size == 0:
            return 0.0
        return (self.compressed_size / self.original_size) * 100


class ZipService:
    """Service for creating ZIP archives of directories."""

    MAX_BUILD_SIZE = 100 * 1024 * 1024  # 100 MB

    DEFAULT_EXCLUDES: Set[str] = {
        '__pycache__', '.git', '.DS_Store', '*.pyc', '*.pyo', '.env', 'node_modules',
    }

    def __init__(self, exclude_patterns: Set[str] = None):
        self.exclude_patterns = exclude_patterns or self.DEFAULT_EXCLUDES

    def zip_directory(self, directory_path: str) -> ZipResult:
        """Create a ZIP archive of a directory in memory (no temp file I/O)."""
        dir_path = Path(directory_path)

        if not dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory_path}")

        if not dir_path.is_dir():
            raise ValueError(f"Path is not a directory: {directory_path}")

        logger.info(f"Creating ZIP archive of {dir_path}")

        files_added = 0
        total_size = 0

        # Use in-memory buffer instead of temp file
        buffer = BytesIO()

        resolved_root = dir_path.resolve()

        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in dir_path.rglob('*'):
                if file_path.is_file() and not self._should_exclude(file_path):
                    # Prevent symlink escape: resolved path must stay inside root
                    if not file_path.resolve().is_relative_to(resolved_root):
                        logger.warning(f"Skipping file outside build dir: {file_path}")
                        continue
                    try:
                        arcname = file_path.relative_to(dir_path)
                        file_size = file_path.stat().st_size
                        zipf.write(file_path, arcname)
                        files_added += 1
                        total_size += file_size
                        logger.debug(f"Added: {arcname} ({file_size} bytes)")
                        # Check accumulated zip size against limit
                        if buffer.tell() > self.MAX_BUILD_SIZE:
                            raise ValueError(
                                f"Build archive exceeds maximum size of "
                                f"{self.MAX_BUILD_SIZE // (1024 * 1024)} MB"
                            )
                    except ValueError:
                        raise
                    except Exception as e:
                        logger.warning(f"Failed to add {file_path}: {e}")

        zip_data = buffer.getvalue()

        result = ZipResult(
            data=zip_data,
            files_count=files_added,
            original_size=total_size,
            compressed_size=len(zip_data)
        )

        logger.info(
            f"ZIP created: {files_added} files, "
            f"{total_size} bytes -> {len(zip_data)} bytes "
            f"({result.compression_ratio:.1f}%)"
        )

        return result

    def _should_exclude(self, file_path: Path) -> bool:
        for part in file_path.parts:
            if part in self.exclude_patterns:
                return True
        for pattern in self.exclude_patterns:
            if pattern.startswith('*') and file_path.name.endswith(pattern[1:]):
                return True
            if file_path.name == pattern:
                return True
        return False


# =============================================================================
# Crypto Service
# =============================================================================
class CryptoService:
    """
    Hybrid encryption service using AES-256-CBC for data and RSA-OAEP for key exchange.
    """

    def generate_keypair(self) -> Tuple[RSAPrivateKey, str]:
        """Generate RSA keypair for encryption."""
        private_key = rsa.generate_private_key(
            public_exponent=RSA_PUBLIC_EXPONENT,
            key_size=RSA_KEY_SIZE
        )

        public_key_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode('utf-8')

        return private_key, public_key_pem

    def encrypt(self, data: bytes, public_key_pem: str) -> str:
        """Encrypt data using hybrid encryption (AES-256-CBC + RSA-OAEP)."""
        rsa_public_key = serialization.load_pem_public_key(public_key_pem.encode())

        aes_key = os.urandom(AES_KEY_SIZE)
        iv = os.urandom(IV_SIZE)

        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
        encryptor = cipher.encryptor()

        padder = sym_padding.PKCS7(AES_BLOCK_SIZE).padder()
        padded_data = padder.update(data) + padder.finalize()

        ciphertext = encryptor.update(padded_data) + encryptor.finalize()

        encrypted_key = rsa_public_key.encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )

        combined = encrypted_key + iv + ciphertext

        logger.debug(
            f"Encrypted {len(data)} bytes -> {len(combined)} bytes "
            f"(key: {len(encrypted_key)}, iv: {IV_SIZE}, data: {len(ciphertext)})"
        )

        return base64.b64encode(combined).decode('utf-8')

    def decrypt(self, encrypted_data: str, private_key: RSAPrivateKey) -> bytes:
        """Decrypt data using hybrid decryption (RSA-OAEP + AES-256-CBC)."""
        combined = base64.b64decode(encrypted_data)

        rsa_key_bytes = private_key.key_size // 8
        encrypted_key = combined[:rsa_key_bytes]
        iv = combined[rsa_key_bytes:rsa_key_bytes + IV_SIZE]
        ciphertext = combined[rsa_key_bytes + IV_SIZE:]

        aes_key = private_key.decrypt(
            encrypted_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )

        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded_data = decryptor.update(ciphertext) + decryptor.finalize()

        unpadder = sym_padding.PKCS7(AES_BLOCK_SIZE).unpadder()
        data = unpadder.update(padded_data) + unpadder.finalize()

        logger.debug(f"Decrypted {len(combined)} bytes -> {len(data)} bytes")

        return data
