"""Platform annotation command journal and durable image usage pins."""
from alembic import op
import sqlalchemy as sa

revision = '20261108_gateway'
down_revision = '20261108_platform'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('gateway_operations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('projects.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('operation', sa.String(64), nullable=False),
        sa.Column('request_id', sa.String(36), nullable=False),
        sa.Column('public_hash', sa.String(64), nullable=False),
        sa.Column('go_payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('external_id', sa.String(36)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('project_id', 'actor_id', 'operation', 'request_id', name='uq_gateway_operation_key'),
        sa.CheckConstraint("status in ('prepared','unknown','succeeded','rejected')", name='ck_gateway_operation_status'))
    op.create_table('gateway_operation_images',
        sa.Column('operation_id', sa.String(36), sa.ForeignKey('gateway_operations.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('image_version_id', sa.String(36), sa.ForeignKey('image_versions.id', ondelete='RESTRICT'), primary_key=True))


def downgrade():
    op.drop_table('gateway_operation_images')
    op.drop_table('gateway_operations')
