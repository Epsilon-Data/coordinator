"""Output-release gate against a real Postgres: migrations 7f3a9c2d1e4b, c5e2a8d4b7f1 and d8b4f1a6c3e9, and JobRepository.

Needs TEST_DATABASE_URL, an admin URL of a throwaway server (for example
postgresql://postgres@localhost:55432/postgres). Each run creates and drops its
own rg_coord_* databases there. The module-level shared.db.db is never used.
"""
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from shared.base_worker import BaseWorker
from shared.db import Database, JobRepository, HELD_ERROR_MESSAGE, ProjectChangedError

TEST_DATABASE_URL = os.environ.get('TEST_DATABASE_URL')
MARKER = 'MARKER-rg-db-patient-42-row-value'

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason='TEST_DATABASE_URL is not set (admin URL of a throwaway Postgres, '
           'e.g. postgresql://postgres@localhost:55432/postgres)',
)

ROOT = Path(__file__).resolve().parents[2]
BASE_SCHEMA = ROOT / 'tests' / 'fixtures' / 'release_gate_base_schema.sql'
BASE_REVISION = '2e1ea7116ba4'
GATE_REVISION = '7f3a9c2d1e4b'
HEAD_REVISION = 'd8b4f1a6c3e9'

ALICE = '00000000-0000-0000-0000-0000000000a1'
BOB = '00000000-0000-0000-0000-0000000000b2'  # reviewer of PROJECT
CAROL = '00000000-0000-0000-0000-0000000000c3'  # staff
DAVE = '00000000-0000-0000-0000-0000000000d4'  # reviewer, approval pending
ERIN = '00000000-0000-0000-0000-0000000000e5'  # reviewer, approval rejected
FRANK = '00000000-0000-0000-0000-0000000000f6'  # researcher
GRACE = '00000000-0000-0000-0000-0000000000a7'  # reviewer, assignments set per test
WS_ALICE = '00000000-0000-0000-0000-00000000aaaa'
WS_BOB = '00000000-0000-0000-0000-00000000bbbb'
ALICE_SUB = '3f6c1a52-8e0d-4b6a-9c7e-2d4f8a1b5e93'

PROJECT = 'eba92dec-17a1-4a47-816e-77087ceb9531'
OTHER_PROJECT = 'a7702e2d-69dd-4543-b3b6-a6df21d287c9'
ARCHETYPE = 'KYZ0RWPE6zwE'

INSERT_DECISION = ('INSERT INTO release_decisions (job_id, decision, output_sha256, reviewer_id, reason) '
                   'VALUES (:job_id, :decision, :sha, :reviewer, :reason)')

# Jobs as they exist before the gate: three finished, three in flight.
LEGACY_SEED = """
INSERT INTO users (id, external_id, username, email, role, approval_status) VALUES
 ('00000000-0000-0000-0000-0000000000a1','3f6c1a52-8e0d-4b6a-9c7e-2d4f8a1b5e93','alice','a@x','user','approved'),
 ('00000000-0000-0000-0000-0000000000b2','ext-b','bob','b@x','reviewer','approved'),
 ('00000000-0000-0000-0000-0000000000c3','ext-c','carol','c@x','staff','approved'),
 ('00000000-0000-0000-0000-0000000000d4','ext-d','dave','d@x','reviewer','pending'),
 ('00000000-0000-0000-0000-0000000000e5','ext-e','erin','e@x','reviewer','rejected'),
 ('00000000-0000-0000-0000-0000000000f6','ext-f','frank','f@x','user','approved'),
 ('00000000-0000-0000-0000-0000000000a7','ext-g','grace','g@x','reviewer','approved');
INSERT INTO workspaces (id, name, user_id, github_repo, github_branch) VALUES
 ('00000000-0000-0000-0000-00000000aaaa','ws-alice','00000000-0000-0000-0000-0000000000a1','alice/r','main'),
 ('00000000-0000-0000-0000-00000000bbbb','ws-bob','00000000-0000-0000-0000-0000000000b2','bob/r','main');
INSERT INTO job_requests (job_id, workspace_id, user_id, commit_sha, status, execution_output, error_message, execution_metrics) VALUES
 ('JOB-OLD-SUCCESS','00000000-0000-0000-0000-00000000aaaa','00000000-0000-0000-0000-0000000000a1','c1','success','42',NULL,'{"total_ms": 10, "ai_decision": "ai_approved"}'),
 ('JOB-OLD-FAILED','00000000-0000-0000-0000-00000000aaaa','00000000-0000-0000-0000-0000000000a1','c2','failed',NULL,'Bundle execution failed: STDOUT: secret','not json {"ai_decision": "ai_rejected"'),
 ('JOB-OLD-REJECTED','00000000-0000-0000-0000-00000000aaaa','00000000-0000-0000-0000-0000000000a1','c3','rejected',NULL,'Build validation failed: x',NULL),
 ('JOB-INFLIGHT','00000000-0000-0000-0000-00000000aaaa','00000000-0000-0000-0000-0000000000a1','c4','executing',NULL,NULL,'{"total_ms": 1}'),
 ('JOB-QUEUED','00000000-0000-0000-0000-00000000aaaa','00000000-0000-0000-0000-0000000000a1','c5','queued',NULL,NULL,NULL),
 ('JOB-BOB','00000000-0000-0000-0000-00000000bbbb','00000000-0000-0000-0000-0000000000b2','c6','executing',NULL,NULL,NULL);
"""


