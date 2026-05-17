"""add job_id_committed and researcher_nonce to job_requests

Adds the two columns required to bind the operational job_id (platform-assigned)
to the cryptographically committed identity used in JAC payloads, ATL Commitment
entries, and hardware-signed HA attestations.

- job_id_committed: SHA-256 hex of (researcher_nonce || operator_nonce). Computed
  at job-acceptance time in executor.py Step 4b. Nullable so jobs created before
  this migration remain valid; jobs without a value are treated as Non-Compliant
  in the ATL submission path.
- researcher_nonce: 16-byte nonce supplied by the researcher (or generated
  server-side as a Non-Compliant fallback). Stored hex-encoded for portability.

Indexed on job_id_committed for verifier lookups: given a Commitment entry from
the public log, an auditor maps back to the operational row via this index.

Revision ID: 44be9b128f17
Revises: 3e5c89512dec
Create Date: 2026-05-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '44be9b128f17'
down_revision: Union[str, Sequence[str], None] = '3e5c89512dec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('job_requests', sa.Column('job_id_committed', sa.String(), nullable=True))
    op.add_column('job_requests', sa.Column('researcher_nonce', sa.String(), nullable=True))
    op.create_index(
        'ix_job_requests_job_id_committed',
        'job_requests',
        ['job_id_committed'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_job_requests_job_id_committed', table_name='job_requests')
    op.drop_column('job_requests', 'researcher_nonce')
    op.drop_column('job_requests', 'job_id_committed')
