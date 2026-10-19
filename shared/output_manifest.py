"""
Output files manifest: the contract shared with the enclave and ResearchWorkspace.

A run's files are described by a manifest, a list of {"name", "size", "sha256", "type"}
sorted by name. Its canonical bytes are json.dumps(manifest, sort_keys=True,
separators=(",", ":")) in UTF-8, and files_manifest_sha256 is the lower-case hex
SHA-256 of them (a run without files has the manifest []). The enclave binds that
hash into the attestation user_data.
"""
import hashlib
import json
import re
from typing import Any, Dict, Iterable, List

# The type a file is served as, by its lower-cased extension (after the last dot of the last
# path segment); any other extension, or none, is DEFAULT_FILE_TYPE. Identical in every repo.
FILE_TYPES = {
    'csv': 'text/csv',
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'json': 'application/json',
    'txt': 'text/plain',
    'pdf': 'application/pdf',
}

DEFAULT_FILE_TYPE = 'application/octet-stream'

# A name is the file's path relative to outputs/: 1 to 5 segments joined by '/', at most 255 characters
FILE_NAME_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}(/[A-Za-z0-9][A-Za-z0-9._-]{0,99}){0,4}')
MAX_FILE_NAME_LENGTH = 255
SHA256_PATTERN = re.compile(r'[0-9a-f]{64}')


def valid_file_name(name: Any) -> bool:
    return isinstance(name, str) and len(name) <= MAX_FILE_NAME_LENGTH and FILE_NAME_PATTERN.fullmatch(name) is not None


def file_type(name: str) -> str:
    """The type a file of this name is served as."""
    segment = name.rsplit('/', 1)[-1]
    if '.' not in segment:
        return DEFAULT_FILE_TYPE
    return FILE_TYPES.get(segment.rsplit('.', 1)[1].lower(), DEFAULT_FILE_TYPE)


def build_manifest(entries: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The manifest of files given as dicts with name, size, sha256 and type: those four keys, sorted by name."""
    manifest = [
        {'name': e['name'], 'size': e['size'], 'sha256': e['sha256'], 'type': e['type']}
        for e in entries
    ]
    return sorted(manifest, key=lambda e: e['name'])


def canonical_manifest_bytes(manifest: List[Dict[str, Any]]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def manifest_sha256(manifest: List[Dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