def _sha256(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _libpq_url(url):
    return make_url(url).set(drivername='postgresql').render_as_string(hide_password=False)


def _admin(sql):
    conn = psycopg2.connect(_libpq_url(TEST_DATABASE_URL))
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()


@contextmanager
def _fresh_database():
    """A new database with the pre-gate base schema and the legacy seed."""
    name = f'rg_coord_{uuid.uuid4().hex[:12]}'
    _admin(f'CREATE DATABASE {name}')
    url = make_url(TEST_DATABASE_URL).set(database=name).render_as_string(hide_password=False)
    try:
        conn = psycopg2.connect(_libpq_url(url))
        try:
            with conn, conn.cursor() as cur:
                cur.execute(BASE_SCHEMA.read_text())
                cur.execute(LEGACY_SEED)
        finally:
            conn.close()
        yield url
    finally:
        _admin(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')


def _alembic(url, action, revision):
    # No ini file, so env.py leaves the test process's logging configuration alone.
    cfg = Config()
    cfg.set_main_option('script_location', str(ROOT / 'migrations'))
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv('DATABASE_URL', url)
        action(cfg, revision)


@pytest.fixture(scope='module')
def gate_db():
    with _fresh_database() as url:
        _alembic(url, command.stamp, BASE_REVISION)
        _alembic(url, command.upgrade, 'head')
        # What ResearchWorkspace mirrors from Keycloak: bob reviews PROJECT
        conn = psycopg2.connect(_libpq_url(url))
        try:
            with conn, conn.cursor() as cur:
                cur.execute('INSERT INTO reviewer_projects (user_id, project_id) VALUES (%s, %s)', (BOB, PROJECT))
        finally:
            conn.close()
        yield url


@pytest.fixture(scope='module')
def repo(gate_db):
    database = Database(gate_db)
    yield JobRepository(database)
    database.close()


@pytest.fixture
def engine(repo):
    return repo.db.engine


def _rows(engine, sql, **params):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def _job(engine, job_id):
    (row,) = _rows(engine, 'SELECT * FROM job_requests WHERE job_id = :job_id', job_id=job_id)
    return row


def _held(engine, job_id):
    rows = _rows(engine, 'SELECT * FROM job_outputs WHERE job_id = :job_id', job_id=job_id)
    return rows[0] if rows else None


def _new_job(engine, job_id, user_id=ALICE, workspace_id=WS_ALICE, status='executing', **columns):
    values = {'job_id': job_id, 'workspace_id': workspace_id, 'user_id': user_id,
              'commit_sha': 'c0ffee', 'status': status, 'project_id': PROJECT, 'archetype_id': ARCHETYPE, **columns}
    with engine.begin() as conn:
        conn.execute(
            text(f"INSERT INTO job_requests ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})"),
            values,
        )
    return job_id


def _assign(engine, reviewer_id, project_id, minutes_ago=0):
    """Mirror a Keycloak reviewer group membership, confirmed minutes_ago."""
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO reviewer_projects (user_id, project_id, synced_at) "
                 "VALUES (:user_id, :project_id, now() - make_interval(mins => :minutes)) "
                 "ON CONFLICT (user_id, project_id) DO UPDATE SET synced_at = EXCLUDED.synced_at"),
            {'user_id': reviewer_id, 'project_id': project_id, 'minutes': minutes_ago},
        )


def _unassign_all(engine, reviewer_id):
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM reviewer_projects WHERE user_id = :user_id'), {'user_id': reviewer_id})


def _decide(engine, job_id, decision, reviewer_id=BOB, reason=None):
    """What ResearchWorkspace does: confirm the reviewer's projects, then the decision and the release status."""
    output_sha256 = _held(engine, job_id)['content_sha256']
    with engine.begin() as conn:
        conn.execute(text('UPDATE reviewer_projects SET synced_at = now() WHERE user_id = :user_id'),
                     {'user_id': reviewer_id})
    with engine.begin() as conn:
        conn.execute(text('SELECT 1 FROM job_requests WHERE job_id = :job_id FOR UPDATE'), {'job_id': job_id})
        conn.execute(
            text('INSERT INTO release_decisions (job_id, decision, output_sha256, reviewer_id, reason) '
                 'VALUES (:job_id, :decision, :output_sha256, :reviewer_id, :reason)'),
            {'job_id': job_id, 'decision': decision, 'output_sha256': output_sha256,
             'reviewer_id': reviewer_id, 'reason': reason},
        )
        conn.execute(
            text('UPDATE job_requests SET release_status = :status WHERE job_id = :job_id'),
            {'job_id': job_id, 'status': 'released' if decision == 'approve' else 'withheld'},
        )


def _expect_sqlstate(engine, code, sql, **params):
    with pytest.raises(DBAPIError) as excinfo:
        with engine.begin() as conn:
            conn.execute(text(sql), params)
    assert excinfo.value.orig.pgcode == code


def _hold_result(repo, job_id, content='count=7\n', attested=True):
    """Hold a result; its attested hash matches it unless attested is False (None) or a hash."""
    attested_output_hash = _sha256(content) if attested is True else (attested or None)
    return repo.complete_job_with_held_output(
        job_id, status='success', kind='result', content=content, attested_output_hash=attested_output_hash,
    )


class TestBackfill:
    def test_finished_jobs_are_released_and_in_flight_jobs_stay_null(self, engine):
        status = {r['job_id']: r['release_status'] for r in _rows(engine, 'SELECT job_id, release_status FROM job_requests')}
        assert status['JOB-OLD-SUCCESS'] == 'released'
        assert status['JOB-OLD-FAILED'] == 'released'
        assert status['JOB-OLD-REJECTED'] == 'released'
        assert status['JOB-INFLIGHT'] is None
        assert status['JOB-QUEUED'] is None
        assert status['JOB-BOB'] is None

    def test_ai_decision_is_extracted_from_execution_metrics(self, engine):
        decision = {r['job_id']: r['ai_decision'] for r in _rows(engine, 'SELECT job_id, ai_decision FROM job_requests')}
        assert decision['JOB-OLD-SUCCESS'] == 'ai_approved'
        assert decision['JOB-OLD-FAILED'] == 'ai_rejected'  # malformed JSON, still extracted
        assert decision['JOB-OLD-REJECTED'] is None
        assert decision['JOB-INFLIGHT'] is None

    def test_legacy_output_is_untouched(self, engine):
        job = _job(engine, 'JOB-OLD-SUCCESS')
        assert job['execution_output'] == '42'
        assert job['output_sha256'] is None
        assert _held(engine, 'JOB-OLD-SUCCESS') is None


