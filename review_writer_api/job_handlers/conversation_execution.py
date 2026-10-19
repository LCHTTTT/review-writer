"""Lease-backed continuation of shared operations; yields while original workers run."""
from __future__ import annotations

from copy import deepcopy

from review_writer_api.conversation_feature import conversation_enabled
from review_writer_api.domain_services.conversation_execution import ResultReference, operation_id
from review_writer_api.domain_services.conversation_actions import action_dependencies
from review_writer_api.errors import WorkflowConflict, WorkflowValidationError
from review_writer_api.job_service import JobYieldRequested
from review_writer_api.security import Principal, Role
from review_writer_api.application_operations import job_belongs_to_project

ACTIVE = {"queued", "running", "cancel_requested"}
STAGE = {"discovery": "discovery", "planning": "blueprint", "blueprint": "blueprint", "sections": "sections",
         "images": "figures", "draft": "draft", "final": "final", "tasks": "tasks", "library": "library", "matrix": "matrix"}


def resolve_arguments(arguments, completed):
    resolved = {}
    for key, value in arguments.items():
        if isinstance(value, dict) and key in {"artifact_id", "candidate_id", "source_job_id"}:
            ref = ResultReference.model_validate(value)
            output = completed[ref.step]["exported"]
            for part in ref.key.split("."):
                output = output[int(part)] if isinstance(output, list) else output.get(part)
            if not isinstance(output, str) or not output:
                raise WorkflowConflict("前一步没有产生所需的实际输出；未猜测候选或任务身份。")
            resolved[key] = output
        else:
            resolved[key] = value
    return resolved


def associated_jobs(repository, user_id, project_id, result):
    outcome = result.get("outcome") or {}
    ids = [outcome[key]["id"] for key in ("job", "matrix_analysis_job") if isinstance(outcome.get(key), dict)]
    if outcome.get("synthesis_job_id"):
        ids.append(outcome["synthesis_job_id"])
    records = [repository.get_job(user_id, value) for value in dict.fromkeys(ids)]
    if any(not job_belongs_to_project(row, project_id) for row in records):
        raise WorkflowConflict("后续任务不属于当前项目。")
    return records


def artifact_ids(value):
    """Only actual returned UUIDs can account for changed immutable pointers."""
    import uuid
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "artifact_ids" and isinstance(item, dict):
                for identifier in item.values():
                    try:
                        found.add(str(uuid.UUID(identifier)))
                    except (ValueError, TypeError, AttributeError):
                        pass
            elif key == "artifact_id" or key.endswith("_artifact_id"):
                try:
                    found.add(str(uuid.UUID(item)))
                except (ValueError, TypeError, AttributeError):
                    pass
            else:
                found.update(artifact_ids(item))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                found.update(artifact_ids(item))
    return found


