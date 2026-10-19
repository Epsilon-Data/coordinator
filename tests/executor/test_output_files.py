"""Output files: the manifest contract, verification of the enclave's files and the stores (unit, no database)."""
import base64
import hashlib
import io
import os
from unittest.mock import MagicMock

import pytest

from shared.output_manifest import (
    FILE_TYPES,
    build_manifest,
    canonical_manifest_bytes,
    file_type,
    manifest_sha256,
    valid_file_name,
)
from workers.executor.output_files import (
    LocalOutputFileStore,
    OutputFileIntegrityError,
    OutputFilesError,
    S3OutputFileStore,
    collect_output_files,
    output_file_store,
    storage_key,
    store_output_files,
    verify_output_files,
)
from workers.executor.settings import OutputFilesConfig, Settings, validate_environment

JOB_ID = 'JOB-FILES-1'
NOTES = b'hello\n'
SUMMARY = b'a,b\n1,2\n'

# Known-answer vector: the canonical manifest of notes.txt and summary.csv, and its SHA-256
KAT_CANONICAL = (
    '[{"name":"notes.txt","sha256":"5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03",'
    '"size":6,"type":"text/plain"},'
    '{"name":"summary.csv","sha256":"492d5ea496056f1a6a6592241032fab764c321596317930b4fa0e1e8bc3b7470",'
    '"size":8,"type":"text/csv"}]'
)
KAT_MANIFEST_SHA256 = 'd08591bd256caf6b90ca0e00be715ae8ea97014bc8d71f8fcfc31e9ed6ea9140'
# Known-answer vector with a subfolder and files of unknown type: a.csv, plots/fig.bin and notes
PATHS_FILES = {'a.csv': b'x,y\n1,2\n', 'plots/fig.bin': b'\x00\x01\x02', 'notes': b'hi\n'}
PATHS_MANIFEST_SHA256 = '49f15dcc85c70e4712ab20edd5e118eb3ea2526e264da93628522a3a6c456907'
EMPTY_MANIFEST_SHA256 = '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'  # sha256 of "[]"
LIMITS = {'max_files': 20, 'max_total_bytes': 10 * 1024 * 1024}


def _entry(name, content, **overrides):
    entry = {
        'name': name, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
        'type': file_type(name), 'content_b64': base64.b64encode(content).decode(),
    }
    entry.update(overrides)
    return entry


def _payload(entries, manifest_hash=None):
    if manifest_hash is None:
        manifest_hash = manifest_sha256(build_manifest(entries))
    return {'files': entries, 'files_manifest_sha256': manifest_hash}


def _verify(payload, attested='same', attestation_present=True, **limits):
    if attested == 'same':
        attested = payload.get('files_manifest_sha256')
    return verify_output_files(
        payload, attestation_present=attestation_present, attested_manifest_sha256=attested,
        **{**LIMITS, **limits},
    )


class TestManifest:
    def test_known_answer_vector(self):
        manifest = build_manifest([
            {'name': 'summary.csv', 'size': 8, 'sha256': hashlib.sha256(SUMMARY).hexdigest(), 'type': 'text/csv'},
            {'name': 'notes.txt', 'size': 6, 'sha256': hashlib.sha256(NOTES).hexdigest(), 'type': 'text/plain'},
        ])
        assert canonical_manifest_bytes(manifest) == KAT_CANONICAL.encode()
        assert manifest_sha256(manifest) == KAT_MANIFEST_SHA256

    def test_known_answer_vector_with_paths_and_unknown_types(self):
        manifest = build_manifest(
            {'name': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'type': file_type(name)}
            for name, data in PATHS_FILES.items()
        )
        assert [(e['name'], e['type']) for e in manifest] == [
            ('a.csv', 'text/csv'), ('notes', 'application/octet-stream'), ('plots/fig.bin', 'application/octet-stream'),
        ]
        assert manifest_sha256(manifest) == PATHS_MANIFEST_SHA256

    def test_no_files_is_the_hash_of_an_empty_list(self):
        assert canonical_manifest_bytes([]) == b'[]'
        assert manifest_sha256([]) == EMPTY_MANIFEST_SHA256

    def test_type_table(self):
        assert FILE_TYPES == {
            'csv': 'text/csv', 'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'json': 'application/json', 'txt': 'text/plain', 'pdf': 'application/pdf',
        }
        assert file_type('Plot.PNG') == 'image/png'
        assert file_type('archive.tar.csv') == 'text/csv'
        assert file_type('plots/Fig.JPEG') == 'image/jpeg'
        assert file_type('script.py') == 'application/octet-stream'
        assert file_type('noext') == 'application/octet-stream'
        assert file_type('data.csv/raw') == 'application/octet-stream'
        assert file_type('trailing.') == 'application/octet-stream'

    @pytest.mark.parametrize('name', [
        'a', 'notes.txt', 'plots/fig1.png', 'a/b/c/d/e.bin', 'x' * 100, '/'.join(['y' * 50] * 5),
    ])
    def test_valid_names(self, name):
        assert valid_file_name(name)

    @pytest.mark.parametrize('name', [
        '', '.hidden', 'a/.hidden', '../x', 'a/../b', './a', 'a/./b', '/a', 'a/', 'a//b', 'a b', 'x' * 101,
        'a/b/c/d/e/f', '/'.join(['y' * 52] * 5), 'a\\b', 'x.csv\n', 'caf\u00e9.csv', 5, None,
    ])
    def test_invalid_names(self, name):
        assert not valid_file_name(name)


