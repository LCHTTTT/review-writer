"""Offline handoffs: actual services, no provider or account mutation."""
import json
import unittest
import time
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from review_writer_api.domain_services.conversation_context import stage_summary, tool_context
from review_writer_api.domain_services.conversation_authorization import authorize_action
from review_writer_api.job_handlers.conversation_execution import associated_jobs, complete_plan
from review_writer_api.errors import WorkflowConflict, WorkflowValidationError
from review_writer_api.tests.figure_test_support import NativeFigureApiTestCase
from review_writer_api.tests import test_conversation_execution_v2 as _execution_fixture


class ContextAndOwnershipTests(unittest.TestCase):
    def test_noncurrent_generated_artifact_is_unwrapped_and_next_step_is_scheduled(self):
        from copy import deepcopy
        from unittest.mock import Mock
        from review_writer_api.job_handlers.conversation_execution import register_conversation_execution
        from review_writer_api.job_service import JobYieldRequested
        from review_writer_core.workflow.artifacts import BLUEPRINT
        identifier = "36c1e77c-1631-4bf2-b539-61eb4bb51ef7"
        artifact = SimpleNamespace(id=identifier, project_id="project", logical_name=BLUEPRINT)
        # get_artifact returns OwnedArtifactRecord, unlike get_current_artifact.
        repo = Mock()
        action = {"name": "planning.blueprint"}
        plan = SimpleNamespace(result={"execution_id": "plan", "baseline": {}, "completed": [], "status": "waiting",
            "pending": {"action": action, "job_id": "action"}})
        action_job = SimpleNamespace(id="action", project_id="project", status="succeeded", result={
            "execution_completed": True, "outcome": {"job": {"id": "generation"}}})
        child = SimpleNamespace(id="generation", project_id="project", status="succeeded", result={"blueprint_artifact_id": identifier})
        repo.get_job.side_effect = lambda user, value: {"plan": plan, "action": action_job, "generation": child}[value]
        repo.get_artifact.return_value = SimpleNamespace(artifact=artifact, project_slug="project")
        repo.get_current_artifact.return_value = None
        conversation = Mock(repository=repo)
        conversation.steering_after.return_value = None
        conversation.overview.return_value = {"artifacts": []}
        conversation.card.return_value = {"id": identifier, "current": False}
        execution = Mock(conversation=conversation)
        jobs = Mock()
        register_conversation_execution(execution, jobs)
        handler = jobs.register_handler.call_args_list[0].args[1]
        snapshots = []
        context = SimpleNamespace(user_id="user", project_id="project", job_id="plan", repository=repo,
            checkpoint=lambda: None, report_partial_result=lambda value: snapshots.append(deepcopy(value)), report_progress=lambda *args: None)
        payload = {"source_turn_id": "turn", "steps": [{"action": "planning.blueprint", "arguments": {}},
            {"action": "blueprint.confirm", "arguments": {"artifact_id": {"step": 0, "key": "blueprint_artifact_id"}}}]}
        with patch("review_writer_api.job_handlers.conversation_execution.advance_baseline"), self.assertRaises(JobYieldRequested):
            handler(context, payload)
        self.assertEqual("planning.blueprint", snapshots[-1]["completed"][0]["action"])
        self.assertEqual(identifier, snapshots[-1]["completed"][0]["exported"]["blueprint_artifact_id"])
        self.assertEqual([{"id": identifier, "current": False}], snapshots[-1]["result_artifacts"])
        self.assertNotIn("pending", snapshots[-1])
        execution.submit_card.assert_not_called()  # completed planning was not replayed

    def test_analysis_completion_offers_choice_without_generating_an_outline(self):
        for action in ("discovery.confirm", "planning.analyze"):
            result = complete_plan({"completed": [{"action": action}]})
            self.assertEqual("选择大纲", result["next_step"]["label"])
            self.assertEqual("查看当前大纲选项", result["next_step"]["message"])
            self.assertEqual([{"action": action}], result["completed"])
            self.assertEqual("文献分析已完成，请选择大纲组织方式。", result["notice"])

    def test_stage_read_is_valid_structured_partial_data(self):
        source = {"paragraphs": [{"text": "x" * 120000}], "artifact_id": "saved-id"}
        result = stage_summary(source)
        self.assertFalse(result["complete"])
        self.assertEqual(120000, result["details"]["paragraphs"][0]["text"]["total_chars"])
        self.assertLess(len(json.dumps(result)), 20000)
        self.assertEqual(120000, len(source["paragraphs"][0]["text"]))

    def test_rolling_context_preserves_newest_source_and_original_results(self):
        steps = [{"results": [{"tool": "read_paper", "status": "succeeded", "value": {
            "paper_id": str(i), "text": str(i) * 20000, "next_offset": 20000}}]} for i in range(6)]
        output = tool_context(steps)
        self.assertTrue(output[0][0]["value"]["context_omitted"])
        self.assertEqual("0", output[0][0]["value"]["paper_id"])
        self.assertEqual(steps[-1]["results"], output[-1])
        self.assertEqual(20000, len(steps[0]["results"][0]["value"]["text"]))
        self.assertLess(len(json.dumps(output)), 70000)

    def test_library_acquisition_is_only_associated_with_its_actual_project(self):
        row = SimpleNamespace(project_id=None, scope="library", job_type="discovery.acquire", payload={"project_id": "owned"})
        repo = SimpleNamespace(get_job=lambda user, identifier: row)
        result = {"outcome": {"job": {"id": "actual"}}}
        self.assertEqual([row], associated_jobs(repo, "user", "owned", result))
        with self.assertRaises(WorkflowConflict):
            associated_jobs(repo, "user", "different", result)
        row.job_type = "library.download"
        with self.assertRaises(WorkflowConflict):
            associated_jobs(repo, "user", "owned", result)

    def test_no_implicit_download_or_overview_in_auto_continuation(self):
        for action in ("discovery.acquire", "draft.overview", "draft.adopt_overview"):
            with self.assertRaises(WorkflowValidationError):
                authorize_action("继续生成到初稿", "继续生成到初稿", action, {}, automatic=True, stop_at="draft")
        authorize_action("下载候选web-1", "下载候选web-1", "discovery.acquire", {"candidate_id": "web-1"})


