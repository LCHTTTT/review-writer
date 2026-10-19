"""Optional project conversation endpoints. Original stage routes are unchanged."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from review_writer_api.job_service import job_payload
from review_writer_api.errors import WorkflowValidationError


from review_writer_api.conversation_feature import conversation_enabled


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=12000)
    object_id: str = Field(default="", max_length=36)
    delivery: Literal["follow_up", "steer"] = "follow_up"
    session_id: str = Field(default="", max_length=36)
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=8)
    web_search: StrictBool | None = None


def build_conversation_router(principal_dependency, service, jobs):
    router = APIRouter(prefix="/api/v1/projects/{project_id}/conversation", tags=["conversation"])

    def enabled():
        if not conversation_enabled():
            raise HTTPException(503, "Project conversation is disabled. Stage pages remain available.")

    @router.get("/overview")
    def overview(project_id: str, principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        return service.overview(principal, project_id)

    @router.get("/messages")
    def history(project_id: str, before: str = "", session_id: str = "", limit: int = Query(20, ge=1, le=50),
                principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        return service.history(principal, project_id, before=before, limit=limit, session_id=session_id)

    @router.get("/artifacts/{artifact_id}")
    def read_artifact(project_id: str, artifact_id: str, offset: int = Query(0, ge=0),
                      limit: int = Query(12000, ge=1, le=20000),
                      view: Literal["raw", "readable"] = "raw", language: Literal["zh", "zh-CN", "en"] = "zh",
                      principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        return service.read_artifact(principal, project_id, artifact_id, offset=offset, limit=limit, view=view,
                                     language="en" if language == "en" else "zh")

    @router.get("/versions/{logical_name:path}")
    def versions(project_id: str, logical_name: str, principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        from review_writer_api.domain_services.conversation import RESULTS, ALLOWED_RESULTS
        service._owned_project(principal, project_id)
        if logical_name not in ALLOWED_RESULTS:
            raise WorkflowValidationError("This result is not available in conversation.")
        stage, label, _ = next(row for row in RESULTS if row[2] == logical_name)
        current = service.repository.get_current_artifact(principal.user_id, project_id, logical_name)
        return {"items": [service.card(a, stage, label, bool(current and current.id == a.id))
                          for a in service.repository.list_artifacts(principal.user_id, project_id, logical_name)[:50]]}

    @router.post("/messages", status_code=202)
    def send(project_id: str, payload: ConversationMessage,
             idempotency_key: str = Header(default="", alias="Idempotency-Key"),
             principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        service._owned_project(principal, project_id)
        if payload.object_id:
            # Pin a real owned version; no guessed or foreign objects.
            service.read_artifact(principal, project_id, payload.object_id, limit=1)
        from review_writer_api.domain_services.conversation_authorization import is_steering, is_stop_continuation, validate_supported_limits
        stop_only = is_stop_continuation(payload.message)
        if not stop_only:
            validate_supported_limits(payload.message)
        service.sessions.get(principal, project_id, payload.session_id, for_write=True)
        service.attachments.descriptors(principal, [str(identifier) for identifier in payload.attachment_ids])
        value = payload.model_dump(exclude={"session_id", "attachment_ids"}, exclude_none=True)
        if payload.attachment_ids:
            value["attachment_ids"] = [str(identifier) for identifier in payload.attachment_ids]
        if payload.session_id:
            value["session_id"] = payload.session_id
        if payload.delivery == "steer" or is_steering(payload.message):
            value["delivery"] = "steer"
        key = idempotency_key.strip() or str(uuid.uuid4())
        job = jobs.submit(principal, scope="project", project_id=project_id, job_type="conversation.control" if stop_only else "conversation.turn",
                          operation_key="project-dialogue:" + key, idempotency_key=key, payload=value)
        if value["delivery"] == "steer":
            # Message identity precedes the control effect; a lost-response
            # retry cannot cancel plans created from later user requests.
            service.execution.stop(principal, project_id, before=job.created_at, session_id=payload.session_id)
        return job_payload(job)

    def owned_action(principal, project_id, turn_id, action_id):
        from review_writer_api.errors import WorkflowNotFound
        service._owned_project(principal, project_id)
        turn = service.repository.get_job(principal.user_id, turn_id)
        if not turn or turn.project_id != project_id or turn.job_type != "conversation.turn":
            raise WorkflowNotFound("Conversation turn not found.")
        action = next((row for row in (turn.result or {}).get("actions", []) if row.get("id") == action_id), None)
        if action is None:
            raise WorkflowNotFound("Conversation action not found.")
        return turn, action

    @router.get("/actions/{turn_id}/{action_id}")
    def action_status(project_id: str, turn_id: str, action_id: str,
                      principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        _turn, action = owned_action(principal, project_id, turn_id, action_id)
        current = jobs.repository.get_current_job(principal.user_id, scope="project", project_id=project_id,
                                                  job_type="conversation.action", operation_key=action_id)
        return {"action": action, "job": job_payload(current) if current else None}

    @router.post("/actions/{turn_id}/{action_id}", status_code=202)
    def adopt_action(project_id: str, turn_id: str, action_id: str,
                     principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        from review_writer_api.errors import WorkflowConflict
        turn, action = owned_action(principal, project_id, turn_id, action_id)
        if turn.status != "succeeded":
            raise WorkflowConflict("Wait for this conversation turn to complete before adopting an action.")
        # Stable action ID is the operation identity even if the response is lost.
        job = service.execution.submit_card(principal, project_id, turn_id, action)
        return job_payload(job)

    @router.post("/continuations/stop")
    def stop_continuation(project_id: str, principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        return {"plans": service.execution.stop(principal, project_id), "business_jobs_cancelled": False}

    return router