class TestVerifyOutputFiles:
    def test_good_files_are_returned_sorted_with_the_manifest_hash(self):
        payload = _payload([_entry('summary.csv', SUMMARY), _entry('notes.txt', NOTES)])

        verified = _verify(payload)

        assert verified.manifest_sha256 == KAT_MANIFEST_SHA256
        assert [(f.name, f.content, f.content_type) for f in verified.files] == [
            ('notes.txt', NOTES, 'text/plain'), ('summary.csv', SUMMARY, 'text/csv'),
        ]

    def test_an_empty_file_list_has_the_empty_manifest(self):
        verified = _verify(_payload([]))
        assert verified.files == []
        assert verified.manifest_sha256 == EMPTY_MANIFEST_SHA256

    def test_tampered_bytes_are_rejected(self):
        entry = _entry('notes.txt', NOTES)
        entry['content_b64'] = base64.b64encode(b'hellO\n').decode()
        with pytest.raises(OutputFilesError, match='^Output files rejected: notes.txt: sha256'):
            _verify(_payload([entry]))

    def test_a_wrong_size_is_rejected(self):
        with pytest.raises(OutputFilesError, match='size does not match'):
            _verify(_payload([_entry('notes.txt', NOTES, size=7)]))

    def test_a_manifest_hash_differing_from_the_response_is_rejected(self):
        payload = _payload([_entry('notes.txt', NOTES)], manifest_hash='0' * 64)
        with pytest.raises(OutputFilesError, match='does not match the enclave response'):
            _verify(payload, attested=manifest_sha256(build_manifest(payload['files'])))

    def test_a_missing_response_manifest_hash_is_rejected(self):
        payload = {'files': [_entry('notes.txt', NOTES)]}
        with pytest.raises(OutputFilesError, match='does not match the enclave response'):
            _verify(payload, attested=None)

    def test_a_manifest_hash_differing_from_the_attestation_is_rejected(self):
        with pytest.raises(OutputFilesError, match='does not match the attestation'):
            _verify(_payload([_entry('notes.txt', NOTES)]), attested='1' * 64)

    def test_an_attestation_without_the_manifest_hash_is_rejected(self):
        with pytest.raises(OutputFilesError, match='attestation does not carry'):
            _verify(_payload([_entry('notes.txt', NOTES)]), attested=None)

    def test_without_an_attestation_only_the_response_hash_is_checked(self):
        verified = _verify(_payload([_entry('notes.txt', NOTES)]), attested=None, attestation_present=False)
        assert [f.name for f in verified.files] == ['notes.txt']

    @pytest.mark.parametrize('name', [
        '', '.hidden.csv', '../x.csv', 'a/../b.csv', 'a//b.csv', '/a.csv', 'a/', 'a b.csv', 'x' * 97 + '.csv',
        'a/b/c/d/e/f.csv', '/'.join(['y' * 52] * 5), 'x.csv\n', 5,
    ])
    def test_invalid_names_are_rejected(self, name):
        with pytest.raises(OutputFilesError, match='invalid name'):
            _verify(_payload([dict(_entry('notes.txt', NOTES), name=name)]))

    def test_subfolders_and_unknown_types_are_accepted(self):
        payload = _payload([_entry(name, data) for name, data in PATHS_FILES.items()])

        verified = _verify(payload)

        assert verified.manifest_sha256 == PATHS_MANIFEST_SHA256
        assert [(f.name, f.content_type, f.content) for f in verified.files] == [
            ('a.csv', 'text/csv', PATHS_FILES['a.csv']),
            ('notes', 'application/octet-stream', PATHS_FILES['notes']),
            ('plots/fig.bin', 'application/octet-stream', PATHS_FILES['plots/fig.bin']),
        ]

    @pytest.mark.parametrize('name', ['run.py', 'data.CSV.exe', 'README', 'bin/tool'])
    def test_any_extension_is_served_as_octet_stream(self, name):
        (f,) = _verify(_payload([_entry(name, NOTES)])).files
        assert f.content_type == 'application/octet-stream'

    @pytest.mark.parametrize('name, claimed', [
        ('notes.txt', 'text/html'), ('run.py', 'text/x-python'), ('plots/fig.bin', 'image/png'), ('chart.png', None),
    ])
    def test_a_type_not_matching_the_table_is_rejected(self, name, claimed):
        with pytest.raises(OutputFilesError, match='type does not match'):
            _verify(_payload([_entry(name, NOTES, type=claimed)]))

    def test_duplicate_names_are_rejected(self):
        with pytest.raises(OutputFilesError, match='duplicate name'):
            _verify(_payload([_entry('notes.txt', NOTES), _entry('notes.txt', SUMMARY)]))

    def test_invalid_base64_is_rejected(self):
        with pytest.raises(OutputFilesError, match='not valid base64'):
            _verify(_payload([_entry('notes.txt', NOTES, content_b64='not base64!')]))

    def test_too_many_files_are_rejected(self):
        entries = [_entry(f'f{i}.txt', NOTES) for i in range(3)]
        with pytest.raises(OutputFilesError, match='3 files, at most 2'):
            _verify(_payload(entries), max_files=2)

    def test_too_many_bytes_are_rejected(self):
        entries = [_entry('a.txt', b'x' * 6), _entry('b.txt', b'y' * 5)]
        with pytest.raises(OutputFilesError, match='exceed 10 bytes'):
            _verify(_payload(entries), max_total_bytes=10)

    @pytest.mark.parametrize('payload', [{}, {'files': None}, {'files': 'x'}, {'files': ['x']}])
    def test_malformed_file_lists_are_rejected(self, payload):
        with pytest.raises(OutputFilesError, match='^Output files rejected: '):
            _verify(payload, attested=None)