class TestGuards:
    def test_reviewer_cannot_decide_on_own_job(self, repo, engine):
        own = _new_job(engine, 'JOB-G-OWN', user_id=BOB, workspace_id=WS_BOB)
        in_own_workspace = _new_job(engine, 'JOB-G-OWN-WS', user_id=ALICE, workspace_id=WS_BOB)
        for job_id in (own, in_own_workspace):
            _hold_result(repo, job_id)
            _expect_sqlstate(
                engine, 'RG001',
                'INSERT INTO release_decisions (job_id, decision, output_sha256, reviewer_id) '
                'VALUES (:job_id, :decision, :sha, :reviewer)',
                job_id=job_id, decision='approve', sha=_sha256('count=7\n'), reviewer=BOB,
            )
            assert _job(engine, job_id)['release_status'] == 'pending'

    def test_release_requires_an_approve_decision(self, repo, engine):
        job_id = _new_job(engine, 'JOB-G-RELEASE')
        _hold_result(repo, job_id)
        _expect_sqlstate(
            engine, 'RG021',
            "UPDATE job_requests SET release_status = 'released' WHERE job_id = :job_id", job_id=job_id,
        )

    def test_held_output_is_fixed_after_a_decision(self, repo, engine):
        job_id = _new_job(engine, 'JOB-G-TAMPER')
        _hold_result(repo, job_id)
        _decide(engine, job_id, 'approve')
        _expect_sqlstate(
            engine, 'RG011',
            'UPDATE job_outputs SET content = :content, content_sha256 = :sha WHERE job_id = :job_id',
            job_id=job_id, content='tampered', sha=_sha256('tampered'),
        )
        _expect_sqlstate(engine, 'RG011', 'DELETE FROM job_outputs WHERE job_id = :job_id', job_id=job_id)

    def test_decisions_are_append_only(self, repo, engine):
        job_id = _new_job(engine, 'JOB-G-APPEND-ONLY')
        _hold_result(repo, job_id)
        _decide(engine, job_id, 'approve')

        _expect_sqlstate(
            engine, 'RG010',
            "UPDATE release_decisions SET decision = 'reject', reason = 'changed' WHERE job_id = :job_id",
            job_id=job_id,
        )
        _expect_sqlstate(engine, 'RG010', 'DELETE FROM release_decisions WHERE job_id = :job_id', job_id=job_id)
        assert _rows(engine, 'SELECT decision, reason FROM release_decisions WHERE job_id = :job_id', job_id=job_id) == [
            {'decision': 'approve', 'reason': None}
        ]

    def test_released_and_withheld_are_final(self, repo, engine):
        released = _new_job(engine, 'JOB-G-FINAL-RELEASED')
        withheld = _new_job(engine, 'JOB-G-FINAL-WITHHELD')
        for job_id in (released, withheld):
            _hold_result(repo, job_id)
        _decide(engine, released, 'approve')
        _decide(engine, withheld, 'reject', reason='row-level output')

        for job_id, status in ((released, 'pending'), (released, 'withheld'), (withheld, 'released')):
            _expect_sqlstate(
                engine, 'RG020',
                'UPDATE job_requests SET release_status = :status WHERE job_id = :job_id', job_id=job_id, status=status,
            )
        assert _job(engine, released)['release_status'] == 'released'
        assert _job(engine, withheld)['release_status'] == 'withheld'

    def test_deleting_a_decided_job_removes_its_decision_and_output(self, repo, engine):
        job_id = _new_job(engine, 'JOB-G-CASCADE')
        _hold_result(repo, job_id)
        _decide(engine, job_id, 'approve')

        with engine.begin() as conn:
            conn.execute(text('DELETE FROM job_requests WHERE job_id = :job_id'), {'job_id': job_id})

        assert _rows(engine, 'SELECT id FROM release_decisions WHERE job_id = :job_id', job_id=job_id) == []
        assert _held(engine, job_id) is None


