"""Output files against a real Postgres: migrations d8b4f1a6c3e9 and f2b7d5e9a1c4 and JobRepository.complete_job_with_held_output.

Needs TEST_DATABASE_URL like test_release_gate_db, whose database helpers it reuses.
"""
import hashlib
import io
import json
import os
import uuid
import zipfile
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from shared.db import Database, JobRepository
from shared.output_manifest import build_manifest, manifest_sha256
from workers.executor.clients import EnclaveClientLocal
from workers.executor.executor import SecureExecutor
from workers.executor.output_files import LocalOutputFileStore
from workers.executor.settings import OutputFilesConfig
from workers.executor.worker import ExecutorWorker
from tests.shared.test_release_gate_db import (  # noqa: F401 (gate_db, repo and engine are fixtures)
    ALICE,
    BASE_REVISION,
    BOB,
    HEAD_REVISION,
    PROJECT,
    WS_ALICE,
    _alembic,
    _expect_sqlstate,
    _fresh_database,
    _held,
    _job,
    _libpq_url,
    _new_job,
    _rows,
    _sha256,
    engine,
    gate_db,
    repo,
)

try:
    from epsilon_verifier import verify_attestation
except ImportError:
    verify_attestation = None

TEST_DATABASE_URL = os.environ.get('TEST_DATABASE_URL')

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason='TEST_DATABASE_URL is not set (admin URL of a throwaway Postgres, '
           'e.g. postgresql://postgres@localhost:55432/postgres)',
)

PROJECT_REVISION = 'c5e2a8d4b7f1'
FILES_REVISION = 'd8b4f1a6c3e9'
PATHS_REVISION = 'f2b7d5e9a1c4'
CONTENT = 'count=7\n'


def _file(job_id, name, data, content_type='text/csv'):
    sha256 = hashlib.sha256(data).hexdigest()
    return {'name': name, 'size': len(data), 'content_type': content_type, 'sha256': sha256,
            'storage_key': f'jobs/{job_id}/{sha256}'}


def _manifest(files):
    return manifest_sha256(build_manifest(dict(f, type=f['content_type']) for f in files))


def _hold(repo, job_id, files=None, content=CONTENT, legacy=False):
    files = files or []
    return repo.complete_job_with_held_output(
        job_id, status='success', kind='result', content=content, attested_output_hash=_sha256(content),
        files=files, files_manifest_sha256=None if legacy else _manifest(files),
    )


def _file_rows(engine, job_id):
    return _rows(engine, 'SELECT name, size, content_type, sha256, storage_key FROM job_output_files '
                         'WHERE job_id = :job_id ORDER BY name', job_id=job_id)


INSERT_DECISION = ('INSERT INTO release_decisions (job_id, decision, output_sha256, reviewer_id, files_manifest_sha256) '
                   'VALUES (:job_id, :decision, :sha, :reviewer, :manifest)')


def _decide(engine, job_id, manifest, decision='approve'):
    """A reviewer's decision on the held output, naming the files manifest they saw."""
    with engine.begin() as conn:
        conn.execute(text('UPDATE reviewer_projects SET synced_at = now() WHERE user_id = :user_id'), {'user_id': BOB})
    with engine.begin() as conn:
        conn.execute(text(INSERT_DECISION), {'job_id': job_id, 'decision': decision, 'sha': _sha256(CONTENT),
                                             'reviewer': BOB, 'manifest': manifest})
        conn.execute(text("UPDATE job_requests SET release_status = 'released' WHERE job_id = :job_id"),
                     {'job_id': job_id})


def _guard_body(engine):
    (row,) = _rows(engine, "SELECT pg_get_functiondef('release_decisions_before_insert'::regproc) AS body")
    return row['body']


