"""project reviewers: route held output to the reviewers of the job's platform project

Each job uses exactly one platform project; build.yml calls its id `dataset_id`.
The coordinator records it in job_requests.project_id (with archetype_id) after
build validation, before any real data is requested. Reviewers are granted in
Keycloak (realm role output-reviewer, groups "Reviewers of <project id>");
ResearchWorkspace mirrors each reviewer's projects into reviewer_projects at
login, on its periodic re-check and right before every decision.

The decision trigger gains these checks (SQLSTATE class RG):
  RG002 the reviewer must also be approved
  RG006 the job has no project, so it cannot be routed
  RG007 the reviewer is not assigned to the job's project
  RG008 the reviewer's assignment was not confirmed in the last 5 minutes
  RG009 a result is approved only when its hash equals the attested output hash.
        Dev databases without attestation may set
        `ALTER DATABASE ... SET epsilon.allow_unattested_release = on`.
and stamps release_decisions.project_id. RG025 fixes project_id and
archetype_id once they are set.

Deploy together with 7f3a9c2d1e4b, in the same window:
1. Deploy the platform realm (role output-reviewer, group output-reviewers,
   the epsilon_groups mapper) and the API.
2. In Keycloak, create "Reviewers of <project id>" for each project and invite
   the reviewers.
3. Disable the coordinator auto-start (EventBridge rule) and stop the executor.
4. Run `alembic upgrade head` as described in 7f3a9c2d1e4b.
5. Set EPSILON_API_URL, EPSILON_COORDINATOR_CLIENT_ID and
   EPSILON_COORDINATOR_CLIENT_SECRET for the coordinator, then deploy the
   coordinator, ResearchWorkspace and the trust center.
6. Start the executor and re-enable auto-start.

Revision ID: c5e2a8d4b7f1
Revises: 7f3a9c2d1e4b
Create Date: 2026-10-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5e2a8d4b7f1'
down_revision: Union[str, Sequence[str], None] = '7f3a9c2d1e4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_STATEMENTS = [
    # 1. The job's project (build.yml datasets[0].dataset_id) and archetype
    "ALTER TABLE job_requests ADD COLUMN project_id uuid",
    "ALTER TABLE job_requests ADD COLUMN archetype_id varchar(255)",
    "CREATE INDEX ix_job_requests_project_release ON job_requests (project_id, release_status)",

    # 2. Mirror of the Keycloak groups "Reviewers of <project id>", written by ResearchWorkspace
    r"""CREATE TABLE reviewer_projects (
    user_id    uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    project_id uuid        NOT NULL,
    synced_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, project_id)
)""",
    "CREATE INDEX ix_reviewer_projects_project ON reviewer_projects (project_id)",

    # 3. Each decision records the project it was made for (stamped by the trigger)
    "ALTER TABLE release_decisions ADD COLUMN project_id uuid",

    # 4. Decision guard. SQLSTATEs, in the order they are checked:
    #   RG005 job not found   RG001 own job   RG002 not an approved reviewer   RG003 job not pending
    #   RG006 job has no project   RG007 not this project's reviewer   RG008 assignment not confirmed recently
    #   RG004 hash mismatch / no held output   RG009 result does not match the attested hash
    r"""CREATE OR REPLACE FUNCTION release_decisions_before_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_job_user        uuid;
    v_ws_user         uuid;
    v_release_status  varchar;
    v_project         uuid;
    v_reviewer_role   varchar;
    v_reviewer_status varchar;
    v_synced_at       timestamptz;
    v_kind            varchar;
    v_content_sha     varchar;
    v_attested        varchar;