class TestCompleteJobWithHeldOutput:
    def test_holds_result_with_hash_and_marks_pending(self, repo, engine):
        job_id = _new_job(engine, 'JOB-H-RESULT', execution_metrics='{"ai_decision": "ai_approved"}')
        content = 'count=7\n'
        attested = _sha256(content)

        returned = repo.complete_job_with_held_output(
            job_id, status='success', kind='result', content=content, attested_output_hash=attested,
            attestation={'attestation': {'module_id': 'm'}},
            verification_receipt='{"valid": true}',
            execution_metrics={'total_ms': 12.5},
            enclave_version='v9', enclave_pcr0='aa',
            job_id_committed='committed', researcher_nonce='nonce',
            commitment_receipt='{"leaf_index": 1}', ha_receipt='{"leaf_index": 2}',
        )

        held = _held(engine, job_id)
        assert held['kind'] == 'result'
        assert held['content'] == content
        assert held['content_sha256'] == _sha256(content)
        assert held['attested_output_hash'] == attested
        job = _job(engine, job_id)
        assert job['status'] == 'success'
        assert job['release_status'] == 'pending'
        assert job['output_sha256'] == _sha256(content)
        assert job['completed_at'] is not None
        assert job['execution_output'] is None
        assert json.loads(job['attestation']) == {'attestation': {'module_id': 'm'}}
        assert job['verification_receipt'] == '{"valid": true}'
        assert json.loads(job['execution_metrics']) == {'ai_decision': 'ai_approved', 'total_ms': 12.5}
        assert (job['enclave_version'], job['enclave_pcr0']) == ('v9', 'aa')
        assert (job['job_id_committed'], job['researcher_nonce']) == ('committed', 'nonce')
        assert (job['commitment_receipt'], job['ha_receipt']) == ('{"leaf_index": 1}', '{"leaf_index": 2}')
        assert returned['release_status'] == 'pending'
        assert returned['output_sha256'] == _sha256(content)

    def test_python_hash_matches_the_database_check_for_non_ascii_content(self, repo, engine):
        job_id = _new_job(engine, 'JOB-H-UTF8')
        content = 'naïve – 測試 🙂\nmean=4.2\n'

        _hold_result(repo, job_id, content)

        (row,) = _rows(
            engine,
            "SELECT content_sha256, encode(sha256(convert_to(content, 'UTF8')), 'hex') AS db_sha "
            'FROM job_outputs WHERE job_id = :job_id', job_id=job_id,
        )
        assert row['content_sha256'] == row['db_sha'] == _sha256(content)

    def test_failure_holds_error_and_shows_the_held_message(self, repo, engine):
        job_id = _new_job(engine, 'JOB-H-ERROR')
        error = 'Bundle execution failed:\nSTDOUT: secret\nSTDERR: Traceback'

        repo.complete_job_with_held_output(
            job_id, status='failed', kind='error', content=error, error_message=HELD_ERROR_MESSAGE,
        )

        held = _held(engine, job_id)
        assert (held['kind'], held['content']) == ('error', error)
        job = _job(engine, job_id)
        assert (job['status'], job['release_status']) == ('failed', 'pending')
        assert job['error_message'] == HELD_ERROR_MESSAGE
        assert job['output_sha256'] is None

    def test_replaces_an_undecided_output(self, repo, engine):
        job_id = _new_job(engine, 'JOB-H-REPLACE')
        repo.complete_job_with_held_output(job_id, status='failed', kind='error', content='first error')
        _hold_result(repo, job_id, 'first result')
        _hold_result(repo, job_id, 'second result')

        held = _held(engine, job_id)
        assert (held['kind'], held['content']) == ('result', 'second result')
        assert held['content_sha256'] == _sha256('second result')
        assert _job(engine, job_id)['output_sha256'] == _sha256('second result')

    def test_does_not_replace_a_result_with_an_error(self, repo, engine):
        job_id = _new_job(engine, 'JOB-H-KEEP-RESULT')
        _hold_result(repo, job_id, 'the result')

        repo.complete_job_with_held_output(
            job_id, status='failed', kind='error', content='late error', error_message=HELD_ERROR_MESSAGE,
        )

        held = _held(engine, job_id)
        assert (held['kind'], held['content']) == ('result', 'the result')
        job = _job(engine, job_id)
        assert (job['status'], job['release_status']) == ('failed', 'pending')
        assert job['output_sha256'] == _sha256('the result')

    @pytest.mark.parametrize('decision, final_status', [('approve', 'released'), ('reject', 'withheld')])
    def test_leaves_job_outputs_alone_after_a_decision(self, repo, engine, decision, final_status):
        job_id = _new_job(engine, f'JOB-H-DECIDED-{decision}')
        _hold_result(repo, job_id, 'reviewed result')
        _decide(engine, job_id, decision, reason='aggregate only' if decision == 'reject' else None)
        before = _held(engine, job_id)

        _hold_result(repo, job_id, 'new result')
        repo.complete_job_with_held_output(job_id, status='failed', kind='error', content='new error')

        assert _held(engine, job_id) == before
        job = _job(engine, job_id)
        assert job['release_status'] == final_status
        assert job['output_sha256'] == _sha256('reviewed result')

    @pytest.mark.parametrize('decision, final_status', [('approve', 'released'), ('reject', 'withheld')])
    def test_a_rerun_after_a_decision_keeps_the_reviewed_attestation_and_receipts(
        self, repo, engine, decision, final_status,
    ):
        job_id = _new_job(engine, f'JOB-H-RERUN-KEEP-{decision}')
        run_fields = ('attestation', 'verification_receipt', 'job_id_committed', 'researcher_nonce',
                      'commitment_receipt', 'ha_receipt', 'enclave_version', 'enclave_pcr0', 'error_message')
        repo.complete_job_with_held_output(
            job_id, status='success', kind='result', content='reviewed result',
            attested_output_hash=_sha256('reviewed result'), execution_metrics={'total': 1},
            **{field: f'reviewed {field}' for field in run_fields},
        )
        _decide(engine, job_id, decision, reason='aggregate only' if decision == 'reject' else None)
        before_job, before_held = _job(engine, job_id), _held(engine, job_id)

        # An operator reset the status, so the job runs again
        with engine.begin() as conn:
            conn.execute(text("UPDATE job_requests SET status = 'cloned' WHERE job_id = :job_id"), {'job_id': job_id})
        repo.complete_job_with_held_output(
            job_id, status='success', kind='result', content='unreviewed result',
            attested_output_hash=_sha256('unreviewed result'), execution_metrics={'total': 2},
            **{field: f'new {field}' for field in run_fields},
        )
        repo.complete_job_with_held_output(
            job_id, status='failed', kind='error', content='new error', error_message=HELD_ERROR_MESSAGE,
            attestation='error run attestation',
        )

        assert _held(engine, job_id) == before_held
        job = _job(engine, job_id)
        for field in run_fields + ('output_sha256', 'execution_metrics', 'release_status'):
            assert job[field] == before_job[field], field
        assert job['release_status'] == final_status
        assert job['status'] == 'success'

    def test_leaves_job_outputs_alone_for_a_job_released_before_the_gate(self, repo, engine):
        _hold_result(repo, 'JOB-OLD-SUCCESS', 'rerun result')

        assert _held(engine, 'JOB-OLD-SUCCESS') is None
        job = _job(engine, 'JOB-OLD-SUCCESS')
        assert job['release_status'] == 'released'
        assert job['output_sha256'] is None

    @pytest.mark.parametrize('field', ['execution_result', 'execution_output', 'release_status'])
    def test_refuses_fields_it_owns_or_must_never_write(self, repo, engine, field):
        job_id = _new_job(engine, f'JOB-H-REFUSE-{field}')
        with pytest.raises(ValueError):
            repo.complete_job_with_held_output(job_id, status='success', kind='result', content='x', **{field: 'x'})
        with pytest.raises(ValueError):
            repo.complete_job_with_held_output(job_id, status='success', kind='other', content='x')
        assert _held(engine, job_id) is None
        assert _job(engine, job_id)['status'] == 'executing'

    def test_a_not_required_job_that_gets_a_held_output_becomes_pending(self, repo, engine):
        job_id = _new_job(engine, 'JOB-H-NOT-REQUIRED')
        repo.update_job_status(job_id, 'failed', error_message='AI analysis crashed', release_status='not_required')

        _hold_result(repo, job_id, 'count=9\n')

        job = _job(engine, job_id)
        assert (job['status'], job['release_status']) == ('success', 'pending')
        assert _held(engine, job_id)['content'] == 'count=9\n'

    def test_a_held_result_clears_the_held_message_of_an_earlier_failed_run(self, repo, engine):
        rerun = _new_job(engine, 'JOB-H-RERUN')
        decided = _new_job(engine, 'JOB-H-RERUN-DECIDED')
        for job_id in (rerun, decided):
            repo.complete_job_with_held_output(
                job_id, status='failed', kind='error', content='first run failed', error_message=HELD_ERROR_MESSAGE,
            )
        _decide(engine, decided, 'reject', reason='row-level output')

        for job_id in (rerun, decided):
            _hold_result(repo, job_id, 'second run result')

        job = _job(engine, rerun)
        assert (job['status'], job['release_status'], job['error_message']) == ('success', 'pending', None)
        # Nothing is written once the release is decided, so the message stays with the held error.
        job = _job(engine, decided)
        assert (job['release_status'], job['error_message']) == ('withheld', HELD_ERROR_MESSAGE)
        assert _held(engine, decided)['kind'] == 'error'

    @pytest.mark.parametrize('failure', ['nul_byte', 'value_too_long'])
    def test_a_failing_held_write_does_not_carry_the_content_in_its_error(self, repo, engine, failure):
        # A NUL byte is refused by the driver before the INSERT runs; an over-long value
        # fails in Postgres with the content bound, where only hide_parameters keeps it out.
        job_id = _new_job(engine, f'JOB-H-HIDDEN-{failure}')
        content = f'patient_count=7 {MARKER}\n'
        if failure == 'nul_byte':
            fields = {'content': content + '\x00'}
        else:
            fields = {'content': content, 'attested_output_hash': 'f' * 65}

        with pytest.raises(Exception) as excinfo:
            repo.complete_job_with_held_output(job_id, status='success', kind='result', **fields)

        assert MARKER not in str(excinfo.value)
        assert _held(engine, job_id) is None
        assert _job(engine, job_id)['release_status'] is None