def advance_baseline(actions, principal, project_id, saved, action, action_job, children, remaining):
    current = actions.conversation.overview(principal, project_id)
    expected = saved["baseline"]
    refs = {a["logical_name"]: a["id"] for a in current["artifacts"]}
    revisions = {s["id"]: s["revision"] for stage in current["stages"] for s in stage["checkpoints"]}
    needed_names, needed_states = set(), set()
    if saved.get("semantic_intent", {}).get("auto_adopt"):
        # The goal outlives this bounded chunk. Protect future dependencies
        # BEFORE rebasing, including edits made while paper analysis runs.
        future = ("planning.recommend_outline", "planning.apply_recommendation", "planning.blueprint", "sections.generate",
                  "images.confirm", "draft.assemble", "draft.approve", "final.build")
        cutoff = {"discovery": 0, "planning": 2, "sections": 4, "draft": 6, "final": 8}[saved["semantic_intent"]["goal"]]
        remaining = [*remaining, *({"action": name} for name in future[:cutoff])]
    for step in remaining:
        names, states = action_dependencies(step["action"])
        needed_names.update(names)
        needed_states.update(states)
    proven_ids = artifact_ids(action_job.result)
    owners = {action_job.id, *(row.id for row in children)}
    for row in children:
        proven_ids.update(artifact_ids(row.result))
        # Existing chapter revision batches delegate paragraph jobs. Include
        # only persisted owned descendants of those exact recorded parents.
        for child in actions.conversation.repository.list_project_jobs(principal.user_id, project_id, job_type="draft.rewrite", limit=500):
            if (child.payload.get("dialogue") or {}).get("batch_job_id") == row.id:
                owners.add(child.id)
    for name in needed_names:
        old, new = expected["expected_artifacts"].get(name, ""), refs.get(name, "")
        if old == new:
            continue
        owned = False
        if new:
            artifact = actions.conversation.repository.get_current_artifact(principal.user_id, project_id, name)
            run = actions.conversation.repository.get_stage_run(principal.user_id, project_id, artifact.producer_run_id) if artifact.producer_run_id else None
            owned = new in proven_ids or bool(run and run.metadata.get("_workflow_job_id") in owners)
        if not owned:
            raise WorkflowConflict("等待期间关联成果被其他操作替换，接续已暂停；未覆盖新成果。")
    outcome = action_job.result.get("outcome") or {}
    stage = {"planning.analyze": "matrix", "planning.save_outline": "matrix", "planning.apply_recommendation": "matrix",
             "draft.publication": "final", "draft.adopt_overview": "final"}.get(
        action["name"], STAGE[action["name"].split(".")[0]])
    acknowledged = {stage: outcome["revision"]} if "revision" in outcome else {}
    for child in children:
        if isinstance(child.result.get("revision"), int):
            acknowledged[STAGE.get(child.job_type.split(".")[0], stage)] = child.result["revision"]
    for key in needed_states:
        if revisions.get(key, 0) == expected["expected_revisions"].get(key, 0):
            continue
        state = actions.conversation.repository.get_stage_state(principal.user_id, project_id, key)
        run = actions.conversation.repository.get_stage_run(principal.user_id, project_id, state.current_run_id) if state and state.current_run_id else None
        if revisions.get(key, 0) != acknowledged.get(key) and not (run and run.metadata.get("_workflow_job_id") in owners):
            raise WorkflowConflict("等待期间检查点版本变化无法归属本次操作，接续已暂停。")
    saved["baseline"] = {"expected_artifacts": refs, "expected_revisions": revisions}


def complete_plan(saved):
    """Explain the actual completed scope; never start unrequested downstream jobs."""
    next_commands = {
        "discovery.search": ("选择要纳入的文献", "Select papers to include", "全部纳入推荐结果"),
        "discovery.acquire": ("选择下载后的文献", "Select acquired papers", "查看本项目文献"),
        "discovery.confirm": ("选择大纲", "Choose an outline", "查看当前大纲选项"),
        "planning.analyze": ("选择大纲", "Choose an outline", "查看当前大纲选项"),
        "planning.recommend_outline": ("查看并采用大纲", "Review and adopt the outline", "查看当前推荐大纲"),
        "planning.apply_recommendation": ("生成章节规划", "Generate chapter planning", "生成章节规划"),
        "planning.save_outline": ("生成章节规划", "Generate chapter planning", "生成章节规划"),
        "planning.blueprint": ("查看并采用章节规划", "Review and adopt chapter planning", "查看当前章节规划"),
        "blueprint.confirm": ("生成章节正文", "Generate section drafts", "生成所有章节正文"),
        "sections.generate": ("查看并确认正文", "Review and confirm section drafts", "查看当前章节正文"),
        "sections.confirm": ("处理正文配图", "Prepare manuscript figures", "保留原图并确认图像"),
        "images.preserve_sources": ("确认配图", "Confirm figures", "确认当前图像"),
        "images.confirm": ("组装初稿", "Assemble the Draft", "组装初稿"),
        "draft.assemble": ("查看并修改初稿", "Review and edit the Draft", "查看当前初稿"),
        "draft.overview": ("查看并保存总览图候选", "Review and save the overview candidate", "查看刚生成的总览图"),
        "draft.approve": ("组装终稿", "Build the Final", "组装终稿"),
        "final.build": ("导出稿件", "Export manuscript", "导出Word"),
    }
    last = saved["completed"][-1]["action"] if saved["completed"] else ""
    saved.update(status="completed", notice="本次授权操作已完成；下方显示实际成果和下一步，未执行授权范围外的操作。")
    if last in {"discovery.confirm", "planning.analyze"}:
        saved["notice"] = "文献分析已完成，请选择大纲组织方式。"
    if last in next_commands:
        label, english, message = next_commands[last]
        saved["next_step"] = {"label": label, "label_en": english, "message": message}
    return saved


