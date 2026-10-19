"""Durable deterministic action execution, separate from dialogue model calls."""
from review_writer_api.conversation_feature import conversation_enabled

from review_writer_api.errors import WorkflowConflict, WorkflowValidationError
from review_writer_api.security import Principal, Role


def register_conversation_actions(actions, jobs):
    def handler(context, payload):
        principal = Principal(context.user_id, frozenset({Role.USER}))
        actions.conversation._owned_project(principal, context.project_id)
        context.checkpoint()
        if not conversation_enabled():
            raise WorkflowValidationError("Conversation actions are disabled; stage pages remain available.")
        if payload.get("parent_plan_id"):
            parent_id = payload["parent_plan_id"]
            parent = context.repository.get_job(context.user_id, parent_id)
            session_id = parent.payload.get("session_id", "") if parent else ""
            latest = context.repository.get_current_job(context.user_id, scope="project", project_id=context.project_id,
                                                        job_type="conversation.plan", operation_key="project-continuation" + (":" + session_id if session_id else ""))
            cursor, found = latest, False
            for _ in range(16):
                if not cursor or cursor.project_id != context.project_id:
                    break
                if cursor.id == parent_id:
                    found = True
                    break
                cursor = context.repository.get_job(context.user_id, cursor.retry_of_job_id) if cursor.retry_of_job_id else None
            if (not parent or parent.project_id != context.project_id or parent.job_type != "conversation.plan"
                    or not found or latest.status not in {"queued", "running"} or latest.cancellation_requested):
                raise WorkflowConflict("接续已停止或授权上下文失效，尚未执行的操作未提交。")
            steering = actions.conversation.steering_after(principal, context.project_id, payload["source_turn_id"], include_finished=True)
            if steering:
                raise WorkflowConflict("已收到后续调整，尚未执行的采用或修改已暂停。")
        current = context.repository.get_job(context.user_id, context.job_id)
        previous = dict(current.result or {})
        if previous.get("execution_completed"):
            return previous
        if previous.get("execution_started"):
            # A write may have committed before acknowledgement was stored. Do
            # not replay it or a paid submission merely because a worker died.
            raise WorkflowConflict("上次操作结果需核对；为避免重复保存或收费，没有自动重放。请查看实际阶段成果和任务。")
        action = payload["action"]
        actions.validate_baseline(principal, context.project_id, action)
        context.report_partial_result({"execution_started": True, "action_id": action["id"]})
        from review_writer_api.submission_context import bind_submission_model_source
        with bind_submission_model_source(payload["source_turn_id"] if payload.get("parent_plan_id") else None):
            result = actions.execute(principal, context.project_id, action, "conversation-action:" + action["id"])
        keys = {"project_id", "revision", "status", "next_stage", "next_path", "synthesis_job_id",
                "synthesis_warning", "matrix_analysis_job", "job", "candidate_ids", "cancelled_job", "edit_preview",
                "paper_id", "metadata_artifact_id", "changed_fields"}
        keys.update(key for key in result if key == "artifact_ids" or key.endswith("_artifact_id"))
        current_overview = actions.conversation.overview(principal, context.project_id)
        completed = {"execution_completed": True, "action_id": action["id"], "label": action["label"],
                     "outcome": {key: value for key, value in result.items() if key in keys},
                     "result_artifacts": [a for a in current_overview["artifacts"]
                                          if action["expected_artifacts"].get(a["logical_name"]) != a["id"]]}
        context.report_partial_result(completed)
        return completed
    jobs.register_handler("conversation.action", handler)
