"""Immutable author attestations of the original PDF, never parsed text."""
from alembic import op
import sqlalchemy as sa

revision = '20261016_0031'
down_revision = '20261011_0030'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('review_pdf_checks',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('user_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('project_id',sa.Uuid(),sa.ForeignKey('projects.id',ondelete='CASCADE'),nullable=False),
        sa.Column('review_job_id',sa.Uuid(),sa.ForeignKey('workflow_jobs.id',ondelete='CASCADE'),nullable=False),
        sa.Column('draft_artifact_id',sa.Uuid(),sa.ForeignKey('workflow_artifacts.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('group_id',sa.String(64),nullable=False),
        sa.Column('paper_id',sa.String(96),nullable=False),
        sa.Column('pdf_sha256',sa.String(64),nullable=False),
        sa.Column('page',sa.Integer(),nullable=False),
        sa.Column('transcription',sa.Text(),nullable=False),
        sa.Column('note',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
    )
    op.create_index('ix_review_pdf_checks_owner','review_pdf_checks',['user_id','project_id','review_job_id'])


def downgrade():
    op.drop_index('ix_review_pdf_checks_owner',table_name='review_pdf_checks')
    op.drop_table('review_pdf_checks')
