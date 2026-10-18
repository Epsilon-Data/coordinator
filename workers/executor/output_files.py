"""
Output files of a real-data run: verification of the enclave's files and their store.

The enclave returns `files` ([{name, size, sha256, type, content_b64}]) and
`files_manifest_sha256`, and binds the manifest hash into the attestation user_data.
The coordinator re-checks every file, rebuilds the manifest and requires its hash to
equal both, then stores the bytes under jobs/<job_id>/<sha256> before the job's
database transaction (content-addressed, so objects left by a failed run are harmless).
"""
import base64
import binascii
import hashlib
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3

from shared.output_manifest import FILE_NAME_PATTERN, build_manifest, file_type, manifest_sha256
from workers.executor.settings import OutputFilesConfig

REJECTED_PREFIX = "Output files rejected: "
STORE_NOT_CONFIGURED_MESSAGE = "Output file store is not configured"


class OutputFilesError(Exception):
    """The run's files cannot be held; the message is the job's held error text."""


class OutputFileIntegrityError(Exception):
    """Bytes read from the store do not have the recorded SHA-256."""


@dataclass(frozen=True)
class OutputFile:
    name: str
    size: int
    sha256: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class VerifiedOutputFiles:
    files: List[OutputFile]
    manifest_sha256: str


def _reject(reason: str) -> OutputFilesError:
    return OutputFilesError(REJECTED_PREFIX + reason)


def _verify_file(index: int, entry: Any) -> OutputFile:
    if not isinstance(entry, dict):
        raise _reject(f"file {index} is not an object")
    name = entry.get('name')
    if not isinstance(name, str) or not FILE_NAME_PATTERN.fullmatch(name):
        raise _reject(f"file {index} has an invalid name")
    content_type = file_type(name)
    if content_type is None:
        raise _reject(f"{name}: extension not allowed")
    if entry.get('type') != content_type:
        raise _reject(f"{name}: type does not match its extension")
    content_b64 = entry.get('content_b64')
    if not isinstance(content_b64, str):
        raise _reject(f"{name}: content missing")
    try:
        content = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise _reject(f"{name}: content is not valid base64") from None
    size = entry.get('size')
    if isinstance(size, bool) or not isinstance(size, int) or size != len(content):
        raise _reject(f"{name}: size does not match its content")
    sha256 = hashlib.sha256(content).hexdigest()
    if entry.get('sha256') != sha256:
        raise _reject(f"{name}: sha256 does not match its content")
    return OutputFile(name=name, size=size, sha256=sha256, content_type=content_type, content=content)


def verify_output_files(
    payload: Dict[str, Any],
    *,
    attestation_present: bool,
    attested_manifest_sha256: Optional[str],
    max_files: int,
    max_total_bytes: int,
) -> VerifiedOutputFiles:
    """Check the enclave's files and return them with their manifest hash, or raise OutputFilesError.

    payload holds the response's `files` and `files_manifest_sha256`. The rebuilt manifest
    hash must equal the response's and, when the run has an attestation, the one in its
    user_data (attested_manifest_sha256; None when the attestation does not carry it).
    """
    entries = payload.get('files')
    if not isinstance(entries, list):
        raise _reject("the enclave response has no file list")
    if len(entries) > max_files:
        raise _reject(f"{len(entries)} files, at most {max_files} are allowed")

    files: List[OutputFile] = []
    names = set()
    total = 0
    for index, entry in enumerate(entries):
        output_file = _verify_file(index, entry)
        if output_file.name in names:
            raise _reject(f"{output_file.name}: duplicate name")
        names.add(output_file.name)
        total += output_file.size
        if total > max_total_bytes:
            raise _reject(f"files exceed {max_total_bytes} bytes in total")
        files.append(output_file)

    digest = manifest_sha256(build_manifest(
        {'name': f.name, 'size': f.size, 'sha256': f.sha256, 'type': f.content_type} for f in files
    ))
    if payload.get('files_manifest_sha256') != digest:
        raise _reject("the files manifest hash does not match the enclave response")
    if attestation_present:
        if attested_manifest_sha256 is None:
            raise _reject("the attestation does not carry a files manifest hash")
        if attested_manifest_sha256 != digest:
            raise _reject("the files manifest hash does not match the attestation")
    return VerifiedOutputFiles(files=sorted(files, key=lambda f: f.name), manifest_sha256=digest)


def storage_key(job_id: str, sha256: str) -> str:
    return f"jobs/{job_id}/{sha256}"


class OutputFileStore(ABC):
    """Holds output file bytes by storage key."""

    @abstractmethod
    def put(self, key: str, body: bytes, content_type: str) -> None:
        pass

    @abstractmethod
    def get(self, key: str) -> bytes:
        pass

    def read_verified(self, key: str, sha256: str) -> bytes:
        """The stored bytes, refused unless their SHA-256 is the recorded one."""
        body = self.get(key)
        if hashlib.sha256(body).hexdigest() != sha256:
            raise OutputFileIntegrityError(f"{key}: stored bytes do not match their sha256")
        return body


class LocalOutputFileStore(OutputFileStore):
    """Files under a root directory: <root>/<storage_key>."""

    def __init__(self, root: str):
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root not in path.parents:
            raise ValueError(f"storage key outside the store: {key}")
        return path

    def put(self, key: str, body: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename, so a reader never sees a partial file
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(body)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()


class S3OutputFileStore(OutputFileStore):
    """Objects in an S3 bucket, SSE-KMS with kms_key_id, otherwise the bucket's default encryption."""

    def __init__(self, bucket: str, kms_key_id: str = '', region: Optional[str] = None, client: Any = None):
        self._bucket = bucket
        self._kms_key_id = kms_key_id
        self._client = client if client is not None else boto3.client('s3', region_name=region)

    def put(self, key: str, body: bytes, content_type: str) -> None:
        args = {'Bucket': self._bucket, 'Key': key, 'Body': body, 'ContentType': content_type}
        if self._kms_key_id:
            args['ServerSideEncryption'] = 'aws:kms'
            args['SSEKMSKeyId'] = self._kms_key_id
        self._client.put_object(**args)

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self._bucket, Key=key)['Body'].read()


def output_file_store(config: OutputFilesConfig) -> Optional[OutputFileStore]:
    """The configured store, or None when OUTPUT_FILES_STORE is unset."""
    if not config.store:
        return None
    if config.store == 's3':
        return S3OutputFileStore(config.bucket, config.kms_key_id, region=config.region)
    if config.store == 'local':
        return LocalOutputFileStore(config.directory)
    raise ValueError(f"OUTPUT_FILES_STORE must be s3 or local, not {config.store}")


def store_output_files(store: OutputFileStore, job_id: str, verified: VerifiedOutputFiles) -> List[Dict[str, Any]]:
    """Write each file's bytes and return the rows complete_job_with_held_output holds."""
    rows = []
    for f in verified.files:
        key = storage_key(job_id, f.sha256)
        store.put(key, f.content, f.content_type)
        rows.append({
            'name': f.name, 'size': f.size, 'content_type': f.content_type,
            'sha256': f.sha256, 'storage_key': key,
        })
    return rows
