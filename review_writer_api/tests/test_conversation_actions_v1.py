"""Offline action-card contract tests; never call external models/providers."""
from unittest import mock

from fastapi.testclient import TestClient

from review_writer_api.tests.figure_test_support import NativeFigureApiTestCase
from review_writer_core.workflow.artifacts import FINAL_VALIDATION, FIGURE_MANIFEST


class ConversationActionTests(NativeFigureApiTestCase):
    offline_text_provider_key = "offline-fixture-not-a-secret"
    def extra_native_workflow_overrides(self):
        self.action = {"action": "images.preserve_sources", "arguments": {}}
        self.fail_reply_once = False
        self.calls = []
        def model(prompt, index):
            self.calls.append(index)
            if index == 0:
                return {"reply": "", "tools": [{"name": "request_action", "arguments": self.action}]}
            if self.fail_reply_once:
                self.fail_reply_once = False
                raise RuntimeError("offline model failure")
            return {"reply": "操作候选待采用，尚未执行。", "tools": []}
        return {"conversation.model": model}

    def propose(self, client):
        response = client.post(f"/api/v1/projects/{self.project_id}/conversation/messages", json={"message": "请提出操作候选"}, headers=self.headers("proposal"))
        self.assertEqual(202, response.status_code, response.text)
        return self.wait_job(client, response.json()["id"])

    def path(self, turn):
        return f"/api/v1/projects/{self.project_id}/conversation/actions/{turn['id']}/{turn['result']['actions'][0]['id']}"

    def test_proposal_is_readonly_and_adoption_is_idempotent_without_model_calls(self):
        with TestClient(self.app) as client:
            self.confirm_review(client)
            turn = self.propose(client)
            self.assertEqual("succeeded", turn["status"], turn)
            repo = self.app.state.workflow_repository
            self.assertIsNone(repo.get_current_artifact(self.first.user_id, self.project_id, FIGURE_MANIFEST))
            calls = len(self.calls)
            submitted = client.post(self.path(turn))
            self.assertEqual(202, submitted.status_code, submitted.text)
            executed = self.wait_job(client, submitted.json()["id"])
            self.assertEqual("succeeded", executed["status"], executed)
            self.assertTrue(executed["result"]["execution_completed"])
            self.assertIsNotNone(repo.get_current_artifact(self.first.user_id, self.project_id, FIGURE_MANIFEST))
            duplicate = client.post(self.path(turn))
            self.assertEqual(submitted.json()["id"], duplicate.json()["id"])
            self.assertEqual(calls, len(self.calls))
            self.assertEqual(executed["id"], client.get(self.path(turn)).json()["job"]["id"])

    def test_changed_source_rejects_adoption_and_preserves_current_result(self):
        with TestClient(self.app) as client:
            self.confirm_review(client)
            turn = self.propose(client)
            self.add_second_confirmed_figure()
            response = client.post(self.path(turn))
            result = self.wait_job(client, response.json()["id"])
            self.assertEqual("failed", result["status"], result)
            self.assertIn("项目成果已变化", result["error_message"])
            self.assertFalse(result["result"].get("execution_started"))
            self.assertIsNone(self.app.state.workflow_repository.get_current_artifact(self.first.user_id, self.project_id, FIGURE_MANIFEST))

    def test_unrelated_final_result_does_not_block_image_adoption(self):
        with TestClient(self.app) as client:
            self.confirm_review(client)
            turn = self.propose(client)
            repo = self.app.state.workflow_repository
            run = repo.create_stage_run(self.first.user_id, self.project_id, "final", status="succeeded")
            result = self._publish(run.id, FINAL_VALIDATION, "validation.json", b"{}", "json")
            repo.set_current_artifact(self.first.user_id, self.project_id, FINAL_VALIDATION, result.id)
            response = client.post(self.path(turn))
            result = self.wait_job(client, response.json()["id"])
            self.assertEqual("succeeded", result["status"], result)

    def test_other_user_cannot_read_or_execute_proposal(self):
        with TestClient(self.app) as client:
            self.confirm_review(client)
            turn = self.propose(client)
            self.current = self.second
            for method in (client.get, client.post):
                self.assertEqual(404, method(self.path(turn)).status_code)

    def test_unregistered_argument_is_rejected_not_silently_discarded(self):
        self.action = {"action": "images.redraw", "arguments": {"figure_ids": ["P001-F01"], "message": "change labels"}}
        with TestClient(self.app) as client:
            turn = self.propose(client)
            self.assertEqual([], turn["result"]["actions"])
            self.assertEqual("failed", turn["result"]["tool_events"][0]["status"])

    def test_failed_reply_reuses_saved_model_steps_and_proposal_on_retry(self):
        self.fail_reply_once = True
        with TestClient(self.app) as client:
            self.confirm_review(client)
            original = self.propose(client)
            self.assertEqual("failed", original["status"])
            response = client.post(f"/api/v1/jobs/{original['id']}/retry")
            resumed = self.wait_job(client, response.json()["id"])
            self.assertEqual("succeeded", resumed["status"], resumed)
            self.assertEqual([0, 1, 1], self.calls)
            self.assertEqual(original["result"]["actions"], resumed["result"]["actions"])

    def test_uncertain_write_is_not_replayed_by_retry(self):
        with TestClient(self.app) as client:
            self.confirm_review(client)
            turn = self.propose(client)
            executor = self.app.state.conversation_service.actions
            with mock.patch.object(executor, "execute", side_effect=RuntimeError("simulated lost acknowledgement")) as execute:
                response = client.post(self.path(turn))
                interrupted = self.wait_job(client, response.json()["id"])
                self.assertEqual("failed", interrupted["status"])
                self.assertTrue(interrupted["result"]["execution_started"])
                retry = client.post(f"/api/v1/jobs/{interrupted['id']}/retry")
                self.assertEqual(409, retry.status_code, retry.text)
                self.assertEqual(1, execute.call_count)
                self.assertNotIn("retry", interrupted["available_actions"])


class ConversationFaultIsolationTests(NativeFigureApiTestCase):
    def setUp(self):
        with mock.patch("review_writer_api.domain_services.conversation.ConversationService", side_effect=RuntimeError("optional adapter unavailable")):
            super().setUp()

    def test_adapter_initialization_failure_does_not_break_original_workflow(self):
        with TestClient(self.app) as client:
            self.assertEqual(200, client.get("/api/v1/health").status_code)
            self.assertFalse(client.get("/api/v1/conversation/config").json()["enabled"])
            self.assertEqual(503, client.get(f"/api/v1/projects/{self.project_id}/conversation/overview").status_code)
            self.confirm_review(client)
            response = client.post(f"/api/v1/projects/{self.project_id}/figures/preserve-sources", headers=self.headers("without-conversation"))
            self.assertEqual(200, response.status_code, response.text)