class TestAiDecisionIsAdvisory:
    def test_an_ai_rejected_job_is_executed_and_its_output_held_for_review(self, repo, engine):
        job_id = _new_job(engine, 'JOB-AI-REJECTED', status='cloned')
        repo.update_job_ai_results(
            job_id, ai_confidence_score=0.05, ai_reasoning='reads patient identifiers', ai_decision='ai_rejected',
        )

        fetched = repo.fetch_cloned_jobs_for_execution(limit=100)

        assert job_id in [job['job_id'] for job in fetched]
        job = _job(engine, job_id)
        assert (job['status'], job['ai_decision'], job['validation_status']) == ('executing', 'ai_rejected', '0.05')

        _hold_result(repo, job_id)
        assert _job(engine, job_id)['release_status'] == 'pending'


class TestUpdateJobFailed:
    """BaseWorker._update_job_failed (clone and AI-worker failures) against the real guards."""

    @pytest.fixture
    def failing_worker(self, repo):
        class CloneLikeWorker(BaseWorker):
            def fetch_jobs(self):
                return []

            def process_job(self, job):
                return False

        with patch('shared.base_worker.signal'):
            worker = CloneLikeWorker('CloneLikeWorker')
        worker._job_logger = MagicMock()
        with patch('shared.base_worker.job_repository', repo):
            yield worker

    def test_a_fresh_job_fails_without_a_release_review(self, failing_worker, engine):
        job_id = _new_job(engine, 'JOB-K1-FRESH', status='cloning')

        failing_worker._update_job_failed(job_id, 'git clone failed: repository not found')

        job = _job(engine, job_id)
        assert (job['status'], job['release_status']) == ('failed', 'not_required')
        assert job['error_message'] == 'git clone failed: repository not found'

    @pytest.mark.parametrize('decision', [None, 'approve'])
    def test_a_job_with_held_output_is_left_unchanged(self, failing_worker, repo, engine, decision):
        job_id = _new_job(engine, f'JOB-K1-HELD-{decision}')
        _hold_result(repo, job_id)
        if decision:
            _decide(engine, job_id, decision)
        before = _job(engine, job_id)

        failing_worker._update_job_failed(job_id, 'AI analysis crashed')

        assert _job(engine, job_id) == before
        failing_worker._job_logger.error.assert_not_called()


