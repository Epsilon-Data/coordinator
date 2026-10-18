"""Output files against a real Postgres: migration d8b4f1a6c3e9 and JobRepository.complete_job_with_held_output.

Needs TEST_DATABASE_URL like test_release_gate_db, whose database helpers it reuses.
"""
import hashlib
import os

import psycopg2
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from shared.db import Database, JobRepository
from shared.output_manifest import build_manifest, manifest_sha256
from tests.shared.test_release_gate_db import (  # noqa: F401 (gate_db, repo and engine are fixtures)
    ALICE,
    BASE_REVISION,
    BOB,
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

TEST_DATABASE_URL = os.environ.get('TEST_DATABASE_URL')

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason='TEST_DATABASE_URL is not set (admin URL of a throwaway Postgres, '
           'e.g. postgresql://postgres@localhost:55432/postgres)',
)

PROJECT_REVISION = 'c5e2a8d4b7f1'
FILES_REVISION = 'd8b4f1a6c3e9'
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
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': FILES_REVISION}]
                # Output held before the revision has no manifest, and is still decided without one
                assert _held(engine, 'JOB-F-PROD')['files_manifest_sha256'] is None
                _decide(engine, 'JOB-F-PROD', None)
                assert _guard_body(engine) != project_guard

                repo = JobRepository(database)
                job_id = _new_job(engine, 'JOB-F-DOWN')
                _hold(repo, job_id, [_file(job_id, 'table.csv', b'a')])
                with pytest.raises(DBAPIError, match='refusing to downgrade'):
                    _alembic(url, command.downgrade, PROJECT_REVISION)
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': FILES_REVISION}]

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
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': FILES_REVISION}]
            finally:
                database.close()