class BoundedReadingIntegrationTests(NativeFigureApiTestCase):
    offline_text_provider_key = "offline-fixture-not-a-secret"
    job_poll_timeout_seconds = 25
    send = _execution_fixture.ConversationExecutionTests.send

    def extra_native_workflow_overrides(self):
        self.prompts = []
        def model(prompt, index):
            self.prompts.append(prompt)
            if index < 2:
                return {"reply": "", "tools": [{"name": "stage_details", "arguments": {"stage": "sections"}} for _ in range(3)]}
            return {"reply": "读取的是概览，不是全部正文。", "tools": []}
        return {"conversation.model": model}

    def test_multiple_large_reads_finish_without_context_failure(self):
        services = self.app.state.conversation_service.actions.services
        with patch.object(services.sections_service, "get", return_value={"sections": [{"text": "x" * 120000}]}), TestClient(self.app) as client:
            job = self.wait_job(client, self.send(client, "查看各阶段和全部章节", "large-reads")["id"])
        self.assertEqual("succeeded", job["status"], job)
        self.assertEqual(3, len(self.prompts))
        self.assertTrue(all(len(prompt) < 110000 for prompt in self.prompts))
        self.assertEqual(6, len(job["result"]["tool_events"]))


class OriginalStageHandoffTests(unittest.TestCase):
    def test_completion_from_existing_draft_runs_original_final_chain_without_chat_model(self):
        from review_writer_api.tests.test_draft_composition_v1 import DraftCompositionTests
        from review_writer_core.workflow.artifacts import FINAL_MANUSCRIPT, DRAFT_MANUSCRIPT
        case = DraftCompositionTests("runTest")
        case.setUp()
        self.addCleanup(case.tearDown)
        case.job_poll_timeout_seconds = 25
        with TestClient(case.app) as client:
            case.initialize(client)
            repository = case.app.state.workflow_repository
            before = repository.get_current_artifact(case.first.user_id, case.project_id, DRAFT_MANUSCRIPT).id
            message = "按当前流程继续，一直到完成综述"
            with patch("review_writer_api.job_handlers.conversation.call_model", side_effect=AssertionError("No chat provider should be used")):
                sent = client.post(f"/api/v1/projects/{case.project_id}/conversation/messages",
                    json={"message": message}, headers=case.headers("completion-final"))
                self.assertEqual(202, sent.status_code, sent.text)
                turn = case.wait_job(client, sent.json()["id"])
                self.assertEqual("succeeded", turn["status"], turn)
                plan = case.wait_job(client, turn["result"]["executions"][0]["id"])
                self.assertEqual("succeeded", plan["status"], plan)
            self.assertEqual(["draft.approve", "final.build"], [s["action"] for s in plan["result"]["completed"]])
            self.assertIsNotNone(repository.get_current_artifact(case.first.user_id, case.project_id, FINAL_MANUSCRIPT))
            self.assertEqual(before, repository.get_current_artifact(case.first.user_id, case.project_id, DRAFT_MANUSCRIPT).id)

    def test_download_refresh_then_selection_uses_original_acquisition_chain(self):
        from review_writer_api.tests.test_discovery_v1 import DiscoveryV1Tests
        from review_writer_api.domain_services.discovery import _candidate_id
        from review_writer_api.job_handlers.conversation import register_conversation_handler
        case = DiscoveryV1Tests("runTest")
        case.setUp()
        self.addCleanup(case.tearDown)
        case.job_poll_timeout_seconds = 25
        service, jobs = case.app.state.conversation_service, case.app.state.job_service
        request = {}
        for name in ("conversation.turn", "conversation.prepare"):
            jobs._handlers.pop(name)
        register_conversation_handler(service, jobs, None, model_call=lambda prompt, index: {
            "reply": "", "tools": [{"name": "execute_plan", "arguments": request}]})
        calls = []
        def download(context, payload):
            calls.append(payload)
            return {"results": [{"candidate_id": payload["candidates"][0]["candidate_id"], "paper_id": "P001"}]}
        with patch.dict(jobs._handlers, {"library.download": download}), TestClient(case.app) as client:
            case.discover(client)
            review = case.app.state.discovery_service.get(case.first, case.project_id)
            row = review["results"][0]["web_results"][0]
            candidate_id = _candidate_id(row, external=True)
            message = f"下载候选{candidate_id}并纳入P001"
            request.update(request_quote=message, steps=[
                {"action": "discovery.acquire", "arguments": {"candidate_id": candidate_id}},
                {"action": "discovery.selection", "arguments": {"candidate_ids": ["P001"], "selected": True}}])
            response = client.post(f"/api/v1/projects/{case.project_id}/conversation/messages",
                json={"message": message}, headers=case.headers("download-chain"))
            self.assertEqual(202, response.status_code, response.text)
            turn = case.wait_job(client, response.json()["id"])
            self.assertEqual("succeeded", turn["status"], turn)
            plan = case.wait_job(client, turn["result"]["executions"][0]["id"])
            self.assertEqual("succeeded", plan["status"], plan)
            self.assertEqual(2, len(plan["result"]["completed"]))
            self.assertEqual(1, len(calls))
            updated = case.app.state.discovery_service.get(case.first, case.project_id)
            self.assertEqual(["P001"], updated["selected_paper_ids"])
            self.assertGreater(updated["revision"], review["revision"])

    def test_recommend_then_adopt_uses_actual_worker_result_and_versions(self):
        from review_writer_api.tests.test_planning_v1 import PlanningV1Tests
        from review_writer_api.job_handlers.conversation import register_conversation_handler
        from review_writer_core.stages.planning.topic_recommendation import validate_recommendation
        case = PlanningV1Tests("runTest")
        case.setUp()
        self.addCleanup(case.tearDown)
        service, jobs = case.app.state.conversation_service, case.app.state.job_service
        message = "生成推荐大纲并采用"
        request = {"request_quote": message, "auto_adopt": True, "stop_at": "planning", "steps": [
            {"action": "planning.recommend_outline", "arguments": {}},
            {"action": "planning.apply_recommendation", "arguments": {"source_job_id": {"step": 0, "key": "job.id"}}}]}
        jobs._handlers.pop("conversation.turn")  # Isolated fixture: replace the live-model handler before workers start.
        jobs._handlers.pop("conversation.prepare")
        register_conversation_handler(service, jobs, None, model_call=lambda prompt, index: {
            "reply": "", "tools": [{"name": "execute_plan", "arguments": request}]})
        calls = []
        def build(context, payload):
            calls.append(payload)
            return validate_recommendation({"organization": "Scientific approaches", "sections": [
                {"title": "Approaches and evidence", "role": "body", "question": "How do approaches differ?",
                 "paper_ids": [p["paper_id"] for p in payload["papers"]], "rationale": "Compare selected studies."}
            ]}, payload["papers"])
        def wait(client, identifier):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                job = client.get("/api/v1/jobs/" + identifier).json()
                if job["status"] not in {"queued", "running", "cancel_requested"}:
                    return job
                time.sleep(.05)
            self.fail("Isolated job did not finish")
        with patch.dict(jobs._handlers, {"planning.topic-outline": build}), patch(
            "review_writer_api.domain_services.planning._topic_outline_intent", return_value={"available": False}), TestClient(case.app) as client:
            response = client.post(f"/api/v1/projects/{case.project_id}/conversation/messages",
                json={"message": message}, headers=case.headers())
            self.assertEqual(202, response.status_code, response.text)
            turn = wait(client, response.json()["id"])
            self.assertEqual("succeeded", turn["status"], turn)
            plan = wait(client, turn["result"]["executions"][0]["id"])
            self.assertEqual("succeeded", plan["status"], plan)
            self.assertEqual(2, len(plan["result"]["completed"]))
            self.assertEqual("生成章节规划", plan["result"]["next_step"]["label"])
            self.assertEqual(1, len(calls))
            chosen = case.app.state.planning_service.get(case.first, case.project_id)
            self.assertIn("Approaches and evidence", chosen["selected_outline_md"])
            self.assertEqual("topic-guided", chosen["outline_selection"]["outline_style"])
            source_id = plan["result"]["completed"][0]["exported"]["job"]["id"]
            action = service.actions.proposal(case.first, case.project_id, "planning.apply_recommendation", {"source_job_id": source_id}, "stale")
            original = case.app.state.planning_service.topic_recommendation_input(case.first, case.project_id)
            with patch.object(case.app.state.planning_service, "topic_recommendation_input", return_value={**original, "input_fingerprint": "changed", "source_scope_fingerprint": "changed-corpus"}):
                with self.assertRaises(WorkflowConflict):
                    service.actions.execute(case.first, case.project_id, action, "stale")
            with patch.object(case.app.state.planning_service, "topic_recommendation_input", return_value={**original, "input_fingerprint": "facts-updated"}):
                adopted = service.actions.execute(case.first, case.project_id, action, "facts-updated")
                self.assertIn("Approaches and evidence", adopted["selected_outline_md"])

    def test_overview_candidate_adoption_uses_original_saved_image(self):
        from review_writer_api.tests.test_draft_composition_v1 import DraftCompositionTests
        from PIL import Image
        case = DraftCompositionTests("runTest")
        case.setUp()
        self.addCleanup(case.tearDown)
        with TestClient(case.app) as client:
            drafts = case.initialize(client)
            final = case.app.state.final_service
            path = final.artifacts.workspace_manager.user_root(case.first.user_id) / "conversation-preview.png"
            Image.new("RGB", (80, 40), "white").save(path)
            payload = {**final.overview_payload(case.first, case.project_id), "preview_only": True}
            result = final.publish_overview(case.first, case.project_id, payload, {
                "output_path": str(path), "editable_text": {"title": "Review overview"}, "report": {}})
            self.assertNotIn(result["overview_artifact_id"], drafts.get(case.first, case.project_id)["manuscript_preview_md"])
            actions = case.app.state.conversation_service.actions
            action = actions.proposal(case.first, case.project_id, "draft.adopt_overview", {
                "image_id": result["overview_artifact_id"], "title": "Review overview"}, "adopt")
            actions.validate_baseline(case.first, case.project_id, action)
            actions.execute(case.first, case.project_id, action, "adopt")
            self.assertIn("/api/v1/artifacts/", drafts.get(case.first, case.project_id)["manuscript_preview_md"])
            self.assertTrue(final.overview_history(case.first, case.project_id)[0]["selected"])
