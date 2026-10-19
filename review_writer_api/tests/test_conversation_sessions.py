"""Real durable discussions, isolated providers and accounts; no remote calls."""
import json
from fastapi.testclient import TestClient
from review_writer_api.tests.figure_test_support import NativeFigureApiTestCase

class SessionTests(NativeFigureApiTestCase):
    offline_text_provider_key = "offline-fixture-not-a-secret"
    job_poll_timeout_seconds = 20

    def extra_native_workflow_overrides(self):
        self.contexts = []
        def model(prompt, index):
            self.contexts.append(json.loads(prompt))
            return {"reply": "只讨论，不修改。", "tools": []}
        return {"conversation.model": model}

    def create(self, client, endpoint, title="讨论一", key="session"):
        response = client.post(endpoint + "/sessions", json={"title": title}, headers=self.headers(key))
        self.assertEqual(202, response.status_code, response.text)
        return self.wait_job(client, response.json()["id"])["id"]

    def message(self, client, endpoint, session_id, message, key):
        response = client.post(endpoint + "/messages", json={"message": message, "session_id": session_id}, headers=self.headers(key))
        self.assertEqual(202, response.status_code, response.text)
        job = self.wait_job(client, response.json()["id"])
        self.assertEqual("succeeded", job["status"], job)
        return job

    def test_owned_isolated_history_idempotency_and_readable_archive(self):
        endpoint = f"/api/v1/projects/{self.project_id}/conversation"
        with TestClient(self.app) as client:
            first = self.create(client, endpoint)
            self.assertEqual(first, self.create(client, endpoint))
            second = self.create(client, endpoint, "讨论二", "session-two")
            one = self.message(client, endpoint, first, "只讨论第一个问题", "one")
            two = self.message(client, endpoint, second, "只讨论另一个问题", "two")
            history = client.get(endpoint + "/messages", params={"session_id": first}).json()
            self.assertEqual([one["id"]], [turn["id"] for turn in history["turns"]])
            self.assertEqual([], client.get(endpoint + "/messages").json()["turns"])
            self.assertNotIn("只讨论第一个问题", json.dumps(self.contexts[-1], ensure_ascii=False))
            self.assertEqual(404, client.get(endpoint + "/messages", params={"session_id": first, "before": two["id"]}).status_code)
            self.assertEqual(200, client.patch(endpoint + "/sessions/" + first, json={"title": "改名", "archived": True}).status_code)
            self.assertEqual(200, client.get(endpoint + "/messages", params={"session_id": first}).status_code)
            self.assertEqual(409, client.post(endpoint + "/messages", json={"message": "只讨论", "session_id": first}).status_code)
            self.assertEqual(200, client.patch(endpoint + "/sessions/" + first, json={"archived": False}).status_code)
            self.assertEqual("改名", client.get(endpoint + "/sessions").json()["items"][1]["title"])
            self.current = self.second
            self.assertEqual(404, client.get(endpoint + "/messages", params={"session_id": first}).status_code)

    def test_preparation_binding_moves_whole_named_history_and_retry_is_safe(self):
        endpoint = "/api/v1/conversation/preparation"
        target = f"/api/v1/projects/{self.project_id}/conversation"
        with TestClient(self.app) as client:
            discussion = self.create(client, endpoint)
            first = self.message(client, endpoint, discussion, "只讨论范围", "a")
            second = self.message(client, endpoint, discussion, "只讨论结构", "b")
            body = {"project_id": self.project_id, "turn_ids": [second["id"]], "session_id": discussion}
            for _ in range(2):
                response = client.post(endpoint + "/bind", json=body)
                self.assertEqual(200, response.status_code, response.text)
            history = client.get(target + "/messages", params={"session_id": discussion}).json()
            self.assertEqual([first["id"], second["id"]], [row["id"] for row in history["turns"]])
            self.assertEqual([], client.get(endpoint + "/sessions").json()["items"])
            self.assertEqual(404, client.get(endpoint + "/messages", params={"session_id": discussion}).status_code)
            self.message(client, target, discussion, "只讨论已绑定的项目", "c")

    def test_empty_preparation_discussion_binding_and_foreign_session(self):
        endpoint = "/api/v1/conversation/preparation"
        with TestClient(self.app) as client:
            identifier = self.create(client, endpoint)
            self.current = self.second
            self.assertEqual(404, client.patch(endpoint + "/sessions/" + identifier, json={"archived": True}).status_code)
            self.assertEqual(404, client.post(endpoint + "/messages", json={"message": "只讨论", "session_id": identifier}).status_code)
            self.current = self.first
            result = client.post(endpoint + "/bind", json={"project_id": self.project_id, "session_id": identifier})
            self.assertEqual(200, result.status_code, result.text)