class TestHeldFiles:
    def test_holds_the_files_and_their_manifest_hash(self, repo, engine):
        job_id = _new_job(engine, 'JOB-F-HOLD')
        files = [_file(job_id, 'table.csv', b'a,b\n'), _file(job_id, 'chart.png', b'png', 'image/png')]

        _hold(repo, job_id, files)

        assert _held(engine, job_id)['files_manifest_sha256'] == _manifest(files)
        assert _file_rows(engine, job_id) == sorted(files, key=lambda f: f['name'])

    def test_a_rerun_of_an_undecided_job_replaces_the_files_and_manifest(self, repo, engine):
        job_id = _new_job(engine, 'JOB-F-REPLACE')
        _hold(repo, job_id, [_file(job_id, 'table.csv', b'first'), _file(job_id, 'old.txt', b'x', 'text/plain')])
        second = [_file(job_id, 'table.csv', b'second')]

        _hold(repo, job_id, second)

        assert _held(engine, job_id)['files_manifest_sha256'] == _manifest(second)
        assert _file_rows(engine, job_id) == second

    def test_a_rerun_from_an_enclave_without_files_clears_them(self, repo, engine):
        job_id = _new_job(engine, 'JOB-F-LEGACY-RERUN')
        _hold(repo, job_id, [_file(job_id, 'table.csv', b'first')])

        _hold(repo, job_id, legacy=True)

        assert _held(engine, job_id)['files_manifest_sha256'] is None
        assert _file_rows(engine, job_id) == []

    def test_a_decided_job_keeps_its_files_and_manifest(self, repo, engine):
        job_id = _new_job(engine, 'JOB-F-DECIDED')
        files = [_file(job_id, 'table.csv', b'reviewed')]
        _hold(repo, job_id, files)
        _decide(engine, job_id, _manifest(files))

        _hold(repo, job_id, [_file(job_id, 'other.csv', b'new')], content='new result')

        assert _held(engine, job_id)['files_manifest_sha256'] == _manifest(files)
        assert _file_rows(engine, job_id) == files

    def test_a_held_result_and_its_files_are_not_replaced_by_an_error(self, repo, engine):
        job_id = _new_job(engine, 'JOB-F-KEEP')
        files = [_file(job_id, 'table.csv', b'kept')]
        _hold(repo, job_id, files)

        repo.complete_job_with_held_output(job_id, status='failed', kind='error', content='late error')

        assert _held(engine, job_id)['files_manifest_sha256'] == _manifest(files)
        assert _file_rows(engine, job_id) == files

    @pytest.mark.parametrize('kwargs, error', [
        ({'files': [{'name': 'a.csv', 'size': 1, 'content_type': 'text/csv', 'sha256': 'a' * 64, 'storage_key': 'k'}],
          'files_manifest_sha256': '0' * 64}, 'does not match the held files'),
        ({'files': [{'name': 'a.csv', 'size': 1, 'content_type': 'text/csv', 'sha256': 'a' * 64, 'storage_key': 'k'}]},
         'need their files_manifest_sha256'),
        ({'kind': 'error', 'files_manifest_sha256': manifest_sha256([])}, 'only a held result'),
    ])
    def test_inconsistent_files_are_refused(self, repo, engine, kwargs, error):
        job_id = _new_job(engine, f'JOB-F-BAD-{len(error)}')
        args = {'status': 'success', 'kind': 'result', 'content': CONTENT, **kwargs}
        with pytest.raises(ValueError, match=error):
            repo.complete_job_with_held_output(job_id, **args)
        assert _held(engine, job_id) is None


INSERT_FILE = ("INSERT INTO job_output_files (job_id, name, size, content_type, sha256, storage_key) "
               "VALUES (:job_id, :name, 1, 'application/octet-stream', :sha, 'k')")
LONGEST_NAME = '/'.join(['a' * 50] * 4 + ['b' * 51])  # 5 segments, 255 characters


