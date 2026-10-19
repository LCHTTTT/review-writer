"""Isolated offline contracts; no provider calls or user data."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from review_writer_api.tests.figure_test_support import NativeFigureApiTestCase
from review_writer_api.errors import WorkflowValidationError


class ConversationTests(NativeFigureApiTestCase):
    offline_text_provider_key = "offline-fixture-not-a-secret"

    def extra_native_workflow_overrides(self):
        self.calls = []
        self.illegal = False
        self.intent_reply = ""
        self.read_stage = None
        self.fail_round = False
        self.model_rounds = []
        def model(prompt, index):
            self.calls.append(prompt)
            self.model_rounds.append(index)
            if index == 0:
                if self.read_stage:
                    return {"tools": [{"name": "stage_details", "arguments": {"stage": self.read_stage}}]}
                return {"reply": self.intent_reply, "tools": [{"name": "delete_project" if self.illegal else "project_state", "arguments": {}}]}
            if self.fail_round:
                from review_writer_core.model_gateway_client import GatewayRequestError
                raise GatewayRequestError("empty", status_code=502, code="PROVIDER_EMPTY_RESPONSE",
                                          details={"category": "empty_response"})
            return {"reply": "已读取当前状态；这是讨论，没有修改项目。", "tools": []}
        return {"conversation.model": model}

    def test_unstarted_stage_is_normal_and_real_file_failures_remain_errors(self):
        from review_writer_api.errors import WorkflowStageNotReady, ArtifactFileMissing, WorkflowNotFound
        service = self.app.state.conversation_service
        reader = service.actions.services.sections_service
        with patch.object(reader, "get", return_value={"sections": [{"section_id": "S01", "heading": "Existing", "text": "Saved prose"}]}):
            existing = service.execute_read(self.first, self.project_id, "stage_details", {"stage": "sections"})
        self.assertEqual("available", existing["availability"])
        self.assertEqual("S01", existing["details"]["sections"][0]["section_id"])
        with patch.object(reader, "get", side_effect=WorkflowStageNotReady()):
            result = service.execute_read(self.first, self.project_id, "stage_details", {"stage": "sections"})
        self.assertEqual("not_generated", result["availability"])
        self.assertEqual([], result["artifacts"])
        for error in (ArtifactFileMissing(), WorkflowNotFound()):
            with patch.object(reader, "get", side_effect=error), self.assertRaises(type(error)):
                service.execute_read(self.first, self.project_id, "stage_details", {"stage": "sections"})
        with self.assertRaises(WorkflowNotFound):
            service.execute_read(self.second, self.project_id, "stage_details", {"stage": "sections"})

    def test_empty_reply_explicit_retry_resumes_only_unfinished_round(self):
        import json
        from review_writer_api.errors import WorkflowStageNotReady
        self.read_stage, self.fail_round = "sections", True
        service = self.app.state.conversation_service
        with TestClient(self.app) as client, patch.object(service.actions.services.sections_service, "get", side_effect=WorkflowStageNotReady()) as read:
            sent = client.post(self.path("/messages"), json={"message": "看看当前章节生成状态"}, headers=self.headers("empty-round"))
            failed = self.wait_job(client, sent.json()["id"])
            self.assertEqual("failed", failed["status"], failed)
            self.assertEqual("CONVERSATION_MODEL_FAILED", failed["error_code"])
            self.assertIn("未返回有效回复", failed["error_message"])
            self.assertEqual("succeeded", failed["result"]["tool_events"][0]["status"])
            self.assertEqual([0, 1], self.model_rounds)  # no automatic paid replay
            self.fail_round = False
            retry = client.post(f"/api/v1/jobs/{failed['id']}/retry")
            self.assertEqual(202, retry.status_code, retry.text)
            resumed = self.wait_job(client, retry.json()["id"])
            self.assertEqual("succeeded", resumed["status"], resumed)
            self.assertEqual([0, 1, 1], self.model_rounds)
            self.assertEqual(1, read.call_count)
            self.assertEqual("not_generated", json.loads(self.calls[-1])["tool_results"][0][0]["value"]["availability"])
            self.assertEqual([], resumed["result"]["executions"] if "executions" in resumed["result"] else [])

    def test_completion_missing_prerequisites_is_actionable_without_model_calls(self):
        with TestClient(self.app) as client:
            created = client.post("/api/v1/projects", json={"slug": "empty-conversation", "topic": "Offline empty scope"}, headers=self.headers("empty-project"))
            self.assertEqual(201, created.status_code, created.text)
            self.project_id = created.json()["project_id"]
            sent = client.post(self.path("/messages"), json={"message": "按当前流程继续，一直到完成综述"}, headers=self.headers("completion-empty"))
            result = self.wait_job(client, sent.json()["id"])
            self.assertEqual("succeeded", result["status"], result)
            self.assertIn("确认要使用的文献", result["result"]["reply"])
            self.assertNotIn("executions", result["result"])
            self.assertEqual([], self.calls)

    def test_completion_existing_draft_submits_once_without_chat_model(self):
        from review_writer_core.workflow.artifacts import DRAFT_MANUSCRIPT
        from review_writer_api.domain_services.conversation_execution import ExecutionRequest
        service = self.app.state.conversation_service
        with TestClient(self.app) as client:
            state = service.overview(self.first, self.project_id)
            state["artifacts"] = [{"logical_name": DRAFT_MANUSCRIPT, "id": "existing-draft"}]
            state["jobs"] = []
            with patch.object(service, "overview", return_value=state), patch.object(service.actions.services.drafts_service, "get", return_value={
                "freshness": {"upstream_stale": False}, "draft_approval_current": False
            }), patch.object(service.execution, "submit", return_value={"id": "offline-submission"}) as submit:
                payload = {"message": "按当前流程继续，一直到完成综述"}
                sent = client.post(self.path("/messages"), json=payload, headers=self.headers("completion-draft"))
                result = self.wait_job(client, sent.json()["id"])
                self.assertEqual("succeeded", result["status"], result)
                request = ExecutionRequest.model_validate(submit.call_args.args[2])
                self.assertEqual(["draft.approve", "final.build"], [s.action for s in request.steps])
                self.assertTrue(request.auto_adopt)
                self.assertEqual([], self.calls)
                again = client.post(self.path("/messages"), json=payload, headers=self.headers("completion-draft"))
                self.assertEqual(sent.json()["id"], again.json()["id"])
                submit.assert_called_once()

    def path(self, suffix=""):
        return f"/api/v1/projects/{self.project_id}/conversation{suffix}"

    def test_readable_json_view_keeps_raw_contract_and_paginates_display_text(self):
        import json
        service = self.app.state.conversation_service
        raw = json.dumps({"schema_version": 1, "project_id": "private", "outline_md": "# Readable outline\n\n## Methods"})
        metadata = {"artifact_id": "saved", "logical_name": "planning/selected_outline.json", "current": False}
        with patch.object(service, "_artifact_text", return_value=(raw, metadata)):
            original = service.read_artifact(self.first, self.project_id, "saved")
            shown = service.read_artifact(self.first, self.project_id, "saved", view="readable", limit=10)
            rest = service.read_artifact(self.first, self.project_id, "saved", view="readable", offset=shown["next_offset"], limit=20000)
        self.assertEqual(raw, original["text"])
        self.assertEqual("# Readable outline\n\n## Methods\n", shown["text"] + rest["text"])
        self.assertEqual("markdown", shown["presentation"])
        self.assertFalse(shown["current"])
        self.assertNotIn("schema_version", shown["text"] + rest["text"])

    def test_read_only_overview_and_history_do_not_change_stage_state(self):
        repository = self.app.state.workflow_repository
        before = repository.get_stage_state(self.first.user_id, self.project_id, "sections")
        with TestClient(self.app) as client:
            overview = client.get(self.path("/overview"))
            self.assertEqual(200, overview.status_code, overview.text)
            self.assertEqual(7, len(overview.json()["stages"]))
            self.assertTrue(overview.json()["capabilities"]["natural_language_writes"])
            self.assertEqual([], client.get(self.path("/messages")).json()["turns"])
        self.assertEqual(before, repository.get_stage_state(self.first.user_id, self.project_id, "sections"))
        self.assertEqual([], self.calls)

    def test_model_tools_history_and_idempotency_are_real_and_bounded(self):
        with TestClient(self.app) as client:
            payload = {"message": "现在还差什么？", "object_id": ""}
            first = client.post(self.path("/messages"), json=payload, headers=self.headers("turn-1"))
            self.assertEqual(202, first.status_code, first.text)
            job = self.wait_job(client, first.json()["id"])
            self.assertEqual("succeeded", job["status"], job)
            again = client.post(self.path("/messages"), json=payload, headers=self.headers("turn-1"))
            self.assertEqual(first.json()["id"], again.json()["id"])
            self.assertEqual(2, len(self.calls))
            self.assertNotIn("base", job["result"])
            self.assertNotIn("steps", job["result"])
            turns = client.get(self.path("/messages")).json()["turns"]
            self.assertEqual(1, len(turns))
            self.assertEqual(payload["message"], turns[0]["message"])
            self.assertEqual("succeeded", turns[0]["result"]["tool_events"][0]["status"])
            changed = client.post(self.path("/messages"), json={"message": "another"}, headers=self.headers("turn-1"))
            self.assertEqual(409, changed.status_code)

    def test_unregistered_write_tool_is_not_executed(self):
        self.illegal = True
        with TestClient(self.app) as client:
            sent = client.post(self.path("/messages"), json={"message": "删掉项目"}, headers=self.headers("turn-write"))
            self.assertEqual(202, sent.status_code, sent.text)
            job = self.wait_job(client, sent.json()["id"])
            self.assertEqual("succeeded", job["status"], job)
            self.assertEqual("failed", job["result"]["tool_events"][0]["status"])
            import json
            self.assertTrue(json.loads(self.calls[-1])["tool_results"][0][0]["error_message"])
            self.assertEqual(200, client.get(self.path("/overview")).status_code)

    def test_mixed_reply_and_read_tools_uses_actual_results_before_final_answer(self):
        self.intent_reply = "我先查看当前项目。"
        with TestClient(self.app) as client:
            sent = client.post(self.path("/messages"), json={"message": "现在还差什么？"}, headers=self.headers("mixed-read"))
            job = self.wait_job(client, sent.json()["id"])
            self.assertEqual("succeeded", job["status"], job)
            self.assertEqual(2, len(self.calls))
            self.assertEqual("succeeded", job["result"]["tool_events"][0]["status"])
            self.assertNotEqual(self.intent_reply, job["result"]["reply"])
            self.assertEqual([], job["result"]["actions"])

    def test_mixed_reply_does_not_authorize_unregistered_tools(self):
        self.intent_reply, self.illegal = "项目已经删除。", True
        with TestClient(self.app) as client:
            sent = client.post(self.path("/messages"), json={"message": "看看项目"}, headers=self.headers("mixed-illegal"))
            job = self.wait_job(client, sent.json()["id"])
            self.assertEqual("succeeded", job["status"], job)
            self.assertEqual("failed", job["result"]["tool_events"][0]["status"])
            self.assertNotEqual(self.intent_reply, job["result"]["reply"])
            self.assertEqual(200, client.get(self.path("/overview")).status_code)

    def test_project_ownership_and_disabled_feature_preserve_original_routes(self):
        with TestClient(self.app) as client:
            self.current = self.second
            self.assertEqual(404, client.get(self.path("/overview")).status_code)
            self.assertEqual(404, client.get(self.path("/messages")).status_code)
            self.current = self.first
            stage_before = client.get(f"/api/v1/projects/{self.project_id}/sections")
            with patch.dict("os.environ", {"REVIEW_WRITER_CONVERSATION_ENABLED": "false"}):
                self.assertFalse(client.get("/api/v1/conversation/config").json()["enabled"])
                self.assertEqual(503, client.get(self.path("/overview")).status_code)
                stage_after = client.get(f"/api/v1/projects/{self.project_id}/sections")
                self.assertEqual(stage_before.status_code, stage_after.status_code)
                self.assertEqual(stage_before.json(), stage_after.json())
                self.assertEqual(200, client.get("/api/v1/health").status_code)

    def test_artifact_preview_is_bounded_and_does_not_change_current_pointer(self):
        service = self.app.state.conversation_service
        card = service.overview(self.first, self.project_id)["artifacts"][0]
        before = service.repository.get_current_artifact(self.first.user_id, self.project_id, card["logical_name"])
        with TestClient(self.app) as client:
            read = client.get(self.path(f"/artifacts/{card['id']}?limit=10"))
            self.assertEqual(200, read.status_code, read.text)
            self.assertEqual(10, len(read.json()["text"]))
            self.assertFalse(read.json()["complete"])
            versions = client.get(self.path("/versions/" + card["logical_name"]))
            self.assertEqual(200, versions.status_code, versions.text)
            self.assertEqual(card["id"], versions.json()["items"][0]["id"])
            self.current = self.second
            self.assertEqual(404, client.get(self.path(f"/artifacts/{card['id']}" )).status_code)
        self.assertEqual(before.id, service.repository.get_current_artifact(self.first.user_id, self.project_id, card["logical_name"]).id)
        with self.assertRaises(WorkflowValidationError):
            service.execute_read(self.first, self.project_id, "project_state", {"user_id": self.second.user_id})
