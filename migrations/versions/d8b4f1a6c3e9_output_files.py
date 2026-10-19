"""output files: files a real-data run writes, held with its output for review

The enclave returns the files a script wrote to EPSILON_OUTPUT_DIR with a
manifest (name, size, sha256, type per file) whose SHA-256 it binds into the
attestation user_data. The coordinator stores each file's bytes outside the
database (S3 or a local directory, under jobs/<job_id>/<sha256>) and holds
its metadata here, next to the held text:

- job_outputs.files_manifest_sha256: SHA-256 of the canonical manifest;
  NULL for output held before this revision or from an enclave without files
- job_output_files: one row per file
- release_decisions.files_manifest_sha256: the manifest the reviewer saw

The decision trigger also raises RG004 when the decision's manifest hash is
not the held one. Once a decision exists, the job's file rows and its
manifest hash are fixed (RG010). Existing rows get a NULL manifest hash,
which a decision without files (NULL) still matches.

Deploy: stop the executor, run `alembic upgrade head`, deploy the coordinator
(with OUTPUT_FILES_STORE and its settings) and ResearchWorkspace, then start
the executor. Downgrade refuses while job_output_files holds any row.

Revision ID: d8b4f1a6c3e9
Revises: c5e2a8d4b7f1
Create Date: 2026-10-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8b4f1a6c3e9'
down_revision: Union[str, Sequence[str], None] = 'c5e2a8d4b7f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_STATEMENTS = [
    # 1. Manifest hash of the held files, and the files themselves
    r"""ALTER TABLE job_outputs ADD COLUMN files_manifest_sha256 varchar(64)
    CHECK (files_manifest_sha256 IS NULL OR files_manifest_sha256 ~ '^[0-9a-f]{64}$')""",
    r"""CREATE TABLE job_output_files (
    job_id       varchar(255) NOT NULL REFERENCES job_outputs (job_id) ON DELETE CASCADE,
    name         varchar(100) NOT NULL CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'),
    size         integer      NOT NULL CHECK (size >= 0),
    content_type varchar(64)  NOT NULL,
    sha256       varchar(64)  NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    storage_key  text         NOT NULL,
    created_at   timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, name)
)""",

    # 2. The manifest the reviewer saw
    "ALTER TABLE release_decisions ADD COLUMN files_manifest_sha256 varchar(64)",

    # 3. Decision guard: every c5e2a8d4b7f1 check, and RG004 also when the manifest hash differs
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
    v_files_manifest  varchar;
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

    SELECT kind, content_sha256, attested_output_hash, files_manifest_sha256
      INTO v_kind, v_content_sha, v_attested, v_files_manifest
      FROM job_outputs WHERE job_id = NEW.job_id FOR SHARE;
    IF v_content_sha IS NULL OR v_content_sha <> NEW.output_sha256
       OR NEW.files_manifest_sha256 IS DISTINCT FROM v_files_manifest THEN
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

    # 4. Held files and their manifest hash are fixed once a decision exists (RG010).
    # Deletes cascaded from removing the job are allowed (pg_trigger_depth() > 1).
    r"""CREATE FUNCTION job_output_files_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
        RETURN OLD;
    END IF;
    IF EXISTS (SELECT 1 FROM release_decisions d WHERE d.job_id IN (OLD.job_id, NEW.job_id)) THEN
        RAISE EXCEPTION 'held files for job % are fixed after a release decision', coalesce(OLD.job_id, NEW.job_id)
              USING ERRCODE = 'RG010';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END $$""",
    r"""CREATE TRIGGER job_output_files_guard
    BEFORE INSERT OR UPDATE OR DELETE ON job_output_files
    FOR EACH ROW EXECUTE FUNCTION job_output_files_guard()""",

    # Named to fire before job_outputs_guard (RG011), which refuses any update after a decision
    r"""CREATE FUNCTION job_outputs_files_manifest_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.files_manifest_sha256 IS DISTINCT FROM OLD.files_manifest_sha256
       AND EXISTS (SELECT 1 FROM release_decisions d WHERE d.job_id = OLD.job_id) THEN
        RAISE EXCEPTION 'the files manifest of job % is fixed after a release decision', OLD.job_id
              USING ERRCODE = 'RG010';
    END IF;
    RETURN NEW;
END $$""",
    r"""CREATE TRIGGER job_outputs_files_manifest_guard
    BEFORE UPDATE OF files_manifest_sha256 ON job_outputs
    FOR EACH ROW EXECUTE FUNCTION job_outputs_files_manifest_guard()""",
]

# The decision guard exactly as revision c5e2a8d4b7f1 created it
PREVIOUS_DECISION_GUARD = r"""CREATE OR REPLACE FUNCTION release_decisions_before_insert() RETURNS trigger
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
END $$"""

DOWNGRADE_STATEMENTS = [
    # File rows would be lost while their bytes stay in the store.
    r"""DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM job_output_files) THEN
        RAISE EXCEPTION 'refusing to downgrade: job_output_files holds % row(s); export and clear them first',
              (SELECT count(*) FROM job_output_files);
    END IF;
END $$""",
    PREVIOUS_DECISION_GUARD,
    "DROP TRIGGER IF EXISTS job_outputs_files_manifest_guard ON job_outputs",
    "DROP FUNCTION IF EXISTS job_outputs_files_manifest_guard()",
    "DROP TABLE IF EXISTS job_output_files",
    "DROP FUNCTION IF EXISTS job_output_files_guard()",
    "ALTER TABLE release_decisions DROP COLUMN IF EXISTS files_manifest_sha256",
    "ALTER TABLE job_outputs DROP COLUMN IF EXISTS files_manifest_sha256",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(sa.text(statement))