class TestFileNames:
    """The CHECK on job_output_files.name is the name rule: a relative path of 1 to 5 segments, at most 255 characters."""

    @pytest.fixture
    def job_id(self, repo, engine):
        job_id = _new_job(engine, f'JOB-F-NAME-{uuid.uuid4().hex[:12]}')
        _hold(repo, job_id)
        return job_id

    @pytest.mark.parametrize('name', [
        'a', 'notes.txt', 'plots/fig1.png', 'a/b/c/d/e.bin', 'README', 'x' * 100, LONGEST_NAME,
    ], ids=['one', 'flat', 'path', 'five', 'noext', 'segment100', 'longest'])
    def test_paths_are_accepted(self, engine, job_id, name):
        with engine.begin() as conn:
            conn.execute(text(INSERT_FILE), {'job_id': job_id, 'name': name, 'sha': 'a' * 64})
        assert [r['name'] for r in _file_rows(engine, job_id)] == [name]

    @pytest.mark.parametrize('name', [
        '', '.hidden', 'a/.hidden', '../x', 'a/../b', './a', '/a', 'a/', 'a//b', 'a b', 'x' * 101,
        'a/b/c/d/e/f', 'a\\b', 'x.csv\n', 'caf\u00e9',
    ])
    def test_bad_names_are_rejected(self, engine, job_id, name):
        _expect_sqlstate(engine, '23514', INSERT_FILE, job_id=job_id, name=name, sha='a' * 64)

    def test_names_over_255_characters_are_rejected(self, engine, job_id):
        with pytest.raises(DBAPIError) as excinfo:
            with engine.begin() as conn:
                conn.execute(text(INSERT_FILE), {'job_id': job_id, 'name': LONGEST_NAME + 'a', 'sha': 'a' * 64})
        assert excinfo.value.orig.pgcode in ('22001', '23514')

    def test_the_check_is_the_rule(self, engine):
        (row,) = _rows(engine, "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
                               "WHERE conname = 'job_output_files_name_check'")
        assert '{0,4}' in row['def'] and 'char_length' in row['def']

    def test_a_subfolder_file_is_held(self, repo, engine):
        job_id = _new_job(engine, 'JOB-F-PATH')
        files = [_file(job_id, 'plots/raw/fig.bin', b'\x00\x01\x02', 'application/octet-stream'),
                 _file(job_id, 'table.csv', b'a,b\n')]
        _hold(repo, job_id, files)
        assert _file_rows(engine, job_id) == files
        assert _held(engine, job_id)['files_manifest_sha256'] == _manifest(files)


class TestDecisionManifest:
    def test_a_decision_naming_the_held_manifest_is_accepted(self, repo, engine):
        job_id = _new_job(engine, 'JOB-F-MATCH')
        files = [_file(job_id, 'table.csv', b'a')]
        _hold(repo, job_id, files)

        _decide(engine, job_id, _manifest(files))

        assert _rows(engine, 'SELECT files_manifest_sha256 FROM release_decisions WHERE job_id = :job_id',
                     job_id=job_id) == [{'files_manifest_sha256': _manifest(files)}]

    @pytest.mark.parametrize('seen', ['0' * 64, None, manifest_sha256([])], ids=['other', 'none', 'empty'])
    def test_a_decision_naming_another_manifest_is_refused(self, repo, engine, seen):
        job_id = _new_job(engine, f'JOB-F-MISMATCH-{seen and seen[:2]}')
        _hold(repo, job_id, [_file(job_id, 'table.csv', b'a')])

        _expect_sqlstate(engine, 'RG004', INSERT_DECISION, job_id=job_id, decision='approve',
                         sha=_sha256(CONTENT), reviewer=BOB, manifest=seen)

    def test_legacy_output_without_a_manifest_is_decided_without_one(self, repo, engine):
        job_id = _new_job(engine, 'JOB-F-LEGACY')
        _hold(repo, job_id, legacy=True)

        _expect_sqlstate(engine, 'RG004', INSERT_DECISION, job_id=job_id, decision='approve',
                         sha=_sha256(CONTENT), reviewer=BOB, manifest=manifest_sha256([]))
        _decide(engine, job_id, None)
        assert _job(engine, job_id)['release_status'] == 'released'

    def test_a_run_without_files_is_decided_with_the_empty_manifest(self, repo, engine):
        job_id = _new_job(engine, 'JOB-F-EMPTY')
        _hold(repo, job_id, [])
        _decide(engine, job_id, manifest_sha256([]))
        assert _job(engine, job_id)['release_status'] == 'released'


