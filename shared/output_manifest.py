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
from typing import Any, Dict, Iterable, List, Optional

# Allowed extensions (lower-cased, after the last dot) and the type each file is served as.
# Identical in every repo.
FILE_TYPES = {
    'csv': 'text/csv',
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'json': 'application/json',
    'txt': 'text/plain',
    'pdf': 'application/pdf',
}

FILE_NAME_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}')
SHA256_PATTERN = re.compile(r'[0-9a-f]{64}')


def file_type(name: str) -> Optional[str]:
    """The type a file of this name is served as, or None when its extension is not allowed."""
    if '.' not in name:
        return None
    return FILE_TYPES.get(name.rsplit('.', 1)[1].lower())


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
