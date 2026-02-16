"""add enclave metadata to job requests

Revision ID: 3e5c89512dec
Revises: 5bfa12f6aca4
Create Date: 2026-02-16 20:08:40.236326

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3e5c89512dec'
down_revision: Union[str, Sequence[str], None] = '5bfa12f6aca4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('job_requests', sa.Column('enclave_version', sa.String(), nullable=True))
    op.add_column('job_requests', sa.Column('enclave_pcr0', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('job_requests', 'enclave_pcr0')
    op.drop_column('job_requests', 'enclave_version')
