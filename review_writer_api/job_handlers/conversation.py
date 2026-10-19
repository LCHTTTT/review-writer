"""Bounded, checkpointed read-tool loop; no stage mutation or shell tools."""
from __future__ import annotations

import json
from review_writer_api.conversation_feature import conversation_enabled
import uuid
from pathlib import Path

from review_writer_api.errors import ConversationModelFailure, WorkflowError, WorkflowValidationError
from review_writer_api.domain_services.conversation_intent import ConversationIntent, resolve_intent
from review_writer_api.security import Principal, Role
from review_writer_core.dialogue_memory import conversation_memory
from review_writer_core.model_gateway_client import GatewayRequestError, ModelResultUnknown, call_model, parse_json_object_text
from review_writer_api.domain_services.conversation_context import tool_context

TOOLS = [
    {"name": "read_attachment", "arguments": {"attachment_id": "an attached owned ID", "offset": 0, "limit": 12000}},
    {"name": "project_state", "arguments": {}},
    {"name": "stage_details", "arguments": {"stage": "discovery|planning|sections|images|draft|final"}},
    {"name": "request_action", "arguments": {"action": "registered action name", "arguments": "action-specific object"}},
    {"name": "execute_action", "arguments": {"action": "registered action name", "arguments": "action-specific object", "request_quote": "literal directive from this user message"}},
    {"name": "execute_plan", "arguments": {"steps": [{"action": "registered action", "arguments": {}}], "request_quote": "literal user directive", "stop_at": "planning|sections|draft|final or null", "auto_adopt": True}},
    {"name": "execute_intent", "arguments": {"goal": "discovery|planning|sections|draft|final", "operation": "generate_review|select_recommended", "request_quote": "complete current user message, including constraints", "auto_adopt": "boolean: true only when user authorizes recommended decisions within goal"}},
    {"name": "stop_continuation", "arguments": {}},
    {"name": "read_section", "arguments": {"artifact_id": "optional saved section_drafts UUID; defaults to current", "section_id": "omit for chapter directory; exact ID for chapter prose; * for all chapter prose", "paragraph_id": "optional exact paragraph ID", "view": "prose (default) or evidence (requires one paragraph)", "offset": "optional character offset within selected view", "limit": "optional 1..20000"}},
    {"name": "read_draft", "arguments": {"section_id": "omit for current Draft chapter/paragraph directory; exact ID to read chapter", "paragraph_id": "optional exact ID to read only one paragraph in that chapter", "offset": "optional selected-view character offset", "limit": "optional 1..20000"}},
    {"name": "read_artifact", "arguments": {"artifact_id": "owned project artifact UUID", "view": "prose (default, chapter body only) or raw (explicit JSON debugging)", "offset": "optional character offset in selected view", "limit": "optional 1..20000"}},
    {"name": "list_papers", "arguments": {"query": "optional title search; account library, not selected corpus"}},
    {"name": "project_papers", "arguments": {"query": "optional library title/ID search; includes actual project selection", "offset": "optional page offset", "limit": "optional 1..100; default 50"}},
    {"name": "paper_metadata", "arguments": {"paper_id": "current-user paper ID; no parsed fulltext required"}},
    {"name": "read_paper", "arguments": {"paper_id": "current user paper ID", "offset": "optional character offset", "limit": "optional 1..20000"}},
]