def extend_goal(execution, principal, project_id, saved, *, current_job_id=None):
    goal = saved.get("semantic_intent")
    if not goal or not goal.get("auto_adopt") or goal.get("goal") == "discovery":
        return False
    from review_writer_api.domain_services.conversation_intent import ConversationIntent, resolve_intent, goal_reached
    current = execution.conversation.overview(principal, project_id)
    # Only our lease-backed plan is excluded; another running operation
    # still prevents duplicate generation.
    current["jobs"] = [row for row in current.get("jobs", []) if row.get("id") not in {saved["execution_id"], current_job_id}]
    canonical = ConversationIntent(goal=goal["goal"], request_quote=goal["request_quote"], auto_adopt=True)
    outcome = resolve_intent(canonical, execution.conversation, principal, project_id, current)
    if not outcome.get("request"):
        if not goal_reached(canonical, execution.conversation, principal, project_id, current):
            raise WorkflowConflict(outcome["reply"])
        return False
    steps = outcome["request"]["steps"]
    offset = len(saved["steps"])
    if offset + len(steps) > 64:
        raise WorkflowConflict("接续状态没有收敛，已暂停并保留成果，请检查当前阶段。")
    for spec in steps:
        for value in spec["arguments"].values():
            if isinstance(value, dict) and "step" in value:
                value["step"] += offset
    saved["steps"].extend(steps)
    saved["scope"]["actions"].extend(spec["action"] for spec in steps)
    saved.update(status="running", notice="当前阶段已完成，按已保存的目标继续下一阶段。")
    return True


