"""add ha_receipt and commitment_receipt JSON columns to job_requests

Adds two TEXT columns to persist the ATL inclusion receipts that ExecutionResult
already carries through to the API caller (sprint A5). The ATL log itself is
the authoritative source, but persisting receipts on the job row lets the
researcher portal display tree_size / leaf_index without re-querying the log.

- commitment_receipt: JSON-serialized ATL response for the Commitment entry
  (from Step 4b sign_and_submit_commitment).
- ha_receipt: JSON-serialized ATL response for the post-execution HA entry
  (from worker.py _submit_to_atl).

Both nullable: pre-ATL jobs and Non-Compliant jobs leave these null.

Revision ID: 2e1ea7116ba4
Revises: 44be9b128f17
Create Date: 2026-05-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2e1ea7116ba4'
down_revision: Union[str, Sequence[str], None] = '44be9b128f17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('job_requests', sa.Column('commitment_receipt', sa.Text(), nullable=True))
    op.add_column('job_requests', sa.Column('ha_receipt', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('job_requests', 'ha_receipt')
    op.drop_column('job_requests', 'commitment_receipt')
