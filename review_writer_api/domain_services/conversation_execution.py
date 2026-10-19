"""Scoped durable sequences of registered application operations, not a new stage engine."""
from __future__ import annotations

import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from review_writer_api.errors import WorkflowValidationError
from review_writer_api.job_service import job_payload
from review_writer_api.security import Permission
from review_writer_api.domain_services.conversation_actions import ACTIONS, ActionArguments
from review_writer_api.domain_services.conversation_authorization import ADOPTIONS, authorize_action, validate_request, revision_save_requested


class ResultReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    step: int = Field(ge=0, le=63)
    key: Literal["blueprint_artifact_id", "candidate_ids.0", "job.id"]


class ExecutionStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: str = Field(min_length=1, max_length=64)
    arguments: dict = Field(default_factory=dict)


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_quote: str = Field(min_length=2, max_length=12000)
    steps: list[ExecutionStep] = Field(min_length=1, max_length=12)
    stop_at: Literal["planning", "sections", "draft", "final"] | None = None
    auto_adopt: bool = False


class ConversationExecution:
    def __init__(self, conversation, actions, jobs):
        self.conversation, self.actions, self.jobs = conversation, actions, jobs

    def submit(self, principal, project_id, request, *, message, turn_id, operation_id, baseline, object_id="", semantic_intent=None):
        principal.require(Permission.PROJECT_WRITE)
        self.conversation._owned_project(principal, project_id)
        source = self.jobs.repository.get_job(principal.user_id, turn_id)
        web_preference = source.payload.get("web_search") if source else None
        if object_id and not self.conversation.read_artifact(principal, project_id, object_id, limit=1)["current"]:
            raise WorkflowValidationError("当前讨论对象是历史版本；请打开当前成果再修改，未覆盖最新稿。")
        try:
            parsed = ExecutionRequest.model_validate(request)
            for position, step in enumerate(parsed.steps):
                if step.action not in ACTIONS or set(step.arguments) - ACTIONS[step.action][1]:
                    raise WorkflowValidationError("未登记的操作或参数，未提交执行。")
                if step.action in ADOPTIONS and not parsed.auto_adopt and position > 0:
                    generated = {"blueprint.confirm": "planning.blueprint", "sections.confirm": "sections.generate", "draft.approve": "draft.assemble", "planning.apply_recommendation": "planning.recommend_outline"}
                    if generated.get(step.action) in {prior.action for prior in parsed.steps[:position]}:
                        raise WorkflowValidationError("普通继续须保留新成果的采用节点，未授权自动确认。")
                plain = {}
                for key, value in step.arguments.items():
                    if isinstance(value, dict) and key in {"artifact_id", "candidate_id", "source_job_id"}:
                        reference = ResultReference.model_validate(value)
                        if reference.step >= position or key not in {"artifact_id", "candidate_id", "source_job_id"}:
                            raise WorkflowValidationError("结果引用必须指向本计划此前的实际输出。")
                        allowed = {"artifact_id": "blueprint_artifact_id", "candidate_id": "candidate_ids.0", "source_job_id": "job.id"}
                        if allowed[key] != reference.key:
                            raise WorkflowValidationError("结果引用类型不匹配。")
                    else:
                        plain[key] = value
                ActionArguments.model_validate(plain)
                if semantic_intent is None:
                    authorize_action(message, parsed.request_quote, step.action, step.arguments,
                                     automatic=parsed.auto_adopt, stop_at=parsed.stop_at, web_search=web_preference)
        except ValidationError as exc:
            issues = "; ".join(".".join(str(part) for part in error["loc"]) + ": " + error["msg"]
                               for error in exc.errors(include_input=False)[:3])
            raise WorkflowValidationError("执行计划参数格式不正确：" + issues) from exc
        validate_request(message, parsed.request_quote, semantic=semantic_intent is not None)
        if semantic_intent is not None:
            from .conversation_intent import resolve_intent
            if semantic_intent.request_quote != message:
                raise WorkflowValidationError("意图必须绑定完整的本轮消息，不能截去限制条件。")
            actual = resolve_intent(semantic_intent, self.conversation, principal, project_id, baseline)
            if actual.get("request") != request:
                raise WorkflowValidationError("执行计划与本次意图和项目状态不一致，未提交操作。")
        # A direct "revise and save" request is a bounded continuation of the
        # existing revision worker. Never save the chat model's invented prose.
        if len(parsed.steps) == 1 and parsed.steps[0].action in {"draft.revise_section", "draft.revise_paragraph"}:
            if revision_save_requested(message):
                parsed.steps.append(ExecutionStep(action="draft.accept_generated", arguments={
                    "section_id": parsed.steps[0].arguments["section_id"],
                    **({"paragraph_id": parsed.steps[0].arguments["paragraph_id"]} if parsed.steps[0].action == "draft.revise_paragraph" else {}),
                    "source_job_id": {"step": 0, "key": "job.id"}}))
        costly = {"discovery.search", "discovery.acquire", "planning.analyze", "planning.recommend_outline", "planning.blueprint", "sections.generate", "sections.rewrite", "images.redraw", "draft.overview", "draft.revise_section", "draft.revise_paragraph", "draft.synthesis"}
        if sum(step.action in costly for step in parsed.steps) > 3:
            raise WorkflowValidationError("本轮生成步骤预算已达上限，请缩小范围；未提交额外生成。")
        value = parsed.model_dump()
        if semantic_intent is not None:
            value["semantic_intent"] = semantic_intent.model_dump()
        value.update(source_turn_id=turn_id, user_request=message,
                     baseline={"expected_artifacts": {a["logical_name"]: a["id"] for a in baseline["artifacts"]},
                               "expected_revisions": {s["id"]: s["revision"] for stage in baseline["stages"] for s in stage["checkpoints"]}})
        session_id = source.payload.get("session_id", "") if source else ""
        if session_id:
            value["session_id"] = session_id
        job = self.jobs.submit(principal, scope="project", project_id=project_id, job_type="conversation.plan",
                              operation_key="project-continuation" + (":" + session_id if session_id else ""), idempotency_key=operation_id, payload=value)
        return job_payload(job)

    def submit_card(self, principal, project_id, turn_id, action, *, parent_plan_id=None):
        payload = {"source_turn_id": turn_id, "action": action}
        if parent_plan_id:
            payload["parent_plan_id"] = parent_plan_id
        return self.jobs.submit(principal, scope="project", project_id=project_id, job_type="conversation.action",
                               operation_key=action["id"], idempotency_key=action["id"],
                               payload=payload)

    def stop(self, principal, project_id, *, before=None, session_id=None):
        self.conversation._owned_project(principal, project_id)
        records = self.jobs.repository.list_project_jobs(principal.user_id, project_id, job_type="conversation.plan", limit=100)
        stopped = []
        for job in records:
            if job.status not in {"queued", "running", "cancel_requested"}:
                continue
            source = self.jobs.repository.get_job(principal.user_id, job.payload.get("source_turn_id", "")) if job.payload.get("source_turn_id") else None
            if session_id is not None and (source.payload.get("session_id", "") if source else "") != session_id:
                continue
            if before is not None and (source.created_at if source else job.created_at) > before:
                continue
            stopped.append(job_payload(self.jobs.request_cancel(principal, job.id)))
        return stopped


def operation_id(turn_id, index):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"conversation-execution:{turn_id}:{index}"))
