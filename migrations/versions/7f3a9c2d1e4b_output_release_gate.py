"""output release gate: held outputs, release decisions and release_status

Real-data output (a result, or the error text of a failed run) is held in
job_outputs until a reviewer records a decision in release_decisions.
job_requests.release_status tracks each job:

- NULL: not finished yet
- pending: finished, its real-data text is held in job_outputs
- released / withheld: a reviewer approved / rejected (final)
- not_required: finished without real-data text (build validation,
  failure before real data was requested)

Jobs that finished before this revision are backfilled as released, and the
advisory AI decision is copied out of execution_metrics into ai_decision.
Guard triggers (SQLSTATE class RG) enforce the transitions, block
self-review and keep decisions append-only. The SQL is the validated
release-gate contract, applied verbatim; the backfill runs before the
triggers exist.

Deploy order:
1. Disable the coordinator auto-start (EventBridge rule) and stop the executor.
2. As the owner of job_requests (rw_app on RDS) run `alembic current`. If it shows no revision
   (production has no alembic_version table), check that job_requests has job_id_committed,
   researcher_nonce, commitment_receipt and ha_receipt, then run `alembic stamp 2e1ea7116ba4`
   (never `stamp head`). Then run `alembic upgrade head`.
3. Deploy the coordinator, ResearchWorkspace and the trust center.
4. Start the executor and re-enable auto-start.

Downgrade refuses while job_outputs holds any row.

Revision ID: 7f3a9c2d1e4b
Revises: 2e1ea7116ba4
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7f3a9c2d1e4b'
down_revision: Union[str, Sequence[str], None] = '2e1ea7116ba4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_STATEMENTS = [
    # 1. New job_requests columns
    "ALTER TABLE job_requests ADD COLUMN release_status varchar(20)",
    "ALTER TABLE job_requests ADD COLUMN ai_decision varchar(32)",
    "ALTER TABLE job_requests ADD COLUMN output_sha256 varchar(64)",
    r"""ALTER TABLE job_requests ADD CONSTRAINT job_requests_release_status_check
    CHECK (release_status IS NULL OR release_status IN ('pending', 'released', 'withheld', 'not_required'))""",
    r"""ALTER TABLE job_requests ADD CONSTRAINT job_requests_output_sha256_check
    CHECK (output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$')""",
    "CREATE INDEX ix_job_requests_release_status ON job_requests (release_status)",

    # 2. Backfill, before the guard triggers exist
    r"""UPDATE job_requests
   SET release_status = 'released'
 WHERE release_status IS NULL
   AND status NOT IN ('pending', 'queued', 'cloning', 'cloned', 'executing',
                      'running', 'ai_analyzing', 'ai_approved', 'validating')""",
    r"""UPDATE job_requests
   SET ai_decision = substring(execution_metrics from '"ai_decision"\s*:\s*"([A-Za-z_]+)"')
 WHERE ai_decision IS NULL
   AND execution_metrics LIKE '%"ai_decision"%'""",

    # 3. Held output
    r"""CREATE TABLE job_outputs (
    job_id               varchar(255) PRIMARY KEY
                         REFERENCES job_requests (job_id) ON DELETE CASCADE,
    kind                 varchar(10)  NOT NULL CHECK (kind IN ('result', 'error')),
    content              text         NOT NULL,
    content_sha256       varchar(64)  NOT NULL,
    attested_output_hash varchar(64)  CHECK (attested_output_hash IS NULL OR attested_output_hash ~ '^[0-9a-f]{64}$'),
    created_at           timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT job_outputs_content_sha256_check
        CHECK (content_sha256 = encode(sha256(convert_to(content, 'UTF8')), 'hex'))
)""",

    # 4. Release decisions (append-only, at most one per job)
    r"""CREATE TABLE release_decisions (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id        varchar(255) NOT NULL UNIQUE
                  REFERENCES job_requests (job_id) ON DELETE CASCADE,
    decision      varchar(10) NOT NULL CHECK (decision IN ('approve', 'reject')),
    output_sha256 varchar(64) NOT NULL CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
    reviewer_id   uuid        NOT NULL REFERENCES users (id),
    reason        text,
    decided_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT release_decisions_reason_required
        CHECK (decision = 'approve' OR (reason IS NOT NULL AND length(btrim(reason)) > 0))
)""",

    # 5. Guards. SQLSTATEs:
    #   RG001 own job   RG002 not a reviewer   RG003 job not pending   RG004 hash mismatch / no held output
    #   RG005 job not found
    #   RG010 decisions are append-only   RG011 held output fixed after decision
    #   RG020 final status changed   RG021 released without approve   RG022 withheld without reject
    #   RG023 pending moved elsewhere   RG024 inserted with a final status
    r"""CREATE FUNCTION release_decisions_before_insert() RETURNS trigger
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
END $$""",
    r"""CREATE TRIGGER release_decisions_before_insert
    BEFORE INSERT ON release_decisions
    FOR EACH ROW EXECUTE FUNCTION release_decisions_before_insert()""",

    # Direct deletes are refused; deletes cascaded from removing the job (or its
    # user/workspace) are allowed (pg_trigger_depth() > 1).
    r"""CREATE FUNCTION release_decisions_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'release decisions are append-only' USING ERRCODE = 'RG010';
END $$""",
    r"""CREATE TRIGGER release_decisions_immutable
    BEFORE UPDATE OR DELETE ON release_decisions
    FOR EACH ROW EXECUTE FUNCTION release_decisions_immutable()""",

    r"""CREATE FUNCTION job_outputs_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
        RETURN OLD;
    END IF;
    IF EXISTS (SELECT 1 FROM release_decisions d WHERE d.job_id = OLD.job_id) THEN
        RAISE EXCEPTION 'held output for job % is fixed after a release decision', OLD.job_id USING ERRCODE = 'RG011';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END $$""",
    r"""CREATE TRIGGER job_outputs_guard
    BEFORE UPDATE OR DELETE ON job_outputs
    FOR EACH ROW EXECUTE FUNCTION job_outputs_guard()""",

    r"""CREATE FUNCTION job_requests_release_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.release_status IN ('released', 'withheld') THEN
            RAISE EXCEPTION 'job %: cannot be created with release status %', NEW.job_id, NEW.release_status
                  USING ERRCODE = 'RG024';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.release_status IS NOT DISTINCT FROM OLD.release_status THEN
        RETURN NEW;
    END IF;
    IF OLD.release_status IN ('released', 'withheld') THEN
        RAISE EXCEPTION 'job %: release status % is final', OLD.job_id, OLD.release_status USING ERRCODE = 'RG020';
    END IF;
    IF OLD.release_status = 'pending' AND NEW.release_status IS DISTINCT FROM 'released'
                                      AND NEW.release_status IS DISTINCT FROM 'withheld' THEN
        RAISE EXCEPTION 'job %: a pending release can only become released or withheld', OLD.job_id
              USING ERRCODE = 'RG023';
    END IF;
    IF NEW.release_status = 'released' AND NOT EXISTS (
            SELECT 1 FROM release_decisions d WHERE d.job_id = NEW.job_id AND d.decision = 'approve') THEN
        RAISE EXCEPTION 'job %: cannot be released without an approve decision', NEW.job_id USING ERRCODE = 'RG021';
    END IF;
    IF NEW.release_status = 'withheld' AND NOT EXISTS (
            SELECT 1 FROM release_decisions d WHERE d.job_id = NEW.job_id AND d.decision = 'reject') THEN
        RAISE EXCEPTION 'job %: cannot be withheld without a reject decision', NEW.job_id USING ERRCODE = 'RG022';
    END IF;
    RETURN NEW;
END $$""",
    r"""CREATE TRIGGER job_requests_release_guard
    BEFORE INSERT OR UPDATE ON job_requests
    FOR EACH ROW EXECUTE FUNCTION job_requests_release_guard()""",
]

