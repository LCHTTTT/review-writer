"""Account preparation turns: no placeholder project and no scientific executor."""
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from review_writer_api.conversation_feature import conversation_enabled
from review_writer_api.domain_services.conversation_authorization import validate_supported_limits
from review_writer_api.job_service import job_payload


class PreparationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=12000)
    session_id: str = Field(default="", max_length=36)
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=8)


class PreparationBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: uuid.UUID
    turn_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    session_id: uuid.UUID | None = None


def build_preparation_router(principal_dependency, service, jobs):
    router = APIRouter(prefix="/api/v1/conversation/preparation", tags=["conversation"])

    def enabled():
        if not conversation_enabled():
            raise HTTPException(503, "Conversation is disabled; project creation remains available.")

    @router.get("/messages")
    def history(before: str = "", session_id: str = "", limit: int = Query(20, ge=1, le=50), principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        return service.history(principal, None, before=before, limit=limit, session_id=session_id)

    @router.post("/messages", status_code=202)
    def send(payload: PreparationMessage, idempotency_key: str = Header(default="", alias="Idempotency-Key"),
             principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        validate_supported_limits(payload.message)
        service.sessions.get(principal, None, payload.session_id, for_write=True)
        service.attachments.descriptors(principal, [str(identifier) for identifier in payload.attachment_ids])
        value = payload.model_dump(exclude={"session_id", "attachment_ids"})
        if payload.session_id:
            value["session_id"] = payload.session_id
        if payload.attachment_ids:
            value["attachment_ids"] = [str(identifier) for identifier in payload.attachment_ids]
        key = idempotency_key.strip() or str(uuid.uuid4())
        return job_payload(jobs.submit(principal, scope="library", project_id=None, job_type="conversation.prepare",
            idempotency_key=key, operation_key="preparation:" + key, payload=value))

    @router.post("/bind")
    def bind(payload: PreparationBinding, principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        return service.bind_preparation(principal, str(payload.project_id), payload.turn_ids, session_id=str(payload.session_id) if payload.session_id else "")

    return router