class TestFilesFixedAfterDecision:
    @pytest.fixture
    def decided(self, request, repo, engine):
        job_id = _new_job(engine, f'JOB-F-FIXED-{request.node.name}')
        files = [_file(job_id, 'table.csv', b'reviewed')]
        _hold(repo, job_id, files)
        _decide(engine, job_id, _manifest(files))
        return job_id

    def test_file_rows_cannot_be_added_changed_or_removed(self, engine, decided):
        _expect_sqlstate(
            engine, 'RG010',
            "INSERT INTO job_output_files (job_id, name, size, content_type, sha256, storage_key) "
            "VALUES (:job_id, 'extra.csv', 1, 'text/csv', :sha, 'k')",
            job_id=decided, sha='b' * 64,
        )
        _expect_sqlstate(engine, 'RG010', "UPDATE job_output_files SET sha256 = :sha WHERE job_id = :job_id",
                         job_id=decided, sha='c' * 64)
        _expect_sqlstate(engine, 'RG010', 'DELETE FROM job_output_files WHERE job_id = :job_id', job_id=decided)

    def test_the_manifest_hash_cannot_change(self, engine, decided):
        _expect_sqlstate(engine, 'RG010', 'UPDATE job_outputs SET files_manifest_sha256 = :sha WHERE job_id = :job_id',
                         job_id=decided, sha='d' * 64)
        _expect_sqlstate(engine, 'RG010', 'UPDATE job_outputs SET files_manifest_sha256 = NULL WHERE job_id = :job_id',
                         job_id=decided)

    def test_deleting_the_job_removes_its_files(self, engine, decided):
        with engine.begin() as conn:
            conn.execute(text('DELETE FROM job_requests WHERE job_id = :job_id'), {'job_id': decided})
        assert _file_rows(engine, decided) == []

    def test_undecided_file_rows_may_change(self, repo, engine):
        job_id = _new_job(engine, 'JOB-F-OPEN')
        _hold(repo, job_id, [_file(job_id, 'table.csv', b'a')])
        with engine.begin() as conn:
            conn.execute(text('DELETE FROM job_output_files WHERE job_id = :job_id'), {'job_id': job_id})
            conn.execute(text('UPDATE job_outputs SET files_manifest_sha256 = NULL WHERE job_id = :job_id'),
                         {'job_id': job_id})
        assert _file_rows(engine, job_id) == []


def _hold_at_project_revision(url, job_id):
    """Hold a result as the c5e2a8d4b7f1 code did (no files columns yet)."""
    conn = psycopg2.connect(_libpq_url(url))
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO job_requests (job_id, workspace_id, user_id, commit_sha, status, project_id, archetype_id, "
                "release_status) VALUES (%s, %s, %s, 'c0ffee', 'success', %s, 'arch', 'pending')",
                (job_id, WS_ALICE, ALICE, PROJECT),
            )
            cur.execute(
                "INSERT INTO job_outputs (job_id, kind, content, content_sha256, attested_output_hash) "
                "VALUES (%s, 'result', %s, %s, %s)",
                (job_id, CONTENT, _sha256(CONTENT), _sha256(CONTENT)),
            )
    finally:
        conn.close()