class TestCollectOutputFiles:
    """The local enclave's collection of the files a script wrote, under the enclave's rules."""

    def _collect(self, path, **limits):
        return collect_output_files(str(path), **{**LIMITS, **limits})

    def test_files_come_back_as_an_enclave_response_carries_them(self, tmp_path):
        (tmp_path / 'summary.csv').write_bytes(SUMMARY)
        (tmp_path / 'notes.txt').write_bytes(NOTES)

        payload = self._collect(tmp_path)

        assert payload == _payload([_entry('notes.txt', NOTES), _entry('summary.csv', SUMMARY)])
        assert payload['files_manifest_sha256'] == KAT_MANIFEST_SHA256

    @pytest.mark.parametrize('make', [lambda p: None, lambda p: p.mkdir()], ids=['missing', 'empty'])
    def test_a_missing_or_empty_directory_has_no_files(self, tmp_path, make):
        make(tmp_path / 'outputs')
        assert self._collect(tmp_path / 'outputs') == {'files': [], 'files_manifest_sha256': EMPTY_MANIFEST_SHA256}

    def test_outputs_must_be_a_directory(self, tmp_path):
        (tmp_path / 'outputs').write_bytes(b'x')
        with pytest.raises(OutputFilesError, match='^Output files rejected: outputs is not a directory'):
            self._collect(tmp_path / 'outputs')

    def test_subfolders_are_walked_and_empty_ones_ignored(self, tmp_path):
        for name, data in PATHS_FILES.items():
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_bytes(data)
        (tmp_path / 'empty' / 'deeper').mkdir(parents=True)
        (tmp_path / 'a' / 'b' / 'c' / 'd' / 'e' / 'f').mkdir(parents=True)

        payload = self._collect(tmp_path)

        assert payload == _payload([_entry(name, PATHS_FILES[name]) for name in sorted(PATHS_FILES)])
        assert payload['files_manifest_sha256'] == PATHS_MANIFEST_SHA256

    def test_the_deepest_allowed_path_is_collected(self, tmp_path):
        (tmp_path / 'a' / 'b' / 'c' / 'd').mkdir(parents=True)
        (tmp_path / 'a' / 'b' / 'c' / 'd' / 'e.bin').write_bytes(b'x')
        assert [f['name'] for f in self._collect(tmp_path)['files']] == ['a/b/c/d/e.bin']

    @pytest.mark.parametrize('make, reason', [
        (lambda p: os.symlink(p / 'notes.txt', p / 'link.txt'), "symlink not allowed: 'link.txt'"),
        (lambda p: os.symlink(p, p / 'loop'), "symlink not allowed: 'loop'"),
        (lambda p: ((p / 'sub').mkdir(), os.symlink(p / 'notes.txt', p / 'sub' / 'link')),
         "symlink not allowed: 'sub/link'"),
        (lambda p: ((p / 'sub').mkdir(), os.symlink('/etc', p / 'sub' / 'etc')), "symlink not allowed: 'sub/etc'"),
        (lambda p: os.mkfifo(p / 'pipe.txt'), 'not a regular file'),
        (lambda p: ((p / 'sub').mkdir(), os.mkfifo(p / 'sub' / 'pipe')), "not a regular file: 'sub/pipe'"),
        (lambda p: (p / '.hidden.txt').write_bytes(b'x'), 'invalid name'),
        (lambda p: ((p / '.git').mkdir(), (p / '.git' / 'HEAD').write_bytes(b'x')), 'invalid name'),
        (lambda p: ((p / 'a b').mkdir(), (p / 'a b' / 'x.csv').write_bytes(b'x')), 'invalid name'),
        (lambda p: ((p / 'a/b/c/d/e').mkdir(parents=True), (p / 'a/b/c/d/e/f.csv').write_bytes(b'x')),
         'invalid name'),
    ])
    def test_entries_breaking_the_rules_are_rejected(self, tmp_path, make, reason):
        (tmp_path / 'notes.txt').write_bytes(NOTES)
        make(tmp_path)
        with pytest.raises(OutputFilesError, match=f'^Output files rejected: .*{reason}'):
            self._collect(tmp_path)

    def test_any_file_type_is_collected(self, tmp_path):
        (tmp_path / 'run.sh').write_bytes(b'#!/bin/sh\n')
        (tmp_path / 'model.pkl').write_bytes(b'\x80\x04')
        assert [(f['name'], f['type']) for f in self._collect(tmp_path)['files']] == [
            ('model.pkl', 'application/octet-stream'), ('run.sh', 'application/octet-stream'),
        ]

    def test_limits_are_enforced(self, tmp_path):
        (tmp_path / 'a.txt').write_bytes(b'x' * 6)
        (tmp_path / 'sub').mkdir()
        (tmp_path / 'sub' / 'b.bin').write_bytes(b'y' * 5)
        with pytest.raises(OutputFilesError, match='2 files, at most 1'):
            self._collect(tmp_path, max_files=1)
        with pytest.raises(OutputFilesError, match='exceed 10 bytes'):
            self._collect(tmp_path, max_total_bytes=10)


