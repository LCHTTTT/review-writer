"""Named discussion metadata in the existing durable job store; no scientific state."""
import uuid
from sqlalchemy import select, or_, and_
from review_writer_api.database import database_session
from review_writer_api.errors import WorkflowNotFound, WorkflowConflict
from review_writer_api.security import Permission
from review_writer_api.workflow_models import WorkflowJob


class ConversationSessions:
    def __init__(self, conversation, jobs):
        self.conversation, self.jobs = conversation, jobs

    def get(self, principal, project_id, session_id, *, for_write=False):
        self.conversation._owned_project(principal, project_id)
        if not session_id:
            return None  # legacy/default discussion remains independently usable
        record = self.jobs.repository.get_job(principal.user_id, session_id)
        if not record or record.job_type != "conversation.session" or not (
            (record.project_id == project_id and not (project_id is None and record.result.get("bound_project_id"))) or
            (record.project_id is None and project_id is not None and record.result.get("bound_project_id") == project_id)):
            raise WorkflowNotFound("Discussion not found in this project/account.")
        if for_write and record.result.get("archived"):
            raise WorkflowConflict("This discussion is archived; restore it before sending.")
        return record

    def list(self, principal, project_id):
        self.conversation._owned_project(principal, project_id)
        with database_session(self.jobs.repository.session_factory) as db:
            scope = (and_(WorkflowJob.project_id.is_(None), WorkflowJob.result_json["bound_project_id"].as_string().is_(None))
                     if project_id is None else or_(WorkflowJob.project_id == uuid.UUID(project_id),
                         WorkflowJob.result_json["bound_project_id"].as_string() == project_id))
            rows = list(db.scalars(select(WorkflowJob).where(WorkflowJob.user_id == uuid.UUID(principal.user_id),
                WorkflowJob.job_type == "conversation.session", scope).order_by(WorkflowJob.created_at.desc()).limit(100)))
            return {"items": [{"id": str(row.id), "title": (row.result_json or {}).get("title", (row.payload_json or {}).get("title", "")),
                               "archived": bool((row.result_json or {}).get("archived")), "created_at": row.created_at.isoformat()} for row in rows]}

    def create(self, principal, project_id, title, key):
        self.conversation._owned_project(principal, project_id)
        return self.jobs.submit(principal, scope="project" if project_id else "library", project_id=project_id,
            job_type="conversation.session", idempotency_key=key, operation_key="discussion:" + key, payload={"title": title})

    def update(self, principal, project_id, session_id, title=None, archived=None):
        principal.require(Permission.PROJECT_WRITE)
        self.conversation._owned_project(principal, project_id)
        with database_session(self.jobs.repository.session_factory) as db:
            row = db.scalar(select(WorkflowJob).where(WorkflowJob.id == uuid.UUID(session_id),
                WorkflowJob.user_id == uuid.UUID(principal.user_id), WorkflowJob.job_type == "conversation.session").with_for_update())
            if row is None or not (str(row.project_id) == project_id or (row.project_id is None and (row.result_json or {}).get("bound_project_id") == project_id)):
                raise WorkflowNotFound("Discussion not found.")
            if row.status != "succeeded":
                raise WorkflowConflict("Wait for discussion creation to complete.")
            value = dict(row.result_json or {})
            if title is not None:
                value["title"] = title
            if archived is not None:
                value["archived"] = archived
            row.result_json = value
        return {"id": session_id, **value}


def register_sessions(conversation, jobs):
    def create(context, payload):
        context.checkpoint()
        return {"title": payload["title"], "archived": False}
    jobs.register_handler("conversation.session", create)
    conversation.sessions = ConversationSessions(conversation, jobs)