class TestUpdateJobStatus:
    def test_ai_decision_survives_the_executor_write_in_both_orders(self, repo, engine):
        ai_first = _new_job(engine, 'JOB-M-AI-FIRST')
        repo.update_job_ai_results(ai_first, ai_confidence_score=0.2, ai_decision='ai_rejected')
        repo.update_job_status(ai_first, 'success', execution_metrics={'total_ms': 12.5})

        executor_first = _new_job(engine, 'JOB-M-EXECUTOR-FIRST')
        repo.update_job_status(executor_first, 'success', execution_metrics={'total_ms': 3.0})
        repo.update_job_ai_results(executor_first, ai_confidence_score=0.9, ai_decision='ai_approved')

        held_write = _new_job(engine, 'JOB-M-HELD-WRITE')
        repo.update_job_ai_results(held_write, ai_decision='ai_rejected')
        repo.complete_job_with_held_output(
            held_write, status='success', kind='result', content='x', execution_metrics={'total_ms': 7.0},
        )

        for job_id, decision, total in ((ai_first, 'ai_rejected', 12.5),
                                        (executor_first, 'ai_approved', 3.0),
                                        (held_write, 'ai_rejected', 7.0)):
            job = _job(engine, job_id)
            assert json.loads(job['execution_metrics']) == {'ai_decision': decision, 'total_ms': total}
            assert job['ai_decision'] == decision

    def test_new_metric_keys_win_and_a_non_object_is_replaced(self, repo, engine):
        merged = _new_job(engine, 'JOB-M-MERGE', execution_metrics='{"total_ms": 1, "kept": true}')
        replaced = _new_job(engine, 'JOB-M-REPLACE', execution_metrics='not json {')

        repo.update_job_status(merged, 'success', execution_metrics={'total_ms': 2})
        repo.update_job_status(replaced, 'success', execution_metrics={'total_ms': 3})

        assert json.loads(_job(engine, merged)['execution_metrics']) == {'total_ms': 2, 'kept': True}
        assert json.loads(_job(engine, replaced)['execution_metrics']) == {'total_ms': 3}

    @pytest.mark.parametrize('field', ['execution_result', 'execution_output', 'execution_result_path', 'ai_result_path'])
    def test_refuses_to_write_execution_output(self, repo, engine, field):
        job_id = _new_job(engine, f'JOB-S-REFUSE-{field}')
        with pytest.raises(ValueError, match='execution_output'):
            repo.update_job_status(job_id, 'success', **{field: 'real-data output'})
        job = _job(engine, job_id)
        assert (job['status'], job['execution_output'], job['result_metadata']) == ('executing', None, None)

    def test_sets_release_status_directly(self, repo, engine):
        job_id = _new_job(engine, 'JOB-S-NOT-REQUIRED')

        returned = repo.update_job_status(
            job_id, 'failed', error_message='Repository not found', release_status='not_required',
        )

        job = _job(engine, job_id)
        assert (job['status'], job['release_status']) == ('failed', 'not_required')
        assert job['error_message'] == 'Repository not found'
        assert returned['release_status'] == 'not_required'


class TestUpdateJobAiResults:
    def test_sets_the_ai_decision_column(self, repo, engine):
        job_id = _new_job(engine, 'JOB-AI-COLUMN')

        repo.update_job_ai_results(job_id, ai_confidence_score=0.9, ai_reasoning='looks fine', ai_decision='ai_approved')

        job = _job(engine, job_id)
        assert job['ai_decision'] == 'ai_approved'
        assert job['validation_status'] == '0.9'
        assert json.loads(job['execution_metrics']) == {'ai_decision': 'ai_approved'}
        assert job['release_status'] is None
        assert repo.get_job(job_id)['ai_decision'] == 'ai_approved'


def _refresh(engine, reviewer_id):
    with engine.begin() as conn:
        conn.execute(text('UPDATE reviewer_projects SET synced_at = now() WHERE user_id = :user_id'),
                     {'user_id': reviewer_id})


def _expect_refused(engine, code, job_id, reviewer_id=BOB, decision='approve', sha=None, reason=None):
    held = _held(engine, job_id)
    _expect_sqlstate(
        engine, code, INSERT_DECISION, job_id=job_id, decision=decision,
        sha=sha or held['content_sha256'], reviewer=reviewer_id, reason=reason,
    )
    assert _rows(engine, 'SELECT id FROM release_decisions WHERE job_id = :job_id', job_id=job_id) == []
    assert _held(engine, job_id) == held