class TestMigration:
    def test_upgrade_over_held_output_and_downgrade_restore_the_project_revision(self):
        with _fresh_database() as url:
            _alembic(url, command.stamp, BASE_REVISION)
            _alembic(url, command.upgrade, PROJECT_REVISION)
            _hold_at_project_revision(url, 'JOB-F-PROD')
            conn = psycopg2.connect(_libpq_url(url))
            try:
                with conn, conn.cursor() as cur:
                    cur.execute('INSERT INTO reviewer_projects (user_id, project_id) VALUES (%s, %s)', (BOB, PROJECT))
            finally:
                conn.close()
            database = Database(url)
            try:
                engine = database.engine
                project_guard = _guard_body(engine)

                _alembic(url, command.upgrade, 'head')
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': HEAD_REVISION}]
                # Output held before the revision has no manifest, and is still decided without one
                assert _held(engine, 'JOB-F-PROD')['files_manifest_sha256'] is None
                _decide(engine, 'JOB-F-PROD', None)
                assert _guard_body(engine) != project_guard

                repo = JobRepository(database)
                job_id = _new_job(engine, 'JOB-F-DOWN')
                _hold(repo, job_id, [_file(job_id, 'table.csv', b'a')])
                with pytest.raises(DBAPIError, match='refusing to downgrade'):
                    _alembic(url, command.downgrade, PROJECT_REVISION)
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': HEAD_REVISION}]

                with engine.begin() as conn:
                    conn.execute(text('DELETE FROM job_output_files WHERE job_id = :job_id'), {'job_id': job_id})
                _alembic(url, command.downgrade, PROJECT_REVISION)
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': PROJECT_REVISION}]
                assert _guard_body(engine) == project_guard
                assert _rows(engine, "SELECT 1 FROM information_schema.tables WHERE table_name = 'job_output_files'") == []
                assert _rows(engine, "SELECT table_name FROM information_schema.columns "
                                     "WHERE column_name = 'files_manifest_sha256'") == []
                assert _rows(engine, "SELECT proname FROM pg_proc WHERE proname IN "
                                     "('job_output_files_guard', 'job_outputs_files_manifest_guard')") == []

                _alembic(url, command.upgrade, 'head')
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': HEAD_REVISION}]
            finally:
                database.close()


def _hold_flat_files_at_files_revision(engine, repo):
    """Held files as production has them at d8b4f1a6c3e9: flat names, one at the old 100-character limit."""
    job_id = _new_job(engine, 'JOB-F-PROD-FILES')
    files = [_file(job_id, 'table.csv', b'a,b\n'), _file(job_id, 'chart.png', b'png', 'image/png'),
             _file(job_id, 'x' * 96 + '.txt', b'notes', 'text/plain')]
    _hold(repo, job_id, files)
    return job_id, files


def _name_column(engine):
    (row,) = _rows(engine, "SELECT character_maximum_length AS length FROM information_schema.columns "
                           "WHERE table_name = 'job_output_files' AND column_name = 'name'")
    (check,) = _rows(engine, "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
                             "WHERE conname = 'job_output_files_name_check'")
    return row['length'], check['def']


class TestPathsMigration:
    def test_upgrade_over_flat_files_and_downgrade_refusing_paths(self):
        with _fresh_database() as url:
            _alembic(url, command.stamp, BASE_REVISION)
            _alembic(url, command.upgrade, FILES_REVISION)
            database = Database(url)
            try:
                engine, repo = database.engine, JobRepository(database)
                flat_job, flat_files = _hold_flat_files_at_files_revision(engine, repo)
                files_column = _name_column(engine)
                assert files_column[0] == 100

                _alembic(url, command.upgrade, 'head')
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': PATHS_REVISION}]
                length, check = _name_column(engine)
                assert length == 255 and '{0,4}' in check and 'char_length' in check
                assert _file_rows(engine, flat_job) == sorted(flat_files, key=lambda f: f['name'])
                _expect_sqlstate(engine, '23514', INSERT_FILE, job_id=flat_job, name='a/../b', sha='a' * 64)

                path_job = _new_job(engine, 'JOB-F-PATH-DOWN')
                _hold(repo, path_job, [_file(path_job, 'plots/fig.bin', b'\x00', 'application/octet-stream')])
                with pytest.raises(DBAPIError, match='refusing to downgrade'):
                    _alembic(url, command.downgrade, FILES_REVISION)
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': PATHS_REVISION}]

                with engine.begin() as conn:
                    conn.execute(text('DELETE FROM job_output_files WHERE job_id = :job_id'), {'job_id': path_job})
                _alembic(url, command.downgrade, FILES_REVISION)
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': FILES_REVISION}]
                assert _name_column(engine) == files_column
                assert _file_rows(engine, flat_job) == sorted(flat_files, key=lambda f: f['name'])

                _alembic(url, command.upgrade, 'head')
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': PATHS_REVISION}]
            finally:
                database.close()


