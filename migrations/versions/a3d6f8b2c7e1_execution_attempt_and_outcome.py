"""execution attempt and signed run outcome on job_requests

- execution_attempt_id: identifier of the current execution attempt, written
  before dispatch and sent to the enclave, which signs it with the platform job
  id. It is what a signed record's attempt_id is compared with, so a record from
  an earlier run of the job, or from another job, does not pass for this run.
  Replaced by each new attempt.
- analysis_outcome: what the enclave signed about the script (succeeded,
  failed, timed_out, invalid_bundle, output_rejected, error), copied from an
  authentic signed record only. Kept apart from the job status.

The signed exit code is not given a column. The script chooses it, so it is
output: it stays in the verification receipt, which is shown only after
release. job_requests.exit_code already exists and is shown to the researcher
at once, so it is left unused.
- attempt_binding: bound, mismatch, unbound_legacy or no_expected_attempt.

All nullable: rows from before this migration have no attempt and stay NULL.

Revision ID: a3d6f8b2c7e1
Revises: f2b7d5e9a1c4
Create Date: 2026-11-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3d6f8b2c7e1'
down_revision: Union[str, Sequence[str], None] = 'f2b7d5e9a1c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('job_requests', sa.Column('execution_attempt_id', sa.String(64), nullable=True))
    op.add_column('job_requests', sa.Column('analysis_outcome', sa.String(32), nullable=True))
    op.add_column('job_requests', sa.Column('attempt_binding', sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column('job_requests', 'attempt_binding')
    op.drop_column('job_requests', 'analysis_outcome')
    op.drop_column('job_requests', 'execution_attempt_id')
