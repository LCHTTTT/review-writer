"""Durable author decisions that keep the original for a review group.

A rejection changes no manuscript artifact. It only prevents the same report's
candidate from being accepted later, so the pending list can actually close.
"""
import json
import uuid

from sqlalchemy import select

from review_writer_api.database import database_session, utc_now
from review_writer_api.workflow_models import ReviewCandidateDecision


def _decision_id(user_id, project_id, review_job_id, group_id):
    identity = json.dumps([str(user_id), str(project_id), str(review_job_id), str(group_id)],
                          ensure_ascii=False)
    return uuid.uuid5(uuid.NAMESPACE_URL, 'review-candidate-decision/1:' + identity)


def rejected_group_ids(session_factory, user_id, project_id, review_job_id):
    with database_session(session_factory) as session:
        rows = session.scalars(select(ReviewCandidateDecision).where(
            ReviewCandidateDecision.user_id == uuid.UUID(str(user_id)),
            ReviewCandidateDecision.project_id == uuid.UUID(str(project_id)),
            ReviewCandidateDecision.review_job_id == uuid.UUID(str(review_job_id)),
            ReviewCandidateDecision.decision == 'rejected',
        )).all()
        return {row.group_id for row in rows}


def rejection(session_factory, user_id, project_id, review_job_id, group_id):
    with database_session(session_factory) as session:
        row = session.scalar(select(ReviewCandidateDecision).where(
            ReviewCandidateDecision.user_id == uuid.UUID(str(user_id)),
            ReviewCandidateDecision.project_id == uuid.UUID(str(project_id)),
            ReviewCandidateDecision.review_job_id == uuid.UUID(str(review_job_id)),
            ReviewCandidateDecision.group_id == group_id,
            ReviewCandidateDecision.decision == 'rejected',
        ))
        return row


def record_rejection(session_factory, *, user_id, project_id, review_job_id, draft_artifact_id,
                      group_id, candidate_sha256, note=''):
    decision_id = _decision_id(user_id, project_id, review_job_id, group_id)
    with database_session(session_factory) as session:
        row = session.get(ReviewCandidateDecision, decision_id)
        if row is None:
            row = ReviewCandidateDecision(id=decision_id, user_id=uuid.UUID(str(user_id)),
                project_id=uuid.UUID(str(project_id)), review_job_id=uuid.UUID(str(review_job_id)),
                draft_artifact_id=uuid.UUID(str(draft_artifact_id)), group_id=group_id,
                candidate_sha256=candidate_sha256, decision='rejected', note=str(note or ''),
                created_at=utc_now())
            session.add(row)
        elif row.candidate_sha256 != candidate_sha256:
            raise ValueError('the same review group already has a different rejected candidate')
        return {'decision': 'rejected', 'group_id': group_id, 'review_job_id': str(review_job_id),
                'draft_artifact_id': str(draft_artifact_id), 'candidate_sha256': candidate_sha256}
