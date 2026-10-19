"""State-aware semantic routing; offline fixtures, no provider calls."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from review_writer_api.domain_services.conversation_intent import ConversationIntent, resolve_intent, intent_context
from review_writer_api.errors import WorkflowValidationError, WorkflowStageNotReady
from review_writer_api.tests.test_conversation_continuation import fixture
from review_writer_core.workflow import artifacts as names


@pytest.mark.parametrize("message", ["都用上，后面按建议写完", "就这些吧，帮我做到最后", "Take the recommendations and finish the manuscript", "能帮我用这些推荐写到最后吗？"])
def test_semantic_selection_uses_real_recommendations_not_command_keywords(message):
    service, project = fixture()
    service.actions.services.discovery_service = Mock()
    service.actions.services.discovery_service.get.return_value = {"results": [{"local_results": [
        {"paper_id": "P001", "score": 1, "recommendation_status": "recommended"},
        {"paper_id": "P002", "score": 1, "recommendation_status": "review"}],
        "web_results": [{"candidate_id": "web", "recommendation_status": "recommended"}]}]}
    intent = ConversationIntent(operation="select_recommended", goal="final", request_quote=message, auto_adopt=True)
    request = resolve_intent(intent, service, None, "project", project)["request"]
    assert request["steps"] == [{"action": "discovery.selection", "arguments": {"candidate_ids": ["P001"], "selected": True}},
                                 {"action": "discovery.confirm", "arguments": {}}]
    assert request["stop_at"] == "final"
    assert request["request_quote"] == message


def test_selection_only_never_starts_analysis():
    service, project = fixture()
    service.actions.services.discovery_service = Mock()
    service.actions.services.discovery_service.get.return_value = {"results": [{"local_results": [
        {"paper_id": "P001", "score": 1, "recommendation_status": "recommended"}]}]}
    request = resolve_intent(ConversationIntent(operation="select_recommended", goal="discovery",
        request_quote="就用这些，先保存选择", auto_adopt=False), service, None, "p", project)["request"]
    assert [step["action"] for step in request["steps"]] == ["discovery.selection"]


def test_missing_outline_uses_original_worker_and_typed_result_reference():
    service, project = fixture([names.MATRIX])
    request = resolve_intent(ConversationIntent(request_quote="后面你按推荐写完", auto_adopt=True), service, None, "p", project)["request"]
    assert [s["action"] for s in request["steps"]][:4] == ["planning.recommend_outline", "planning.apply_recommendation", "planning.blueprint", "blueprint.confirm"]
    assert request["steps"][1]["arguments"]["source_job_id"] == {"step": 0, "key": "job.id"}
    assert request["steps"][3]["arguments"]["artifact_id"] == {"step": 2, "key": "blueprint_artifact_id"}
    assert len(request["steps"]) <= 12
    assert not any(s["action"] in {"discovery.search", "images.redraw", "final.pdf", "final.export"} for s in request["steps"])


def test_generation_only_preserves_adoption_node():
    service, project = fixture([names.MATRIX])
    request = resolve_intent(ConversationIntent(request_quote="生成推荐大纲", auto_adopt=False), service, None, "p", project)["request"]
    assert [s["action"] for s in request["steps"]] == ["planning.recommend_outline"]


@pytest.mark.parametrize("message", ["这些都选上会怎样？", "后面按推荐写完，不采用大纲", "先思考，不要执行"])
def test_no_execution_or_adoption_cannot_be_erased_by_model(message):
    service, project = fixture()
    with pytest.raises(WorkflowValidationError):
        resolve_intent(ConversationIntent(request_quote=message, auto_adopt=True), service, None, "p", project)


def test_context_is_bounded_and_normal_empty_is_not_failure():
    service = SimpleNamespace(actions=SimpleNamespace(services=SimpleNamespace(discovery_service=Mock())))
    service.actions.services.discovery_service.get.side_effect = WorkflowStageNotReady()
    assert intent_context(service, None, "p")["recommendations"]["available"] is False
    service.actions.services.discovery_service.get.side_effect = None
    service.actions.services.discovery_service.get.return_value = {"results": [{"local_results": [
        {"paper_id": f"P{i}", "title": "Title", "score": 1, "recommendation_status": "recommended"} for i in range(70)]}]}
    value = intent_context(service, None, "p")["recommendations"]
    assert value["count"] == 70 and len(value["items"]) == 40 and not value["complete"]


def test_string_boolean_is_not_automatic_permission():
    with pytest.raises(ValidationError):
        ConversationIntent(request_quote="后面按推荐写完", auto_adopt="true")


def test_persisted_goal_extends_after_analysis_and_keeps_real_result_references():
    from review_writer_api.job_handlers.conversation_execution import extend_goal, resolve_arguments
    service, project = fixture([names.MATRIX])
    project["jobs"] = [{"id": "plan", "job_type": "conversation.plan"}]
    service.overview = Mock(return_value=project)
    execution = SimpleNamespace(conversation=service, actions=service.actions)
    saved = {"execution_id": "plan", "semantic_intent": {"goal": "final", "auto_adopt": True,
        "operation": "select_recommended", "request_quote": "都用上，后面按建议写完"},
        "steps": [{"action": "discovery.selection"}, {"action": "discovery.confirm"}],
        "scope": {"actions": ["discovery.selection", "discovery.confirm"]}}
    assert extend_goal(execution, None, "p", saved)
    assert len(saved["steps"]) == 13  # two bounded chunks, not model step-budget failure
    assert saved["steps"][3]["arguments"]["source_job_id"] == {"step": 2, "key": "job.id"}
    assert saved["steps"][5]["arguments"]["artifact_id"] == {"step": 4, "key": "blueprint_artifact_id"}
    completed = [{}, {}, {"exported": {"job": {"id": "real-outline-job"}}}]
    assert resolve_arguments(saved["steps"][3]["arguments"], completed) == {"source_job_id": "real-outline-job"}
    # Resume keeps the extended sequence, and a current Final terminates it.
    project["artifacts"].append({"logical_name": names.FINAL_MANUSCRIPT, "id": "final"})
    assert not extend_goal(execution, None, "p", saved)
    assert len(saved["steps"]) == 13


def test_persisted_goal_cannot_ignore_other_users_operations_or_changed_draft():
    from review_writer_api.job_handlers.conversation_execution import extend_goal
    from review_writer_api.errors import WorkflowConflict
    service, project = fixture([names.DRAFT_MANUSCRIPT])
    service.overview = Mock(return_value=project)
    service.actions.services.drafts_service.get.return_value["freshness"]["upstream_stale"] = True
    execution = SimpleNamespace(conversation=service, actions=service.actions)
    saved = {"execution_id": "plan", "semantic_intent": {"goal": "final", "auto_adopt": True,
        "request_quote": "后面按推荐写完"}, "steps": [], "scope": {"actions": []}}
    with pytest.raises(WorkflowConflict, match="已有修改"):
        extend_goal(execution, None, "p", saved)
    project["jobs"] = [{"id": "other", "job_type": "sections.generate"}]
    with pytest.raises(WorkflowConflict, match="已有任务"):
        extend_goal(execution, None, "p", saved)


def test_semantic_target_must_be_current_and_scoped_to_whole_review():
    service, project = fixture([names.DRAFT_MANUSCRIPT])
    for changes in ({"target_artifact_id": "foreign-or-history"}, {"section_id": "S02"}):
        with pytest.raises(WorkflowValidationError):
            resolve_intent(ConversationIntent(request_quote="后面按推荐写完", auto_adopt=True, **changes), service, None, "p", project)
    assert resolve_intent(ConversationIntent(request_quote="这份稿件继续写完", auto_adopt=True,
        target_artifact_id="actual-id"), service, None, "p", project)["request"]["steps"][0]["action"] == "draft.approve"


def test_internal_semantic_submission_cannot_be_used_to_inject_extra_operations():
    from review_writer_api.domain_services.conversation_execution import ConversationExecution
    from review_writer_api.security import Principal, Role
    service, project = fixture([names.DRAFT_MANUSCRIPT])
    service._owned_project = Mock()
    jobs = Mock()
    executor = ConversationExecution(service, service.actions, jobs)
    principal = Principal("owner", frozenset({Role.USER}))
    message = "后面按推荐写完"
    intent = ConversationIntent(request_quote=message, auto_adopt=True)
    request = resolve_intent(intent, service, principal, "p", project)["request"]
    request["steps"].append({"action": "final.pdf", "arguments": {}})
    with pytest.raises(WorkflowValidationError, match="不一致"):
        executor.submit(principal, "p", request, message=message, turn_id="turn",
            operation_id="plan", baseline=project, semantic_intent=intent)
    jobs.submit.assert_not_called()


def test_saved_selection_can_be_confirmed_without_selecting_again_or_rescreening():
    service, project = fixture([names.DISCOVERY_REVIEW])
    service.actions.services.discovery_service.get.return_value = {"selected_paper_ids": ["P001"]}
    request = resolve_intent(ConversationIntent(request_quote="后面的流程按推荐做完", auto_adopt=True), service, None, "p", project)["request"]
    assert [step["action"] for step in request["steps"]] == ["discovery.confirm"]


def test_retry_excludes_its_own_lease_but_not_another_running_job():
    from review_writer_api.job_handlers.conversation_execution import extend_goal
    service, project = fixture([names.MATRIX])
    project["jobs"] = [{"id": "retry-plan", "job_type": "conversation.plan"}]
    service.overview = Mock(return_value=project)
    execution = SimpleNamespace(conversation=service, actions=service.actions)
    saved = {"execution_id": "old-plan", "semantic_intent": {"goal": "final", "auto_adopt": True,
        "request_quote": "后面按推荐写完"}, "steps": [], "scope": {"actions": []}}
    assert extend_goal(execution, None, "p", saved, current_job_id="retry-plan")


def test_automatic_cutoff_at_outline_does_not_generate_chapters():
    from review_writer_api.job_handlers.conversation_execution import extend_goal
    service, project = fixture([names.MATRIX])
    service.overview = Mock(return_value=project)
    execution = SimpleNamespace(conversation=service, actions=service.actions)
    saved = {"execution_id": "plan", "semantic_intent": {"goal": "planning", "auto_adopt": True,
        "request_quote": "这些都用，按推荐做到大纲就停"}, "steps": [], "scope": {"actions": []}}
    assert extend_goal(execution, None, "p", saved)
    assert [s["action"] for s in saved["steps"]] == ["planning.recommend_outline", "planning.apply_recommendation"]
    project["artifacts"].append({"logical_name": names.PLANNING_OUTLINE, "id": "outline"})
    assert not extend_goal(execution, None, "p", saved)


def test_raw_automatic_plan_cannot_expand_recommendations_to_entire_library():
    from review_writer_api.domain_services.conversation_authorization import authorize_action
    with pytest.raises(WorkflowValidationError):
        authorize_action("后面按推荐写完", "后面按推荐写完", "discovery.select_library", {}, automatic=True, stop_at="final")


@pytest.mark.parametrize("goal", ["sections", "draft"])
def test_select_and_continue_preserves_the_requested_cutoff(goal):
    service, project = fixture()
    service.actions.services.discovery_service.get.return_value = {"results": [{"local_results": [
        {"paper_id": "P001", "score": 1, "recommendation_status": "recommended"}]}]}
    request = resolve_intent(ConversationIntent(operation="select_recommended", goal=goal,
        request_quote="这些都用上，按推荐推进到我指定的阶段", auto_adopt=True), service, None, "p", project)["request"]
    assert request["stop_at"] == goal


def test_assemble_from_approved_inputs_does_not_require_extra_automatic_permission():
    service, project = fixture(blueprint_current=True, active_blueprint_approved=True)
    project["stages"] = [{"checkpoints": [{"id": "sections", "status": "approved"}, {"id": "figures", "status": "approved"}]}]
    request = resolve_intent(ConversationIntent(goal="draft", request_quote="请把当前内容组装好", auto_adopt=False), service, None, "p", project)["request"]
    assert [step["action"] for step in request["steps"]] == ["draft.assemble"]