def register_conversation_handler(service, jobs, gateway, *, model_call=None):
    def handler(context, payload):
        principal = Principal(context.user_id, frozenset({Role.USER}))
        preparing = context.project_id is None
        service._owned_project(principal, context.project_id)
        current = context.repository.get_job(context.user_id, context.job_id)
        saved = dict(current.result or {})
        source = current
        while not saved and source.retry_of_job_id:
            source = context.repository.get_job(context.user_id, source.retry_of_job_id)
            if source is None:
                break
            saved = dict(source.result or {})
            if saved:
                saved.setdefault("execution_id", source.id)
        saved.setdefault("execution_id", context.job_id)
        if saved.get("reply"):
            return saved
        context.checkpoint()
        if not conversation_enabled():
            raise WorkflowValidationError("Conversation is disabled; stage tasks are unchanged.")
        if not preparing and not saved.get("executions"):
            # Save the intent BEFORE submitting; retries reuse the same plan ID
            # and baseline rather than replaying a paid retrieval or rebasing.
            from review_writer_api.domain_services.conversation_entry import discovery_entry, recommendation_entry
            from review_writer_api.domain_services.conversation_continuation import continuation_entry
            intent = saved.get("entry_intent")
            if not intent:
                project = service.overview(principal, context.project_id)
                request = None
                reply = None
                canonical = None
                continuation = continuation_entry(payload["message"], service, principal, context.project_id, project,
                    object_id=payload.get("object_id", ""), attachment_ids=payload.get("attachment_ids", []))
                if continuation:
                    canonical = ConversationIntent(request_quote=payload["message"], auto_adopt=True)
                    if continuation and not continuation.get("request"):
                        saved.update(reply=continuation["reply"], scope="discussion_only", activity={"phase": "completed"})
                        context.report_partial_result(saved)
                        return saved
                    request = continuation.get("request") if continuation else None
                    reply = continuation.get("reply") if continuation else None
                if request is None and not continuation and not saved.get("base"):
                    request = recommendation_entry(payload["message"], service.actions.services.discovery_service, principal, context.project_id,
                        object_id=payload.get("object_id", ""), attachment_ids=payload.get("attachment_ids", []))
                if request is None and not continuation and not saved.get("base"):
                    request = discovery_entry(payload["message"], project, object_id=payload.get("object_id", ""), attachment_ids=payload.get("attachment_ids", []), web_search=payload.get("web_search"))
                if request:
                    intent = {"request": request, "baseline": project,
                              "id": str(uuid.uuid5(uuid.NAMESPACE_URL, saved["execution_id"] + ":discovery-entry"))}
                    if reply:
                        intent["reply"] = reply
                    if canonical:
                        intent["semantic_intent"] = canonical.model_dump()
                    saved["entry_intent"] = intent
                    context.report_partial_result(saved)
            if intent:
                steering = service.steering_after(principal, context.project_id, context.job_id)
                if steering:
                    saved.update(reply="已收到调整要求，本轮尚未提交接续任务。", interrupted_by=steering.id)
                    return saved
                context.checkpoint()
                execution = service.execution.submit(principal, context.project_id, intent["request"], message=payload["message"],
                    turn_id=saved["execution_id"], operation_id=intent["id"], baseline=intent["baseline"],
                    semantic_intent=ConversationIntent.model_validate(intent["semantic_intent"]) if intent.get("semantic_intent") else None)
                selecting = intent["request"]["steps"][0]["action"] == "discovery.selection"
                saved.update(executions=[execution], scope="scoped_execution", activity={"phase": "submitted", "operation": intent["request"]["steps"][0]["action"]},
                    reply=intent.get("reply") or ("已提交纳入当前本地推荐文献，并接续确认筛选、启动文献分析。左侧将显示实际已保存的文献；分析与任务进度在下方查看。大纲和正文尚未生成。" if selecting else
                    "已将主题交给第二阶段的检索与筛选任务。推荐结果将在下方显示；请选定要纳入的文献后，再确认并开始分析。尚未确认文献或启动正文写作。"))
                context.report_partial_result(saved)
                context.report_progress(1, 1)
                return saved
        if not saved.get("base"):
            records = service.history(principal, context.project_id, limit=50, session_id=payload.get("session_id", ""))["turns"]
            memory = conversation_memory([{"id": turn["id"], "user": turn["message"], "task_status": turn["status"],
                "responses": [{"assistant": turn["result"].get("reply", ""), "decision": "discussion_only"}]}
                for turn in records if turn["id"] != context.job_id and turn["status"] not in {"queued", "running", "cancel_requested"}])
            skill = (Path(__file__).resolve().parents[2] / "skills" / "review-project-conversation" / "SKILL.md").read_text(encoding="utf-8")
            from review_writer_api.domain_services.conversation_actions import ACTIONS
            preparation_tools = [tool for tool in TOOLS if tool["name"] in {"project_state", "list_papers", "paper_metadata", "read_paper", "read_attachment"}] + [
                {"name": "prepare_project", "arguments": {"slug": "project name", "topic": "research scope", "taxonomy_profile": "optional; chemistry_general by default"}}]
            if preparing:
                skill += "\nACCOUNT PREPARATION: no project exists. Discuss research needs, then prepare_project proposes editable creation fields only. No stage tools or execution. Never claim a project was created. Do not require model/API/image setup to create a project."
            saved["base"] = {"instructions": skill, "tools": preparation_tools if preparing else TOOLS,
                             "available_actions": {} if preparing else {key: {"label": value[0], "argument_names": sorted(value[1])} for key, value in ACTIONS.items()},
                             "project": service.overview(principal, context.project_id),
                             "memory": memory, "user": payload["message"], "object_id": payload.get("object_id", ""),
                             "attachments": service.attachments.descriptors(principal, list(dict.fromkeys([
                                 *payload.get("attachment_ids", []), *[a["id"] for record in reversed(records) for a in record.get("attachments", [])]
                             ]))[:8])}
            if not preparing:
                from review_writer_api.domain_services.conversation_intent import intent_context
                saved["base"]["intent_context"] = intent_context(service, principal, context.project_id)
                saved["base"]["retrieval_permission"] = {"web_search": payload.get("web_search"),
                    "rule": "The current-turn switch grants permission only for actual discovery searches. False means local-only even if text mentions online; True allows online unless text forbids it. Null means legacy explicit-text permission. Do not inherit it from history or search merely because it is enabled."}
            saved["steps"], saved["tool_events"], saved["artifact_ids"], saved["actions"] = [], [], [], []
            context.report_partial_result(saved)
        for index in range(6):
            context.checkpoint()
            if not conversation_enabled():
                raise WorkflowValidationError("Conversation is disabled; stage tasks are unchanged.")
            steering = service.steering_after(principal, context.project_id, context.job_id)
            if steering:
                saved.update(reply="已收到调整要求，尚未提交的操作已停止；已经提交的后台任务仍按原要求执行。调整消息将随后处理。", interrupted_by=steering.id)
                context.report_partial_result(saved)
                return saved
            steps = saved["steps"]
            if index >= len(steps):
                steps.append({})
            step = steps[index]
            if "response" not in step:
                # Rebuild only the unfinished round, preserving the admitted
                # baseline and completed tool results on explicit job retry.
                prompt = json.dumps({"context": saved["base"], "tool_results": tool_context(steps),
                    "response_contract": {"reply": "final answer when tools is empty; otherwise optional brief intent, not a completed result",
                                          "workflow_state": "not_generated or WORKFLOW_STAGE_NOT_READY is a normal missing output, not provider failure. Do not reread it; use existing prerequisites and registered actions for an authorized continuation. Keep routing brief; original stage workers perform the scientific work.",
                                          "tools": [{"name": "registered read tool", "arguments": {}}]},
                    "remaining_model_steps": 6 - index}, ensure_ascii=False)
                if len(prompt) > 110000:
                    raise WorkflowValidationError("Conversation context budget reached; ask about a smaller scope.")
                step["prompt"] = prompt
                context.report_partial_result(saved)
                context.checkpoint()
                saved["activity"] = {"phase": "waiting_model", "round": index + 1, "completed_reads": len(saved["tool_events"])}
                context.report_partial_result(saved)
                try:
                    if model_call:
                        raw = model_call(step["prompt"], index)
                    else:
                        normal, secrets = gateway.environment_for_job(context)
                        raw = call_model(step["prompt"], label=f"{'preparation' if preparing else 'project'}-conversation-{index}", response_format="json",
                            gateway_url=normal["REVIEW_WRITER_MODEL_GATEWAY_URL"], task_token=secrets["REVIEW_WRITER_TASK_TOKEN"])
                except (GatewayRequestError, ModelResultUnknown) as exc:
                    failure = {**getattr(exc, "details", {}), "code": getattr(exc, "code", "")}
                    saved["activity"] = {"phase": "model_failed", "round": index + 1, "completed_reads": len(saved["tool_events"])}
                    context.report_partial_result(saved)
                    raise ConversationModelFailure.from_provider({"category": "outcome_unknown"} if isinstance(exc, ModelResultUnknown) else failure) from exc
                try:
                    response = raw if isinstance(raw, dict) else parse_json_object_text(raw, context="Project conversation", top_level_only=True)
                except RuntimeError as exc:
                    raise ConversationModelFailure("模型返回的回复格式不完整，未执行其中的操作；你的问题已保留。") from exc
                if set(response) - {"reply", "tools"}:
                    raise WorkflowValidationError("The conversation returned an invalid action format.")
                reply, calls = response.get("reply", ""), response.get("tools", [])
                if not isinstance(reply, str) or len(reply) > 24000 or not isinstance(calls, list) or len(calls) > 3:
                    raise WorkflowValidationError("The conversation response exceeds its contract.")
                if not reply.strip() and not calls:
                    raise WorkflowValidationError("The conversation returned an empty response.")
                if sum(isinstance(call, dict) and call.get("name") in {"execute_action", "execute_plan", "execute_intent"} for call in calls) > 1:
                    raise WorkflowValidationError("Use one scoped execution plan instead of competing write loops.")
                step["response"] = response
                context.report_partial_result(saved)
            response = step["response"]
            if response.get("reply", "").strip() and not response.get("tools"):
                # Keep model prompts private; the history endpoint projects only reply/events/refs.
                saved["reply"] = response["reply"].strip()
                saved["scope"] = "proposals_only" if saved.get("actions") else "discussion_only"
                saved["activity"] = {"phase": "completed"}
                context.report_partial_result(saved)
                return saved
            # A mixed reply is an intent, not a final answer or authorization.
            # Run the same validated tools; reads return to the model with actual
            # results, and writes use the server's submitted/not-completed reply.
            results = step.setdefault("results", [])
            for position, call in enumerate(response.get("tools", [])):
                if position < len(results):
                    continue
                context.checkpoint()
                steering = service.steering_after(principal, context.project_id, context.job_id)
                if steering:
                    saved.update(reply="调整要求已排队，本轮尚未提交的操作已停止；已提交任务不会自动撤销。", interrupted_by=steering.id)
                    context.report_partial_result(saved)
                    return saved
                if len(saved["tool_events"]) >= 8:
                    raise WorkflowValidationError("Conversation tool budget reached; narrow the request.")
                if not isinstance(call, dict) or set(call) != {"name", "arguments"} or not isinstance(call["arguments"], dict):
                    raise WorkflowValidationError("The conversation returned invalid tool arguments.")
                event = {"id": f"{saved['execution_id']}:{index}:{position}", "name": call["name"], "status": "succeeded"}
                saved["activity"] = {"phase": "reading", "tool": call["name"], "round": index + 1}
                try:
                    if preparing and call["name"] not in {"project_state", "list_papers", "paper_metadata", "read_paper", "read_attachment", "prepare_project"}:
                        raise WorkflowValidationError("Stage execution is unavailable before a real project is selected.")
                    if call["name"] == "prepare_project" and preparing:
                        from review_writer_api.schemas import ProjectCreateRequest
                        if set(call["arguments"]) - {"slug", "topic", "taxonomy_profile"}:
                            raise WorkflowValidationError("Unsupported project proposal fields.")
                        value = ProjectCreateRequest.model_validate(call["arguments"]).model_dump()
                        saved["project_proposal"] = value
                    elif call["name"] == "read_attachment":
                        args = call["arguments"]
                        if set(args) - {"attachment_id", "offset", "limit"} or args.get("attachment_id") not in {a["id"] for a in saved["base"].get("attachments", [])}:
                            raise WorkflowValidationError("Read only files attached in this discussion.")
                        value = service.attachments.read(principal, args["attachment_id"], offset=args.get("offset", 0), limit=args.get("limit", 12000))
                    elif call["name"] == "execute_intent":
                        parsed = ConversationIntent.model_validate(call["arguments"])
                        if parsed.request_quote != payload["message"]:
                            raise WorkflowValidationError("执行必须引用本轮用户的明确要求，历史内容不能授权操作。")
                        resolved = resolve_intent(parsed, service, principal, context.project_id, saved["base"]["project"])
                        request = resolved.get("request")
                        if request is None:
                            saved.update(reply=resolved["reply"], scope="discussion_only", activity={"phase": "completed"})
                            context.report_partial_result(saved)
                            return saved
                        operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, event["id"]))
                        context.report_partial_result(saved)
                        value = service.execution.submit(principal, context.project_id, request, message=payload["message"],
                            turn_id=saved["execution_id"], operation_id=operation_id, baseline=saved["base"]["project"], object_id=payload.get("object_id", ""), semantic_intent=parsed)
                        saved.setdefault("executions", []).append(value)
                        saved["tool_events"].append(event)
                        results.append({"tool": call["name"], "status": "succeeded", "value": {"job_id": value["id"]}})
                        goal_label = {"discovery": "文献筛选", "planning": "分析与大纲", "sections": "章节正文", "draft": "初稿", "final": "终稿"}.get(parsed.goal, parsed.goal)
                        reply = resolved.get("reply") or f"目标已保存：推进到{goal_label}。本次要求已提交后台执行；任务卡显示实际操作、生成及采用状态。"
                        saved.update(reply=reply, scope="scoped_execution", goal=parsed.goal)
                        saved["activity"] = {"phase": "submitted"}
                        context.report_partial_result(saved)
                        return saved
                    elif call["name"] in {"execute_action", "execute_plan"}:
                        args = call["arguments"]
                        if call["name"] == "execute_action":
                            if set(args) != {"action", "arguments", "request_quote"}:
                                raise WorkflowValidationError("Invalid execution request.")
                            request = {"steps": [{"action": args["action"], "arguments": args["arguments"]}], "request_quote": args["request_quote"]}
                        else:
                            request = args
                        intent = step.setdefault("execution_intents", {}).setdefault(str(position), {
                            "request": request, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, event["id"]))})
                        context.report_partial_result(saved)
                        value = service.execution.submit(principal, context.project_id, intent["request"], message=payload["message"],
                            turn_id=saved["execution_id"], operation_id=intent["id"], baseline=saved["base"]["project"], object_id=payload.get("object_id", ""))
                        saved.setdefault("executions", []).append(value)
                        saved["tool_events"].append(event)
                        results.append({"tool": call["name"], "status": "succeeded", "value": {"job_id": value["id"]}})
                        saved.update(reply="本次要求已提交后台执行。任务卡显示实际操作、生成及采用状态；提交不等于生成完成。", scope="scoped_execution")
                        saved["activity"] = {"phase": "submitted"}
                        context.report_partial_result(saved)
                        return saved
                    elif call["name"] == "stop_continuation":
                        from review_writer_api.domain_services.conversation_authorization import is_steering
                        if call["arguments"] or not is_steering(payload["message"]):
                            raise WorkflowValidationError("Stopping requires this user's explicit request.")
                        value = {"stopped_plans": service.execution.stop(principal, context.project_id, session_id=payload.get("session_id", "")), "business_jobs_cancelled": False}
                    elif call["name"] == "request_action":
                        args = call["arguments"]
                        if set(args) != {"action", "arguments"}:
                            raise WorkflowValidationError("Invalid action request.")
                        operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, event["id"]))
                        value = service.actions.proposal(principal, context.project_id, args["action"], args["arguments"], operation_id)
                        initial = saved["base"]["project"]
                        value["expected_artifacts"] = {a["logical_name"]: a["id"] for a in initial["artifacts"]}
                        value["expected_revisions"] = {s["id"]: s["revision"] for stage in initial["stages"] for s in stage["checkpoints"]}
                        saved.setdefault("actions", []).append(value)
                    else:
                        value = service.execute_read(principal, context.project_id, call["name"], call["arguments"])
                    if isinstance(value, dict) and value.get("artifact_id"):
                        saved["artifact_ids"] = list(dict.fromkeys([*saved["artifact_ids"], value["artifact_id"]]))
                    encoded = json.dumps(value, ensure_ascii=False)
                    if len(encoded) > 24000:
                        value = {"excerpt": encoded[:24000], "complete": False, "note": "Use bounded reads for the remaining content."}
                    results.append({"tool": call["name"], "status": "succeeded", "value": value})
                except (WorkflowError, TypeError, ValueError) as exc:
                    event["status"] = "failed"
                    results.append({"tool": call["name"], "status": "failed",
                                    "error": getattr(exc, "code", "INVALID_TOOL_ARGUMENTS"),
                                    "error_message": str(exc)[:1000] if isinstance(exc, WorkflowValidationError) else "操作未完成，请核对当前阶段状态及参数。"})
                saved["tool_events"].append(event)
                context.report_partial_result(saved)
                recent = saved["tool_events"][-2:]
                if len(recent) == 2 and all(item["name"] in {"execute_action", "execute_plan"} and item["status"] == "failed" for item in recent):
                    failures = [result for batch in saved["steps"] for result in batch.get("results", []) if result.get("status") == "failed"]
                    if len(failures) >= 2 and failures[-1].get("error_message") == failures[-2].get("error_message"):
                        raise WorkflowValidationError("接续操作未能提交：" + failures[-1]["error_message"] + " 已停止重复尝试，已有成果保留。")
            context.report_progress(index + 1, 6)
        failures = [result for step in saved["steps"] for result in step.get("results", []) if result.get("status") == "failed"]
        reason = failures[-1].get("error_message") if failures else None
        raise WorkflowValidationError("本轮未能提交或完成所需操作；已完成结果保留。" + (reason or "请明确当前需要执行的阶段操作；系统未追加模型请求。"))
    jobs.register_handler("conversation.turn", handler)
    jobs.register_handler("conversation.prepare", handler)