LOCAL_SCRIPT = (
    "import os\n"
    "out = os.environ['EPSILON_OUTPUT_DIR']\n"
    "open(os.path.join(out, 'table.csv'), 'w').write('group,n\\na,7\\n')\n"
    "open(os.path.join(out, 'chart.png'), 'wb').write(b'PNG')\n"
    "os.makedirs(os.path.join(out, 'plots', 'raw'))\n"
    "open(os.path.join(out, 'plots', 'raw', 'fig.bin'), 'wb').write(bytes([0, 1, 2]))\n"
    "print('count=7')\n"
)


def _verify_local_attestation(*args, allow_expired=False, **kwargs):
    # The installed verifier may predate allow_expired; the local certificate is fresh anyway
    return verify_attestation(*args, **kwargs)


@pytest.mark.skipif(verify_attestation is None, reason='epsilon_verifier is not installed')
class TestLocalEnclaveRun:
    """A run on the local enclave (USE_LOCAL_ENCLAVE=true) holds its files like a real enclave run."""

    @pytest.fixture
    def worker(self, tmp_path, monkeypatch):
        monkeypatch.setenv('USE_LOCAL_ENCLAVE', 'true')
        settings = MagicMock()
        settings.storage.shared_storage_path = str(tmp_path / 'storage')
        settings.output_files = OutputFilesConfig(store='local', directory=str(tmp_path / 'files'))
        with patch('workers.executor.clients.get_settings', return_value=settings):
            client = EnclaveClientLocal()
        executor = SecureExecutor.__new__(SecureExecutor)
        executor._active_jobs = {}
        executor._enclave_client = client
        executor._log = MagicMock()

        def execute(request):
            bundle = io.BytesIO()
            with zipfile.ZipFile(bundle, 'w') as zf:
                zf.writestr('build.yml', 'analysis:\n  script_file: main.py\n')
                zf.writestr('main.py', LOCAL_SCRIPT)
            public_key, session_id = client.get_public_key(request.job_id)
            success, output, attestation, files = client.send_encrypted_data_to_enclave(
                session_id, client.encrypt_zip_data(bundle.getvalue(), public_key),
            )
            return executor._create_result(request.job_id, success, output, 1.0, attestation, {}, files)

        worker = ExecutorWorker.__new__(ExecutorWorker)
        worker._settings = settings
        worker._output_file_store = LocalOutputFileStore(settings.output_files.directory)
        worker._executor = MagicMock(is_ready=True, execute=MagicMock(side_effect=execute))
        worker._atl_client = None
        return worker

    def test_the_files_are_stored_and_held_with_the_attested_manifest(self, repo, engine, worker, tmp_path):
        job_id = _new_job(engine, 'JOB-F-LOCAL')
        (tmp_path / 'storage' / 'repositories' / job_id).mkdir(parents=True)
        job = {
            'job_id': job_id, 'workspace_id': WS_ALICE, 'user_id': ALICE, 'researcher_nonce': None,
            'commit_sha': 'c0ffee', 'github_repo': 'alice/analysis', 'github_branch': 'main',
            'commit_message': 'run', 'commit_author': 'alice', 'created_at': '2026-10-18T00:00:00',
        }

        with patch('workers.executor.worker.job_repository', repo), \
                patch('workers.executor.worker.verify_attestation', _verify_local_attestation):
            assert worker.process_job(job) is True

        table, chart, fig = b'group,n\na,7\n', b'PNG', b'\x00\x01\x02'
        files = [_file(job_id, 'chart.png', chart, 'image/png'),
                 _file(job_id, 'plots/raw/fig.bin', fig, 'application/octet-stream'),
                 _file(job_id, 'table.csv', table)]
        held = _held(engine, job_id)
        assert (held['kind'], held['content']) == ('result', 'count=7\n')
        assert held['files_manifest_sha256'] == _manifest(files)
        assert _file_rows(engine, job_id) == files
        for f, data in zip(files, (chart, fig, table)):
            assert (tmp_path / 'files' / f['storage_key']).read_bytes() == data
        row = _job(engine, job_id)
        assert row['status'] == 'success'
        receipt = json.loads(row['verification_receipt'])
        assert (receipt['valid'], receipt['files_manifest_sha256']) == (True, _manifest(files))