class TestProjectRouting:
    """The decision guard checks added by c5e2a8d4b7f1."""

    def test_the_decision_records_the_job_project(self, repo, engine):
        job_id = _new_job(engine, 'JOB-P-STAMP')
        _hold_result(repo, job_id)
        _refresh(engine, BOB)

        with engine.begin() as conn:
            # A project named by the caller is replaced by the job's own
            conn.execute(
                text('INSERT INTO release_decisions (job_id, decision, output_sha256, reviewer_id, project_id) '
                     'VALUES (:job_id, :decision, :sha, :reviewer, :project_id)'),
                {'job_id': job_id, 'decision': 'approve', 'sha': _sha256('count=7\n'), 'reviewer': BOB,
                 'project_id': OTHER_PROJECT},
            )

        assert _rows(engine, 'SELECT project_id::text FROM release_decisions WHERE job_id = :job_id',
                     job_id=job_id) == [{'project_id': PROJECT}]

    @pytest.mark.parametrize('reviewer_id', [DAVE, ERIN, CAROL, FRANK],
                             ids=['pending-reviewer', 'rejected-reviewer', 'staff', 'researcher'])
    def test_only_an_approved_reviewer_may_decide(self, repo, engine, reviewer_id):
        job_id = _new_job(engine, f'JOB-P-RG002-{reviewer_id[-2:]}')
        _hold_result(repo, job_id)
        # Assigned and confirmed: only the account itself is refused
        _assign(engine, reviewer_id, PROJECT)

        _expect_refused(engine, 'RG002', job_id, reviewer_id)
        _expect_refused(engine, 'RG002', job_id, reviewer_id, decision='reject', reason='row-level output')

    def test_a_reviewer_demoted_to_researcher_may_not_decide(self, repo, engine):
        job_id = _new_job(engine, 'JOB-P-DEMOTED')
        _hold_result(repo, job_id)
        _assign(engine, GRACE, PROJECT)
        with engine.begin() as conn:
            conn.execute(text("UPDATE users SET role = 'user' WHERE id = :id"), {'id': GRACE})
        try:
            _expect_refused(engine, 'RG002', job_id, GRACE)
        finally:
            with engine.begin() as conn:
                conn.execute(text("UPDATE users SET role = 'reviewer' WHERE id = :id"), {'id': GRACE})
            _unassign_all(engine, GRACE)

    def test_a_job_without_a_project_cannot_be_decided(self, repo, engine):
        job_id = _new_job(engine, 'JOB-P-NO-PROJECT', project_id=None, archetype_id=None)
        _hold_result(repo, job_id)
        _refresh(engine, BOB)

        _expect_refused(engine, 'RG006', job_id)
        _expect_refused(engine, 'RG006', job_id, decision='reject', reason='row-level output')
        assert _job(engine, job_id)['release_status'] == 'pending'

    def test_a_reviewer_of_another_project_cannot_decide(self, repo, engine):
        job_id = _new_job(engine, 'JOB-P-OTHER-PROJECT', project_id=OTHER_PROJECT)
        _hold_result(repo, job_id)
        _refresh(engine, BOB)  # bob reviews PROJECT only

        _expect_refused(engine, 'RG007', job_id)
        _expect_refused(engine, 'RG007', job_id, decision='reject', reason='row-level output')

    def test_an_assignment_must_be_confirmed_in_the_last_5_minutes(self, repo, engine):
        job_id = _new_job(engine, 'JOB-P-STALE')
        _hold_result(repo, job_id)
        _assign(engine, GRACE, PROJECT, minutes_ago=6)
        try:
            _expect_refused(engine, 'RG008', job_id, GRACE)
            _expect_refused(engine, 'RG008', job_id, GRACE, decision='reject', reason='row-level output')

            _assign(engine, GRACE, PROJECT, minutes_ago=4)
            with engine.begin() as conn:
                conn.execute(text(INSERT_DECISION), {'job_id': job_id, 'decision': 'approve',
                                                     'sha': _sha256('count=7\n'), 'reviewer': GRACE, 'reason': None})
            assert _rows(engine, 'SELECT reviewer_id::text FROM release_decisions WHERE job_id = :job_id',
                         job_id=job_id) == [{'reviewer_id': GRACE}]
        finally:
            _unassign_all(engine, GRACE)

    @pytest.mark.parametrize('attested', [False, 'b' * 64], ids=['not-attested', 'different-attested-hash'])
    def test_a_result_is_approved_only_when_it_matches_the_attested_hash(self, repo, engine, attested):
        job_id = _new_job(engine, f'JOB-P-RG009-{bool(attested)}')
        _hold_result(repo, job_id, attested=attested)
        _refresh(engine, BOB)

        _expect_refused(engine, 'RG009', job_id)

        # It can still be withheld
        _decide(engine, job_id, 'reject', reason='output does not match its attestation')
        assert _job(engine, job_id)['release_status'] == 'withheld'

    def test_held_error_text_may_be_approved_without_an_attestation(self, repo, engine):
        job_id = _new_job(engine, 'JOB-P-ERROR-TEXT')
        repo.complete_job_with_held_output(
            job_id, status='failed', kind='error', content='Traceback: KeyError', error_message=HELD_ERROR_MESSAGE,
        )

        _decide(engine, job_id, 'approve')

        assert _job(engine, job_id)['release_status'] == 'released'

    def test_a_dev_database_may_allow_unattested_release(self, repo, engine):
        allowed = _new_job(engine, 'JOB-P-GUC-ON')
        refused = _new_job(engine, 'JOB-P-GUC-OFF')
        for job_id in (allowed, refused):
            _hold_result(repo, job_id, attested=False)
        _refresh(engine, BOB)

        with engine.begin() as conn:
            conn.execute(text("SET LOCAL epsilon.allow_unattested_release = 'on'"))
            conn.execute(text(INSERT_DECISION), {'job_id': allowed, 'decision': 'approve',
                                                 'sha': _sha256('count=7\n'), 'reviewer': BOB, 'reason': None})

        assert _rows(engine, 'SELECT decision FROM release_decisions WHERE job_id = :job_id', job_id=allowed) == [
            {'decision': 'approve'}
        ]
        # SET LOCAL ended with its transaction
        _expect_refused(engine, 'RG009', refused)


class TestDecisionCheckOrder:
    """Where two checks fail at once, the earlier one in the trigger is reported."""

    def test_own_job_before_the_account(self, repo, engine):
        job_id = _new_job(engine, 'JOB-O-OWN', user_id=DAVE)
        _hold_result(repo, job_id)

        _expect_refused(engine, 'RG001', job_id, DAVE)

    def test_the_account_before_the_release_status(self, engine):
        job_id = _new_job(engine, 'JOB-O-ACCOUNT')

        _expect_sqlstate(engine, 'RG002', INSERT_DECISION, job_id=job_id, decision='approve', sha='a' * 64,
                         reviewer=CAROL, reason=None)

    def test_the_release_status_before_the_project(self, engine):
        job_id = _new_job(engine, 'JOB-O-STATUS', project_id=None, archetype_id=None)

        _expect_sqlstate(engine, 'RG003', INSERT_DECISION, job_id=job_id, decision='approve', sha='a' * 64,
                         reviewer=BOB, reason=None)

    def test_the_project_before_the_assignment(self, repo, engine):
        job_id = _new_job(engine, 'JOB-O-PROJECT', project_id=None, archetype_id=None)
        _hold_result(repo, job_id)

        _expect_refused(engine, 'RG006', job_id, GRACE)  # grace has no assignment at all

    def test_the_assignment_age_before_the_output_hash(self, repo, engine):
        job_id = _new_job(engine, 'JOB-O-STALE-HASH')
        _hold_result(repo, job_id)
        _assign(engine, GRACE, PROJECT, minutes_ago=6)
        try:
            _expect_refused(engine, 'RG008', job_id, GRACE, sha='a' * 64)
        finally:
            _unassign_all(engine, GRACE)

    def test_the_output_hash_before_the_attestation(self, repo, engine):
        job_id = _new_job(engine, 'JOB-O-HASH-ATTESTATION')
        _hold_result(repo, job_id, attested=False)
        _refresh(engine, BOB)

        _expect_refused(engine, 'RG004', job_id, sha='a' * 64)


