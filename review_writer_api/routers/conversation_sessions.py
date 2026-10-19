"""Shared named discussion endpoints for project and preparation scopes."""
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from review_writer_api.conversation_feature import conversation_enabled
from review_writer_api.job_service import job_payload

class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=96)

class SessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=96)
    archived: bool | None = None


def build_sessions_router(principal_dependency, service, *, preparation=False):
    prefix = "/api/v1/conversation/preparation/sessions" if preparation else "/api/v1/projects/{project_id}/conversation/sessions"
    router = APIRouter(prefix=prefix, tags=["conversation"])
    def enabled():
        if not conversation_enabled():
            raise HTTPException(503, "Conversation is disabled.")
    def scope(project_id):
        return None if preparation else project_id

    @router.get("")
    def list_sessions(project_id: str | None = None, principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        return service.sessions.list(principal, scope(project_id))

    @router.post("", status_code=202)
    def create(payload: SessionCreate, project_id: str | None = None,
               idempotency_key: str = Header(default="", alias="Idempotency-Key"), principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        return job_payload(service.sessions.create(principal, scope(project_id), payload.title, idempotency_key.strip() or str(uuid.uuid4())))

    @router.patch("/{session_id}")
    def update(session_id: uuid.UUID, payload: SessionUpdate, project_id: str | None = None,
               principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        return service.sessions.update(principal, scope(project_id), str(session_id), title=payload.title, archived=payload.archived)
    return router
