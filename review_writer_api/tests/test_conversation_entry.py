"""Offline retrieval handoff, original-stage reuse and readable failures."""
import json
import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import update

from review_writer_api.database import AIModelRequest, database_session
from review_writer_api.domain_services.conversation_entry import discovery_entry, recommendation_entry
from review_writer_api.domain_services.conversation_authorization import authorize_action
from review_writer_api.domain_services.project_papers import patch_selection, recommended_paper_ids
from review_writer_api.errors import ConversationModelFailure, WorkflowValidationError
from review_writer_api.tests import test_discovery_v1 as fixtures
from review_writer_api.workflow_models import WorkflowJob
from review_writer_core.model_gateway_client import GatewayRequestError
from review_writer_core.workflow.artifacts import MATRIX


def project(**changes):
    return {"topic": "General academic topic", "artifacts": [], "jobs": [], **changes}


class EntryIntentTests(unittest.TestCase):
    def test_current_turn_network_switch_controls_fast_and_model_action_authorization(self):
        from review_writer_api.domain_services.conversation_authorization import web_search_requested
        for preference, message, allowed in (
            (False, "请联网搜索石墨烯电池", False),
            (True, "请检索石墨烯电池", True),
            (True, "搜索石墨烯电池，不要联网，只搜索本地", False),
            (None, "请联网搜索石墨烯电池", True),
            (None, "请检索石墨烯电池", False),
        ):
            with self.subTest(preference=preference, message=message):
                self.assertEqual(allowed, web_search_requested(message, preference))
                self.assertEqual(allowed, discovery_entry(message, project(), web_search=preference)["steps"][0]["arguments"]["web_search"])
                if allowed:
                    authorize_action(message, message, "discovery.search", {"web_search": True}, web_search=preference)
                else:
                    with self.assertRaises(WorkflowValidationError):
                        authorize_action(message, message, "discovery.search", {"web_search": True}, web_search=preference)

    def test_saved_recommendations_respect_exclusions_and_do_not_adopt_web_candidates(self):
        review = {"results": [{"local_results": [
            {"paper_id": "P1", "score": 1, "recommendation_status": "recommended"},
            {"paper_id": "P2", "score": 1, "recommendation_status": "review"},
            {"paper_id": "P3", "score": 0, "recommendation_status": "recommended"},
            {"paper_id": "P4", "score": 1, "recommendation_status": "recommended"}],
            "web_results": [{"candidate_id": "web", "recommendation_status": "recommended"}]},
            {"local_results": [{"paper_id": "P1", "score": 1, "recommendation_status": "recommended"}, {"paper_id": "P4", "score": 1, "role": "excluded"}]},
            {"keep": False, "local_results": [{"paper_id": "P5", "score": 1, "recommendation_status": "recommended"}]}]}
        self.assertEqual(["P1"], recommended_paper_ids(review))

    def test_recommendation_adoption_does_not_override_discussion_or_save_only(self):
        for message in ("全部纳入推荐结果会怎样？", "仅纳入全部推荐，不要分析", "全部纳入推荐结果，先不执行"):
            self.assertIsNone(recommendation_entry(message, None, None, "project"))
        self.assertIsNone(recommendation_entry("全部纳入推荐结果", None, None, "project", attachment_ids=["untrusted-file"]))
        authorize_action("全部纳入推荐结果", "全部纳入推荐结果", "discovery.confirm", {})
        with self.assertRaises(WorkflowValidationError):
            authorize_action("全部纳入推荐结果", "全部纳入推荐结果", "draft.approve", {})
    def test_explicit_gateway_unknown_outcome_wins_over_http_422_normalization(self):
        message = str(ConversationModelFailure.from_provider({"code": "MODEL_RESULT_UNKNOWN", "category": "unknown", "provider_status": 422}))
        self.assertIn("总等待时限", message)
        self.assertIn("未自动重发", message)

    def test_fresh_review_routes_to_stage_search_with_full_user_constraints(self):
        message = 'Please write a review on the topic "syntheses of the axial-chiral allenes", categorized by substrates.'
        request = discovery_entry(message, project())
        self.assertEqual(message, request["steps"][0]["arguments"]["topic"])
        self.assertEqual(["discovery.search"], [step["action"] for step in request["steps"]])
        self.assertFalse(request["steps"][0]["arguments"]["web_search"])
        self.assertEqual("planning", request["stop_at"])

    def test_plain_search_and_local_only_are_commands_not_scientific_rules(self):
        for message in ("请检索石墨烯电池，仅搜索我的文献库", "Search graphene batteries, local only"):
            self.assertFalse(discovery_entry(message, project())["steps"][0]["arguments"]["web_search"])
        self.assertEqual("General academic topic", discovery_entry("生成综述", project())["steps"][0]["arguments"]["topic"])

    def test_online_requires_positive_current_turn_request_in_every_execution_mode(self):
        for message in ("请检索石墨烯电池", "Please write a review on graphene batteries"):
            self.assertFalse(discovery_entry(message, project())["steps"][0]["arguments"]["web_search"])
            for automatic in (True, False):
                with self.assertRaises(WorkflowValidationError):
                    authorize_action(message, message, "discovery.search", {"web_search": True}, automatic=automatic)
        for message in ("请联网搜索石墨烯电池", "搜索石墨烯电池，启用联网", "Search online for graphene batteries", "Search the web for graphene batteries"):
            request = discovery_entry(message, project())
            self.assertTrue(request["steps"][0]["arguments"]["web_search"])
            for automatic in (True, False):
                authorize_action(message, message, "discovery.search", {"web_search": True}, automatic=automatic)
        for message in ("搜索石墨烯电池，不要联网，只搜索本地", "检索石墨烯电池，不需要联网", "Search graphene batteries, don't search online"):
            self.assertFalse(discovery_entry(message, project())["steps"][0]["arguments"]["web_search"])

    def test_discussion_existing_artifacts_and_attachments_do_not_trigger_new_search(self):
        for message in ("生成综述会不会很慢？", "请写一篇综述，先不要执行", "搜索按钮怎么用", "搜索正文中的同义词", "请写一个摘要", "请写综述，不要检索", "论文里说搜索论文", "topic: graphene batteries"):
            self.assertIsNone(discovery_entry(message, project()), message)
        for arguments in ({"object_id": "current"}, {"attachment_ids": ["file"]}):
            self.assertIsNone(discovery_entry("生成综述", project(), **arguments))
        self.assertIsNone(discovery_entry("生成综述", project(artifacts=[{"id": "selected"}])))
        self.assertIsNone(discovery_entry("生成综述", project(jobs=[{"job_type": "discovery.search"}])))
        self.assertIsNotNone(discovery_entry("重新检索新的论文主题", project(artifacts=[{"id": "existing"}])))


class ConversationEntryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.DiscoveryV1Tests("runTest")
        self.calls = []
        self.model_error = False
        self.incomplete_reply = False
        self.semantic = None
        self.prompts = []
        def model(prompt, index):
            self.calls.append(index)
            self.prompts.append(json.loads(prompt))
            if self.semantic and index == 0:
                return {"reply": "", "tools": [{"name": "execute_intent", "arguments": self.semantic}]}
            if self.model_error:
                raise GatewayRequestError("unsafe provider payload", status_code=422, code="MODEL_REQUEST_FAILED",
                    details={"details": {"category": "outcome_unknown", "code": "MODEL_RESULT_UNKNOWN"}})
            if self.incomplete_reply:
                return '{"reply":"","tools":[{"name":"project_state","arguments":{}}'
            return {"reply": "已有范围或需要讨论，未重新检索。", "tools": []}
        original_create_app = fixtures.create_app
        def create_app(*args, **kwargs):
            kwargs["native_workflow_overrides"] = {**kwargs.get("native_workflow_overrides", {}), "conversation.model": model}
            return original_create_app(*args, **kwargs)
        with patch.object(fixtures, "create_app", side_effect=create_app):
            self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.case.job_poll_timeout_seconds = 20
        self.service = self.case.app.state.conversation_service

    def send(self, client, message, key="turn", **settings):
        response = client.post(f"/api/v1/projects/{self.case.project_id}/conversation/messages", json={"message": message, **settings}, headers=self.case.headers(key))
        self.assertEqual(202, response.status_code, response.text)
        return self.case.wait_job(client, response.json()["id"])

    def test_network_switch_reaches_original_discovery_worker_and_is_strict_boolean(self):
        with TestClient(self.case.app) as client:
            bad = client.post(f"/api/v1/projects/{self.case.project_id}/conversation/messages",
                json={"message": "请检索石墨烯电池", "web_search": "true"}, headers=self.case.headers("invalid-switch"))
            self.assertEqual(422, bad.status_code)
            for key, message, switch in (("on", "请检索石墨烯电池", True), ("off", "请重新联网检索石墨烯电池", False)):
                turn = self.send(client, message, key, web_search=switch)
                self.assertEqual("succeeded", turn["status"], turn)
                plan = self.case.wait_job(client, turn["result"]["executions"][0]["id"])
                self.assertEqual("succeeded", plan["status"], plan)
                self.assertEqual(switch, self.case.discovery_payloads[-1]["web_search"])
            self.assertEqual([], self.calls)

    def test_fresh_review_reuses_discovery_without_a_chat_model_round_or_confirmation(self):
        message = 'Please write a review on the topic "general materials science", categorized by methods.'
        with TestClient(self.case.app) as client:
            turn = self.send(client, message)
            self.assertEqual("succeeded", turn["status"], turn)
            self.assertEqual([], self.calls)
            plan = self.case.wait_job(client, turn["result"]["executions"][0]["id"])
            self.assertEqual("succeeded", plan["status"], plan)
            self.assertEqual(["discovery.search"], plan["result"]["scope"]["actions"])
            search_id = plan["result"]["completed"][0]["business_job_ids"][0]
            search = client.get("/api/v1/jobs/" + search_id).json()
            self.assertEqual("discovery.search", search["job_type"])
            self.assertEqual(message, self.case.discovery_payloads[0]["topic"])
            self.assertFalse(self.case.discovery_payloads[0]["web_search"])
            self.assertEqual(search["result"]["artifact_id"], plan["result"]["completed"][0]["exported"]["artifact_id"])
            self.assertEqual([], self.case.app.state.discovery_service.get(self.case.first, self.case.project_id)["selected_paper_ids"])
            self.assertIsNone(self.service.repository.get_current_artifact(self.case.first.user_id, self.case.project_id, MATRIX))
            self.assertEqual(turn["id"], self.send(client, message)["id"])
            self.assertEqual(1, len(self.case.discovery_payloads))
            self.assertNotIn("entry_intent", turn["result"])

    def test_existing_selection_and_questions_stay_in_dialogue_without_retrieval(self):
        patch_selection(self.case.app.state.discovery_service, self.case.first, self.case.project_id, revision=0, add_ids=["P001"])
        with TestClient(self.case.app) as client:
            turn = self.send(client, "生成综述")
            self.assertEqual("succeeded", turn["status"])
            question = self.send(client, "生成综述会不会很慢？", "question")
            self.assertEqual("succeeded", question["status"])
        self.assertEqual([0, 0], self.calls)
        self.assertEqual([], self.case.discovery_payloads)
        self.assertEqual(["P001"], self.case.app.state.discovery_service.get(self.case.first, self.case.project_id)["selected_paper_ids"])

    def test_include_all_recommendations_saves_then_confirms_and_submits_original_analysis(self):
        planning = self.case.app.state.planning_service
        def cached_facts(principal, project_id, **kwargs):
            _, artifact = planning._matrix(principal, project_id)
            return {"source_matrix_artifact_id": artifact.id, "pending_paper_count": 0}
        with patch.object(planning, "matrix_enrichment_payload", side_effect=cached_facts), patch.object(planning, "validate_matrix_enrichment_inputs"), TestClient(self.case.app) as client:
            self.case.discover(client)
            discovery = self.case.app.state.discovery_service
            review = discovery.get(self.case.first, self.case.project_id)
            review["results"][0]["local_results"][0]["recommendation_status"] = "recommended"
            discovery._publish_review(self.case.first, self.case.project_id, review, expected_revision=review["revision"])
            turn = self.send(client, "全部纳入推荐结果", "include-recommended")
            self.assertEqual("succeeded", turn["status"], turn)
            plan = self.case.wait_job(client, turn["result"]["executions"][0]["id"])
            self.assertEqual("succeeded", plan["status"], plan)
            self.assertEqual(["discovery.selection", "discovery.confirm"], [step["action"] for step in plan["result"]["completed"]])
            choices = client.get(f"/api/v1/projects/{self.case.project_id}/discovery/papers?query=no-match&offset=100").json()
            self.assertEqual([], choices["items"])
            self.assertEqual(["P001"], choices["selected_paper_ids"])
            self.assertEqual(["P001"], choices["confirmed_paper_ids"])
            self.assertEqual("P001", choices["selected_items"][0]["paper_id"])
            analysis = plan["result"]["completed"][1]["business_job_ids"]
            self.assertEqual("matrix.enrich", client.get("/api/v1/jobs/" + analysis[0]).json()["job_type"])
        self.assertEqual([], self.calls)

    def test_semantic_selection_uses_context_without_literal_include_keywords(self):
        message = "就用你刚列出的这些，先存下来"
        self.semantic = {"operation": "select_recommended", "goal": "discovery", "request_quote": message, "auto_adopt": False}
        with TestClient(self.case.app) as client:
            self.case.discover(client)
            discovery = self.case.app.state.discovery_service
            review = discovery.get(self.case.first, self.case.project_id)
            review["results"][0]["local_results"][0]["recommendation_status"] = "recommended"
            discovery._publish_review(self.case.first, self.case.project_id, review, expected_revision=review["revision"])
            turn = self.send(client, message, "semantic-selection")
            self.assertEqual("succeeded", turn["status"], turn)
            plan = self.case.wait_job(client, turn["result"]["executions"][0]["id"])
            self.assertEqual("succeeded", plan["status"], plan)
            self.assertEqual(["discovery.selection"], [row["action"] for row in plan["result"]["completed"]])
            self.assertEqual(["P001"], discovery.get(self.case.first, self.case.project_id)["selected_paper_ids"])
            self.assertIsNone(self.case.app.state.workflow_repository.get_current_artifact(self.case.first.user_id, self.case.project_id, MATRIX))
            self.assertEqual([0], self.calls)
            context = self.prompts[0]["context"]["intent_context"]["recommendations"]
            self.assertEqual("current_saved_local_recommendations", context["scope"])
            self.assertEqual("P001", context["items"][0]["paper_id"])

    def test_semantic_intent_cannot_drop_negative_clause(self):
        message = "这些都用上，但是不要执行"
        self.semantic = {"operation": "select_recommended", "goal": "final", "request_quote": "这些都用上", "auto_adopt": True}
        with TestClient(self.case.app) as client:
            turn = self.send(client, message, "semantic-negative")
            self.assertEqual([], turn["result"].get("executions", []))
            self.assertEqual("failed", turn["result"]["tool_events"][0]["status"])
            self.assertEqual([], self.case.app.state.workflow_repository.list_project_jobs(self.case.first.user_id, self.case.project_id, job_type="conversation.plan"))

    def test_gateway_unknown_result_is_readable_not_generic_and_never_replayed(self):
        self.model_error = True
        with TestClient(self.case.app) as client:
            turn = self.send(client, "看看当前状态")
            self.assertEqual("failed", turn["status"])
            self.assertEqual("CONVERSATION_MODEL_FAILED", turn["error_code"])
            self.assertIn("总等待时限", turn["error_message"])
            self.assertIn("未自动重发", turn["error_message"])
            self.assertNotIn("unsafe", turn["error_message"])
            self.assertEqual("model_failed", turn["result"]["activity"]["phase"])
            history = client.get(f"/api/v1/projects/{self.case.project_id}/conversation/messages").json()
            self.assertEqual(turn["error_message"], history["turns"][0]["error_message"])
        self.assertEqual([0], self.calls)
        self.assertEqual([], self.case.discovery_payloads)

    def test_incomplete_model_json_does_not_execute_a_nested_tool_fragment(self):
        self.incomplete_reply = True
        with TestClient(self.case.app) as client:
            turn = self.send(client, "查看当前项目")
            self.assertEqual("failed", turn["status"])
            self.assertEqual("CONVERSATION_MODEL_FAILED", turn["error_code"])
            self.assertIn("格式不完整", turn["error_message"])
            self.assertEqual([], turn["result"]["tool_events"])
        self.assertEqual([0], self.calls)

    def test_legacy_failure_uses_owned_gateway_record_without_rewriting_history(self):
        self.model_error = True
        with TestClient(self.case.app) as client:
            turn = self.send(client, "查看状态")
            with database_session(self.case.sessions) as session:
                session.execute(update(WorkflowJob).where(WorkflowJob.id == uuid.UUID(turn["id"])).values(error_code="JOB_EXECUTION_FAILED", error_message="Job execution failed."))
                session.add(AIModelRequest(user_id=uuid.UUID(self.case.first.user_id), project_id=uuid.UUID(self.case.project_id), job_id=uuid.UUID(turn["id"]),
                    request_key="legacy", stage="project-conversation-0", model_tier="test", model_name="offline", request_sha256="0"*64,
                    status="failed", error_message=json.dumps({"code": "MODEL_RESULT_UNKNOWN", "details": {"category": "outcome_unknown"}}),
                    input_price_usd_per_million=Decimal(0), cached_input_price_usd_per_million=Decimal(0), output_price_usd_per_million=Decimal(0)))
            history = client.get(f"/api/v1/projects/{self.case.project_id}/conversation/messages").json()
            self.assertIn("总等待时限", history["turns"][0]["error_message"])
            self.assertEqual("JOB_EXECUTION_FAILED", self.service.repository.get_job(self.case.first.user_id, turn["id"]).error_code)
            self.case.current = self.case.second
            self.assertEqual(404, client.get(f"/api/v1/projects/{self.case.project_id}/conversation/messages").status_code)