class TestProjectIsFixed:
    def test_a_recorded_project_or_archetype_cannot_change(self, engine):
        job_id = _new_job(engine, 'JOB-F-CHANGE')

        for sql in ('UPDATE job_requests SET project_id = :other WHERE job_id = :job_id',
                    'UPDATE job_requests SET project_id = NULL WHERE job_id = :job_id',
                    "UPDATE job_requests SET archetype_id = 'other-archetype' WHERE job_id = :job_id",
                    'UPDATE job_requests SET archetype_id = NULL WHERE job_id = :job_id'):
            _expect_sqlstate(engine, 'RG025', sql, job_id=job_id, other=OTHER_PROJECT)

        # The same values again are no change
        with engine.begin() as conn:
            conn.execute(text('UPDATE job_requests SET project_id = :project, archetype_id = :archetype '
                              'WHERE job_id = :job_id'),
                         {'project': PROJECT.upper(), 'archetype': ARCHETYPE, 'job_id': job_id})
        job = _job(engine, job_id)
        assert (str(job['project_id']), job['archetype_id']) == (PROJECT, ARCHETYPE)

    def test_a_missing_project_may_be_set_once(self, engine):
        job_id = _new_job(engine, 'JOB-F-SET-ONCE', project_id=None, archetype_id=None)

        with engine.begin() as conn:
            conn.execute(text('UPDATE job_requests SET project_id = :project, archetype_id = :archetype '
                              'WHERE job_id = :job_id'),
                         {'project': PROJECT, 'archetype': ARCHETYPE, 'job_id': job_id})

        job = _job(engine, job_id)
        assert (str(job['project_id']), job['archetype_id']) == (PROJECT, ARCHETYPE)
        _expect_sqlstate(engine, 'RG025', 'UPDATE job_requests SET project_id = :other WHERE job_id = :job_id',
                         job_id=job_id, other=OTHER_PROJECT)


class TestRecordJobProject:
    def test_records_the_project_and_archetype_and_a_rerun_records_them_again(self, repo, engine):
        job_id = _new_job(engine, 'JOB-R-RECORD', project_id=None, archetype_id=None)

        repo.record_job_project(job_id, PROJECT, ARCHETYPE)
        repo.record_job_project(job_id, PROJECT, ARCHETYPE)

        job = _job(engine, job_id)
        assert (str(job['project_id']), job['archetype_id']) == (PROJECT, ARCHETYPE)
        assert repo.get_job(job_id)['project_id'] == PROJECT
        assert repo.get_job(job_id)['archetype_id'] == ARCHETYPE

    @pytest.mark.parametrize('project_id, archetype_id', [(OTHER_PROJECT, ARCHETYPE), (PROJECT, 'other-archetype')],
                             ids=['project', 'archetype'])
    def test_a_rerun_naming_another_project_or_archetype_is_refused(self, repo, engine, project_id, archetype_id):
        job_id = _new_job(engine, f'JOB-R-CHANGE-{archetype_id}-{project_id[:4]}')

        with pytest.raises(ProjectChangedError):
            repo.record_job_project(job_id, project_id, archetype_id)

        job = _job(engine, job_id)
        assert (str(job['project_id']), job['archetype_id']) == (PROJECT, ARCHETYPE)

    def test_an_unknown_job_is_an_error(self, repo):
        with pytest.raises(ValueError, match='not found'):
            repo.record_job_project('JOB-R-MISSING', PROJECT, ARCHETYPE)


class TestGetSubmitterSub:
    def test_returns_the_external_id_of_the_job_submitter(self, repo, engine):
        job_id = _new_job(engine, 'JOB-U-SUB')
        legacy = _new_job(engine, 'JOB-U-LEGACY', user_id=FRANK)

        assert repo.get_submitter_sub(job_id) == ALICE_SUB
        assert repo.get_submitter_sub(legacy) == 'ext-f'

    def test_an_unknown_job_has_no_submitter(self, repo):
        assert repo.get_submitter_sub('JOB-U-MISSING') is None


class TestDowngrade:
    def test_refuses_while_output_is_held_and_round_trips_when_empty(self):
        with _fresh_database() as url:
            _alembic(url, command.stamp, BASE_REVISION)
            _alembic(url, command.upgrade, 'head')
            database = Database(url)
            try:
                repo, engine = JobRepository(database), database.engine
                _hold_result(repo, 'JOB-INFLIGHT')

                # One transaction: the project revision's downgrade is rolled back too
                with pytest.raises(DBAPIError, match='refusing to downgrade'):
                    _alembic(url, command.downgrade, BASE_REVISION)
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': HEAD_REVISION}]
                assert _held(engine, 'JOB-INFLIGHT') is not None

                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM job_outputs WHERE job_id = 'JOB-INFLIGHT'"))
                _alembic(url, command.downgrade, BASE_REVISION)
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': BASE_REVISION}]
                assert _rows(
                    engine,
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name IN ('job_outputs', 'release_decisions', 'reviewer_projects')",
                ) == []
                assert _rows(
                    engine,
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'job_requests' "
                    "AND column_name IN ('release_status', 'ai_decision', 'output_sha256', 'project_id', 'archetype_id')",
                ) == []

                _alembic(url, command.upgrade, 'head')
                assert _rows(engine, 'SELECT version_num FROM alembic_version') == [{'version_num': HEAD_REVISION}]
                # JOB-INFLIGHT finished in between, so the re-run backfill releases it.
                assert _job(engine, 'JOB-INFLIGHT')['release_status'] == 'released'
                assert _job(engine, 'JOB-QUEUED')['release_status'] is None
            finally:
                database.close()