DOWNGRADE_STATEMENTS = [
    # Held output would be lost or shown to researchers by the pre-gate code.
    r"""DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM job_outputs) THEN
        RAISE EXCEPTION 'refusing to downgrade: job_outputs holds % row(s); export and clear them first',
              (SELECT count(*) FROM job_outputs);
    END IF;
END $$""",
    "DROP TRIGGER IF EXISTS job_requests_release_guard ON job_requests",
    "DROP FUNCTION IF EXISTS job_requests_release_guard()",
    "DROP TABLE IF EXISTS release_decisions",
    "DROP FUNCTION IF EXISTS release_decisions_before_insert()",
    "DROP FUNCTION IF EXISTS release_decisions_immutable()",
    "DROP TABLE IF EXISTS job_outputs",
    "DROP FUNCTION IF EXISTS job_outputs_guard()",
    "DROP INDEX IF EXISTS ix_job_requests_release_status",
    "ALTER TABLE job_requests DROP CONSTRAINT IF EXISTS job_requests_output_sha256_check",
    "ALTER TABLE job_requests DROP CONSTRAINT IF EXISTS job_requests_release_status_check",
    "ALTER TABLE job_requests DROP COLUMN IF EXISTS output_sha256",
    "ALTER TABLE job_requests DROP COLUMN IF EXISTS ai_decision",
    "ALTER TABLE job_requests DROP COLUMN IF EXISTS release_status",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(sa.text(statement))