class TestLocalStore:
    def test_round_trip_and_rows(self, tmp_path):
        store = LocalOutputFileStore(str(tmp_path))
        verified = _verify(_payload([_entry('summary.csv', SUMMARY), _entry('notes.txt', NOTES)]))

        rows = store_output_files(store, JOB_ID, verified)

        notes_sha = hashlib.sha256(NOTES).hexdigest()
        assert rows[0] == {
            'name': 'notes.txt', 'size': 6, 'content_type': 'text/plain', 'sha256': notes_sha,
            'storage_key': f'jobs/{JOB_ID}/{notes_sha}',
        }
        assert (tmp_path / 'jobs' / JOB_ID / notes_sha).read_bytes() == NOTES
        assert store.read_verified(rows[1]['storage_key'], rows[1]['sha256']) == SUMMARY
        assert [p.name for p in (tmp_path / 'jobs' / JOB_ID).iterdir() if p.name.startswith('.tmp-')] == []

    def test_a_reader_refuses_bytes_that_do_not_match(self, tmp_path):
        store = LocalOutputFileStore(str(tmp_path))
        sha = hashlib.sha256(NOTES).hexdigest()
        store.put(storage_key(JOB_ID, sha), b'tampered', 'text/plain')
        with pytest.raises(OutputFileIntegrityError):
            store.read_verified(storage_key(JOB_ID, sha), sha)

    def test_keys_cannot_leave_the_root(self, tmp_path):
        store = LocalOutputFileStore(str(tmp_path / 'root'))
        with pytest.raises(ValueError, match='outside the store'):
            store.put('../escape', b'x', 'text/plain')


