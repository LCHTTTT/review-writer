"""Author decisions that keep the original manuscript for one review group."""
from alembic import op
import sqlalchemy as sa

revision = '20261020_0032'
down_revision = '20261016_0031'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('review_candidate_decisions',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('user_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),
        sa.Column('project_id',sa.Uuid(),sa.ForeignKey('projects.id',ondelete='CASCADE'),nullable=False),
        sa.Column('review_job_id',sa.Uuid(),sa.ForeignKey('workflow_jobs.id',ondelete='CASCADE'),nullable=False),
        sa.Column('draft_artifact_id',sa.Uuid(),sa.ForeignKey('workflow_artifacts.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('group_id',sa.String(64),nullable=False),
        sa.Column('candidate_sha256',sa.String(64),nullable=False),
        sa.Column('decision',sa.String(16),nullable=False),
        sa.Column('note',sa.Text(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('user_id','project_id','review_job_id','group_id',
                            name='uq_review_candidate_decision_group'),
    )
    op.create_index('ix_review_candidate_decisions_owner','review_candidate_decisions',
                    ['user_id','project_id','review_job_id'])


def downgrade():
    op.drop_index('ix_review_candidate_decisions_owner',table_name='review_candidate_decisions')
    op.drop_table('review_candidate_decisions')