BEGIN
    SELECT jr.user_id, w.user_id, jr.release_status, jr.project_id
      INTO v_job_user, v_ws_user, v_release_status, v_project
      FROM job_requests jr
      JOIN workspaces w ON w.id = jr.workspace_id
     WHERE jr.job_id = NEW.job_id
       FOR UPDATE OF jr;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'release decision: job % not found', NEW.job_id USING ERRCODE = 'RG005';
    END IF;

    IF NEW.reviewer_id = v_job_user OR NEW.reviewer_id = v_ws_user THEN
        RAISE EXCEPTION 'release decision: a reviewer cannot decide on their own job' USING ERRCODE = 'RG001';
    END IF;

    SELECT role, approval_status INTO v_reviewer_role, v_reviewer_status FROM users WHERE id = NEW.reviewer_id;
    IF v_reviewer_role IS DISTINCT FROM 'reviewer' OR v_reviewer_status IS DISTINCT FROM 'approved' THEN
        RAISE EXCEPTION 'release decision: user % is not an approved reviewer', NEW.reviewer_id USING ERRCODE = 'RG002';
    END IF;

    IF v_release_status IS DISTINCT FROM 'pending' THEN
        RAISE EXCEPTION 'release decision: job % is not pending release (%)', NEW.job_id, coalesce(v_release_status, 'null')
              USING ERRCODE = 'RG003';
    END IF;

    IF v_project IS NULL THEN
        RAISE EXCEPTION 'release decision: job % has no project, so it cannot be routed to a reviewer', NEW.job_id
              USING ERRCODE = 'RG006';
    END IF;

    SELECT synced_at INTO v_synced_at
      FROM reviewer_projects
     WHERE user_id = NEW.reviewer_id AND project_id = v_project
       FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'release decision: user % is not a reviewer of project %', NEW.reviewer_id, v_project
              USING ERRCODE = 'RG007';
    END IF;
    IF v_synced_at < now() - interval '5 minutes' THEN
        RAISE EXCEPTION 'release decision: the reviewer assignment of user % was not confirmed in the last 5 minutes', NEW.reviewer_id
              USING ERRCODE = 'RG008';
    END IF;

    SELECT kind, content_sha256, attested_output_hash INTO v_kind, v_content_sha, v_attested
      FROM job_outputs WHERE job_id = NEW.job_id FOR SHARE;
    IF v_content_sha IS NULL OR v_content_sha <> NEW.output_sha256 THEN
        RAISE EXCEPTION 'release decision: output hash does not match the held output' USING ERRCODE = 'RG004';
    END IF;

    IF NEW.decision = 'approve' AND v_kind = 'result'
       AND (v_attested IS NULL OR v_attested <> v_content_sha)
       AND coalesce(current_setting('epsilon.allow_unattested_release', true), 'off') <> 'on' THEN
        RAISE EXCEPTION 'release decision: the held result of job % does not match its attested output hash', NEW.job_id
              USING ERRCODE = 'RG009';
    END IF;

    NEW.project_id := v_project;
    RETURN NEW;
END $$""",

    # 5. A recorded project or archetype cannot change (a NULL may be set once)
    r"""CREATE FUNCTION job_requests_project_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.project_id IS NOT NULL AND NEW.project_id IS DISTINCT FROM OLD.project_id)
       OR (OLD.archetype_id IS NOT NULL AND NEW.archetype_id IS DISTINCT FROM OLD.archetype_id) THEN
        RAISE EXCEPTION 'job %: the recorded project cannot change', OLD.job_id USING ERRCODE = 'RG025';
    END IF;
    RETURN NEW;
END $$""",
    r"""CREATE TRIGGER job_requests_project_guard
    BEFORE UPDATE OF project_id, archetype_id ON job_requests
    FOR EACH ROW EXECUTE FUNCTION job_requests_project_guard()""",
]

# The decision guard exactly as revision 7f3a9c2d1e4b created it
PREVIOUS_DECISION_GUARD = r"""CREATE OR REPLACE FUNCTION release_decisions_before_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_job_user       uuid;
    v_ws_user        uuid;
    v_release_status varchar;
    v_reviewer_role  varchar;
    v_content_sha    varchar;
BEGIN
    SELECT jr.user_id, w.user_id, jr.release_status
      INTO v_job_user, v_ws_user, v_release_status
      FROM job_requests jr
      JOIN workspaces w ON w.id = jr.workspace_id
     WHERE jr.job_id = NEW.job_id
       FOR UPDATE OF jr;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'release decision: job % not found', NEW.job_id USING ERRCODE = 'RG005';
    END IF;

    IF NEW.reviewer_id = v_job_user OR NEW.reviewer_id = v_ws_user THEN
        RAISE EXCEPTION 'release decision: a reviewer cannot decide on their own job' USING ERRCODE = 'RG001';
    END IF;

    SELECT role INTO v_reviewer_role FROM users WHERE id = NEW.reviewer_id;
    IF v_reviewer_role IS DISTINCT FROM 'reviewer' THEN
        RAISE EXCEPTION 'release decision: user % is not a reviewer', NEW.reviewer_id USING ERRCODE = 'RG002';
    END IF;

    IF v_release_status IS DISTINCT FROM 'pending' THEN
        RAISE EXCEPTION 'release decision: job % is not pending release (%)', NEW.job_id, coalesce(v_release_status, 'null')
              USING ERRCODE = 'RG003';
    END IF;

    SELECT content_sha256 INTO v_content_sha FROM job_outputs WHERE job_id = NEW.job_id FOR SHARE;
    IF v_content_sha IS NULL OR v_content_sha <> NEW.output_sha256 THEN
        RAISE EXCEPTION 'release decision: output hash does not match the held output' USING ERRCODE = 'RG004';
    END IF;

    RETURN NEW;
END $$"""

DOWNGRADE_STATEMENTS = [
    PREVIOUS_DECISION_GUARD,
    "DROP TRIGGER IF EXISTS job_requests_project_guard ON job_requests",
    "DROP FUNCTION IF EXISTS job_requests_project_guard()",
    "ALTER TABLE release_decisions DROP COLUMN IF EXISTS project_id",
    "DROP TABLE IF EXISTS reviewer_projects",
    "DROP INDEX IF EXISTS ix_job_requests_project_release",
    "ALTER TABLE job_requests DROP COLUMN IF EXISTS archetype_id",
    "ALTER TABLE job_requests DROP COLUMN IF EXISTS project_id",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(sa.text(statement))