class TestS3Store:
    def test_put_with_kms_and_verified_read(self):
        client = MagicMock()
        store = S3OutputFileStore('epsilon-output-files', 'arn:aws:kms:ap-southeast-2:1:key/k', client=client)
        verified = _verify(_payload([_entry('notes.txt', NOTES)]))

        (row,) = store_output_files(store, JOB_ID, verified)

        client.put_object.assert_called_once_with(
            Bucket='epsilon-output-files', Key=row['storage_key'], Body=NOTES, ContentType='text/plain',
            ServerSideEncryption='aws:kms', SSEKMSKeyId='arn:aws:kms:ap-southeast-2:1:key/k',
        )
        client.get_object.return_value = {'Body': io.BytesIO(NOTES)}
        assert store.read_verified(row['storage_key'], row['sha256']) == NOTES
        client.get_object.assert_called_once_with(Bucket='epsilon-output-files', Key=row['storage_key'])

    def test_without_a_kms_key_the_bucket_default_encryption_applies(self):
        client = MagicMock()
        S3OutputFileStore('bucket', client=client).put('jobs/J/abc', b'x', 'image/png')
        client.put_object.assert_called_once_with(Bucket='bucket', Key='jobs/J/abc', Body=b'x', ContentType='image/png')

    def test_a_reader_refuses_bytes_that_do_not_match(self):
        client = MagicMock()
        client.get_object.return_value = {'Body': io.BytesIO(b'tampered')}
        with pytest.raises(OutputFileIntegrityError):
            S3OutputFileStore('bucket', client=client).read_verified('jobs/J/abc', hashlib.sha256(NOTES).hexdigest())


class TestConfiguration:
    def test_unset_store_is_none(self):
        assert output_file_store(OutputFilesConfig(store='')) is None

    def test_local_store(self, tmp_path):
        assert isinstance(output_file_store(OutputFilesConfig(store='local', directory=str(tmp_path))), LocalOutputFileStore)

    def test_s3_store_uses_the_configured_region(self, monkeypatch):
        boto3 = MagicMock()
        monkeypatch.setattr('workers.executor.output_files.boto3', boto3)
        store = output_file_store(OutputFilesConfig(store='s3', bucket='b', region='ap-southeast-2'))
        assert isinstance(store, S3OutputFileStore)
        boto3.client.assert_called_once_with('s3', region_name='ap-southeast-2')

    def test_settings_read_the_environment(self, monkeypatch):
        monkeypatch.setenv('OUTPUT_FILES_STORE', ' S3 ')
        monkeypatch.setenv('OUTPUT_FILES_BUCKET', 'epsilon-output-files')
        monkeypatch.setenv('OUTPUT_FILES_KMS_KEY_ID', 'alias/output-files')
        monkeypatch.setenv('OUTPUT_FILES_DIR', '/data/files')
        monkeypatch.setenv('AWS_REGION', 'us-east-1')
        config = OutputFilesConfig()
        assert (config.store, config.bucket, config.kms_key_id, config.directory, config.region) == (
            's3', 'epsilon-output-files', 'alias/output-files', '/data/files', 'us-east-1',
        )
        assert (config.max_files, config.max_total_mb) == (20, 10)

    @pytest.mark.parametrize('config, error', [
        (OutputFilesConfig(store='gcs'), 'OUTPUT_FILES_STORE must be s3 or local'),
        (OutputFilesConfig(store='s3', bucket=''), 'OUTPUT_FILES_BUCKET is required'),
        (OutputFilesConfig(store='local', directory=''), 'OUTPUT_FILES_DIR is required'),
    ])
    def test_incomplete_configuration_is_an_error(self, config, error):
        _, errors = validate_environment(Settings(output_files=config))
        assert any(error in e for e in errors)

    @pytest.mark.parametrize('config', [
        OutputFilesConfig(store=''), OutputFilesConfig(store='s3', bucket='b'), OutputFilesConfig(store='local', directory='/d'),
    ])
    def test_complete_configuration_is_accepted(self, config):
        _, errors = validate_environment(Settings(output_files=config))
        assert not any('OUTPUT_FILES' in e for e in errors)
