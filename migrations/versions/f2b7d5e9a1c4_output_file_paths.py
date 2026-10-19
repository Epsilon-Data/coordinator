"""output file paths: a held file's name is its path relative to outputs/

A run may now write any file type, in subfolders of outputs/. A file's name
is its relative path with '/' as separator: 1 to 5 segments, each matching
^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$, at most 255 characters in all.
job_output_files.name is widened to varchar(255) and its CHECK replaced by
that rule. Existing names (flat, at most 100 characters) satisfy it.

Storage keys are unchanged (jobs/<job_id>/<sha256>); names never go into them.

Downgrade restores varchar(100) and the flat-name CHECK, and refuses while any
row's name contains '/' or is longer than 100 characters.

Revision ID: f2b7d5e9a1c4
Revises: d8b4f1a6c3e9
Create Date: 2026-10-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f2b7d5e9a1c4'
down_revision: Union[str, Sequence[str], None] = 'd8b4f1a6c3e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UPGRADE_STATEMENTS = [
    "ALTER TABLE job_output_files DROP CONSTRAINT job_output_files_name_check",
    "ALTER TABLE job_output_files ALTER COLUMN name TYPE varchar(255)",
    r"""ALTER TABLE job_output_files ADD CONSTRAINT job_output_files_name_check
    CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}(/[A-Za-z0-9][A-Za-z0-9._-]{0,99}){0,4}$'
           AND char_length(name) <= 255)""",
]

DOWNGRADE_STATEMENTS = [
    r"""DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM job_output_files WHERE strpos(name, '/') > 0 OR char_length(name) > 100) THEN
        RAISE EXCEPTION 'refusing to downgrade: job_output_files holds % row(s) with a path or a name over 100 characters',
              (SELECT count(*) FROM job_output_files WHERE strpos(name, '/') > 0 OR char_length(name) > 100);
    END IF;
END $$""",
    "ALTER TABLE job_output_files DROP CONSTRAINT job_output_files_name_check",
    "ALTER TABLE job_output_files ALTER COLUMN name TYPE varchar(100)",
    r"""ALTER TABLE job_output_files ADD CONSTRAINT job_output_files_name_check
    CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')""",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(sa.text(statement))


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(sa.text(statement))
