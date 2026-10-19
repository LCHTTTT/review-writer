"""Isolated preparation scope: use the real queue, ownership and project service."""
import json
import uuid
from fastapi.testclient import TestClient
from review_writer_api.tests.figure_test_support import NativeFigureApiTestCase


class PreparationTests(NativeFigureApiTestCase):
    offline_text_provider_key = "offline-fixture-not-a-secret"
    job_poll_timeout_seconds = 20

    def extra_native_workflow_overrides(self):
        self.contexts = []
        self.tool = {"name": "prepare_project", "arguments": {"slug": "prepared-review", "topic": "Copper comparisons"}}
        def model(prompt, index):
            context = json.loads(prompt)["context"]
            self.contexts.append(context)
            return {"reply": "", "tools": [self.tool]} if index == 0 else {"reply": "已整理项目建议，尚未创建。", "tools": []}
        return {"conversation.model": model}

    def prepare(self, client, key="prep"):
        response = client.post("/api/v1/conversation/preparation/messages", json={"message": "讨论铜催化综述范围"}, headers=self.headers(key))
        self.assertEqual(202, response.status_code, response.text)
        return self.wait_job(client, response.json()["id"])

    def test_real_account_scope_no_placeholder_and_owned_binding_preserves_messages(self):
        with TestClient(self.app) as client:
            before = client.get("/api/v1/projects").json()["count"]
            turn = self.prepare(client)
            self.assertEqual("succeeded", turn["status"], turn)
            self.assertNotIn("base", turn["result"])
            self.assertEqual("prepared-review", turn["result"]["project_proposal"]["slug"])
            self.assertEqual(before, client.get("/api/v1/projects").json()["count"])
            self.assertIsNone(self.contexts[0]["project"]["project_id"])
            job = self.app.state.workflow_repository.get_job(self.first.user_id, turn["id"])
            self.assertEqual("library", job.scope)
            self.assertIsNone(job.project_id)
            body = {"project_id": self.project_id, "turn_ids": [turn["id"]]}
            for _ in range(2):
                result = client.post("/api/v1/conversation/preparation/bind", json=body)
                self.assertEqual(200, result.status_code, result.text)
            self.assertEqual([], client.get("/api/v1/conversation/preparation/messages").json()["turns"])
            history = client.get(f"/api/v1/projects/{self.project_id}/conversation/messages").json()
            self.assertEqual(turn["id"], history["turns"][0]["id"])
            repeat = client.post("/api/v1/conversation/preparation/messages", json={"message": "讨论铜催化综述范围"}, headers=self.headers("prep"))
            self.assertEqual(turn["id"], repeat.json()["id"])

    def test_preparation_cannot_execute_stages_or_bind_foreign_or_running_turns(self):
        self.tool = {"name": "execute_action", "arguments": {"action": "sections.generate", "arguments": {}, "request_quote": "生成正文"}}
        with TestClient(self.app) as client:
            turn = self.prepare(client)
            self.assertEqual("failed", turn["result"]["tool_events"][0]["status"])
            self.assertEqual([], self.app.state.workflow_repository.list_project_jobs(self.first.user_id, self.project_id, job_type="conversation.plan"))
            result = client.post("/api/v1/conversation/preparation/bind", json={"project_id": self.project_id, "turn_ids": [str(uuid.uuid4())]})
            self.assertEqual(404, result.status_code)
            self.current = self.second
            self.assertEqual([], client.get("/api/v1/conversation/preparation/messages").json()["turns"])
            result = client.post("/api/v1/conversation/preparation/bind", json={"project_id": self.project_id, "turn_ids": [turn["id"]]})
            self.assertEqual(404, result.status_code)
