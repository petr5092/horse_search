"""Private S3 file metadata.

Revision ID: 20261105_files
Revises: 7b655d5c4ae0
"""
from alembic import op
import sqlalchemy as sa

revision = "20261105_files"
down_revision = "7b655d5c4ae0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("staging_key", sa.String(255), nullable=False, unique=True),
        sa.Column("object_key", sa.String(255), nullable=True, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("operation_started_at", sa.DateTime(), nullable=True),
        sa.Column("expected_sha256", sa.String(64), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_files_owner_id", "files", ["owner_id"])


def downgrade():
    op.drop_index("ix_files_owner_id", table_name="files")
    op.drop_table("files")
