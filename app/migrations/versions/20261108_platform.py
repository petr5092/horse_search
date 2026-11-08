"""Projects, immutable label schemas and prepared image library."""
from alembic import op
import sqlalchemy as sa

revision = '20261108_platform'
down_revision = '20261105_files'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('projects',
        sa.Column('id',sa.String(36),primary_key=True), sa.Column('name',sa.String(255),nullable=False),
        sa.Column('state',sa.String(16),nullable=False), sa.Column('label_schema_id',sa.String(36),nullable=False,unique=True),
        sa.Column('current_label_schema_version',sa.Integer(),nullable=False), sa.CheckConstraint("state IN ('active','archived')"))
    op.create_table('project_members',
        sa.Column('project_id',sa.String(36),sa.ForeignKey('projects.id'),primary_key=True),
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),primary_key=True),
        sa.Column('role',sa.String(16),nullable=False),sa.Column('is_active',sa.Boolean(),nullable=False),
        sa.CheckConstraint("role IN ('manager','annotator','reviewer')"))
    op.create_table('label_schema_versions',sa.Column('schema_id',sa.String(36),primary_key=True),
        sa.Column('version',sa.Integer(),primary_key=True),sa.Column('project_id',sa.String(36),sa.ForeignKey('projects.id'),nullable=False),
        sa.Column('labels',sa.JSON(),nullable=False),sa.UniqueConstraint('project_id','version'),sa.CheckConstraint('version > 0'))
    op.create_table('assets',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('project_id',sa.String(36),sa.ForeignKey('projects.id'),nullable=False),
        sa.Column('source_file_id',sa.String(36),sa.ForeignKey('files.id'),nullable=False),
        sa.Column('source_uri',sa.String(2048),nullable=False),sa.Column('license',sa.String(255),nullable=False),
        sa.Column('session_id',sa.String(36),nullable=False),sa.UniqueConstraint('id','project_id'))
    op.create_index('ix_assets_project_id','assets',['project_id'])
    op.create_table('image_versions',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('project_id',sa.String(36),sa.ForeignKey('projects.id'),nullable=False),
        sa.Column('asset_id',sa.String(36),nullable=False),sa.Column('file_id',sa.String(36),sa.ForeignKey('files.id'),nullable=False,unique=True),
        sa.Column('sha256',sa.String(64),nullable=False),sa.Column('width',sa.Integer(),nullable=False),sa.Column('height',sa.Integer(),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),sa.Column('created_at',sa.DateTime(),nullable=False),
        sa.ForeignKeyConstraint(['asset_id','project_id'],['assets.id','assets.project_id']),
        sa.CheckConstraint('width > 0 AND height > 0'),sa.CheckConstraint("status = 'ready'"))
    op.create_index('ix_image_versions_project_id','image_versions',['project_id'])


def downgrade():
    for table in ['image_versions','assets','label_schema_versions','project_members','projects']:
        op.drop_table(table)