def register_conversation_execution(execution, jobs):

    def handler(context, payload):
        principal = Principal(context.user_id, frozenset({Role.USER}))
        execution.conversation._owned_project(principal, context.project_id)
        context.checkpoint()
        if not conversation_enabled():
            raise WorkflowValidationError("对话接续已关闭，原阶段仍可独立使用。")
        current = context.repository.get_job(context.user_id, context.job_id)
        saved = deepcopy(current.result or {})
        source = current
        while not saved and source.retry_of_job_id:
            source = context.repository.get_job(context.user_id, source.retry_of_job_id)
            if source is None:
                break
            saved = deepcopy(source.result or {})
        if not saved:
            saved = {"execution_id": context.job_id, "baseline": payload["baseline"], "completed": [], "status": "running",
                     "scope": {"stop_at": payload.get("stop_at"), "auto_adopt": payload.get("auto_adopt", False),
                               "actions": [step["action"] for step in payload["steps"]]}}
        saved.setdefault("steps", deepcopy(payload["steps"]))
        if payload.get("semantic_intent"):
            saved.setdefault("semantic_intent", payload["semantic_intent"])
        if saved.get("status") == "completed":
            return saved
        context.report_partial_result(saved)
        if execution.conversation.steering_after(principal, context.project_id, payload["source_turn_id"], include_finished=True):
            saved.update(status="paused", notice="本次要求已被后续调整取代，尚未执行的采用／接续停止；已提交生成任务保留。")
            context.report_partial_result(saved)
            raise WorkflowConflict(saved["notice"])
        position = len(saved["completed"])
        if position >= len(saved["steps"]):
            if extend_goal(execution, principal, context.project_id, saved, current_job_id=context.job_id):
                context.report_partial_result(saved)
                raise JobYieldRequested(delay_seconds=0, queue_reason="conversation_next_step")
            complete_plan(saved)
            context.report_partial_result(saved)
            return saved
        spec = saved["steps"][position]
        try:
            if not saved.get("pending"):
                arguments = resolve_arguments(spec["arguments"], saved["completed"])
                action = execution.actions.proposal(principal, context.project_id, spec["action"], arguments,
                                                    operation_id(saved["execution_id"], position))
                action.update(saved["baseline"])
                execution.actions.validate_baseline(principal, context.project_id, action)
                saved["pending"] = {"action": action}
                context.report_partial_result(saved)  # durable intent before submission
            pending = saved["pending"]
            context.checkpoint()
            if not pending.get("job_id"):
                record = execution.submit_card(principal, context.project_id, payload["source_turn_id"], pending["action"], parent_plan_id=saved["execution_id"])
                pending["job_id"] = record.id
                context.report_partial_result(saved)
            action_job = context.repository.get_job(context.user_id, pending["job_id"])
            if not action_job or action_job.project_id != context.project_id:
                raise WorkflowConflict("执行任务不可用。")
            if action_job.status in ACTIVE:
                saved.update(status="waiting", notice="正在等待已提交的操作；不占用模型调用等待。")
                context.report_partial_result(saved)
                raise JobYieldRequested(delay_seconds=2, queue_reason="conversation_waiting")
            if action_job.status != "succeeded" or not action_job.result.get("execution_completed"):
                raise WorkflowConflict(action_job.error_message or "操作未完成；保留已有成果，未自动重放。")
            children = associated_jobs(context.repository, context.user_id, context.project_id, action_job.result)
            pending["business_job_ids"] = [row.id for row in children]
            if any(row.status in ACTIVE for row in children):
                saved.update(status="waiting", notice="生成任务仍在按原要求执行；后续采用尚未开始。")
                context.report_partial_result(saved)
                raise JobYieldRequested(delay_seconds=2, queue_reason="conversation_waiting")
            if any(row.status != "succeeded" for row in children):
                raise WorkflowConflict("生成任务失败或中断，已有结果已保留，接续停止；未整批重跑。")
            advance_baseline(execution.actions, principal, context.project_id, saved, pending["action"], action_job, children, saved["steps"][position + 1:])
            exported = dict(action_job.result.get("outcome") or {})
            if children:
                exported.update(children[0].result)
                exported["job"] = {"id": children[0].id}
                if pending["action"]["name"] == "draft.revise_paragraph":
                    from review_writer_api.domain_services.conversation_draft_edits import readable_preview
                    candidate_id = children[0].result.get("candidate_id")
                    candidate = next((row for row in execution.actions.services.drafts_service.get(principal, context.project_id)["rewrite_candidates"]
                                      if row.get("candidate_id") == candidate_id), None)
                    if candidate:
                        exported["edit_preview"] = readable_preview(candidate)
            saved["completed"].append({"action": pending["action"]["name"], "job_id": action_job.id,
                                       "business_job_ids": [row.id for row in children], "exported": exported})
            cards = {a["id"]: a for a in saved.get("result_artifacts", [])}
            for card in action_job.result.get("result_artifacts", []):
                cards[card["id"]] = card
            returned = set().union(*(artifact_ids(row.result) for row in children)) if children else set()
            current_cards = execution.conversation.overview(principal, context.project_id)["artifacts"]
            for card in current_cards:
                if card["id"] in returned:
                    cards[card["id"]] = card
            # Preview-only images are historical candidates, not current pointers.
            from review_writer_api.domain_services.conversation import RESULTS
            for identifier in returned - cards.keys():
                owned = execution.conversation.repository.get_artifact(principal.user_id, identifier)
                artifact = owned.artifact if owned else None
                if artifact and artifact.project_id == context.project_id:
                    for stage, label, name in RESULTS:
                        if name == artifact.logical_name:
                            current = execution.conversation.repository.get_current_artifact(principal.user_id, context.project_id, name)
                            cards[identifier] = execution.conversation.card(artifact, stage, label, bool(current and current.id == identifier))
                            break
            current_ids = {card["id"] for card in current_cards}
            saved["result_artifacts"] = [{**card, "current": card["id"] in current_ids} for card in cards.values()]
            saved.pop("pending", None)
            saved.update(status="running", notice="前一步已核验，准备执行授权范围内的下一步。")
            context.report_partial_result(saved)
            context.report_progress(position + 1, len(saved["steps"]))
            if position + 1 == len(saved["steps"]):
                if extend_goal(execution, principal, context.project_id, saved, current_job_id=context.job_id):
                    context.report_partial_result(saved)
                    raise JobYieldRequested(delay_seconds=0, queue_reason="conversation_next_step")
                complete_plan(saved)
                context.report_partial_result(saved)
                return saved
            raise JobYieldRequested(delay_seconds=0, queue_reason="conversation_next_step")
        except WorkflowConflict as exc:
            saved.update(status="paused", notice=str(exc), next_action=spec["action"])
            context.report_partial_result(saved)
            raise
    jobs.register_handler("conversation.plan", handler)

    def control(context, payload):
        principal = Principal(context.user_id, frozenset({Role.USER}))
        execution.conversation._owned_project(principal, context.project_id)
        context.checkpoint()
        current = context.repository.get_job(context.user_id, context.job_id)
        stopped = execution.stop(principal, context.project_id, before=current.created_at, session_id=payload.get("session_id", ""))
        return {"reply": "已请求停止后续接续／采用；已经提交的生成任务保留，未调用对话模型。",
                "stopped_plan_ids": [row["id"] for row in stopped], "business_jobs_cancelled": False}

    jobs.register_handler("conversation.control", control)
