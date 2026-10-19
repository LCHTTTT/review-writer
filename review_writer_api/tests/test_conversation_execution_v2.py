"""Offline execution/continuation contracts, using original services and workers."""
import json
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from review_writer_api.tests.figure_test_support import NativeFigureApiTestCase
from review_writer_api.domain_services.conversation_authorization import authorize_action
from review_writer_api.errors import WorkflowValidationError
from review_writer_core.workflow.artifacts import BLUEPRINT, FIGURE_MANIFEST


class ConversationExecutionTests(NativeFigureApiTestCase):
    offline_text_provider_key = "offline-fixture-not-a-secret"
    job_poll_timeout_seconds = 25

    def extra_native_workflow_overrides(self):
        self.tool = {"name": "execute_action", "arguments": {"action": "images.preserve_sources", "arguments": {}, "request_quote": "保留原图"}}
        self.model_calls = []
        self.model_started, self.model_release = threading.Event(), threading.Event()
        self.block_model = False
        self.intent_reply = ""
        self.repeat_tool = False
        def model(prompt, index):
            self.model_calls.append((json.loads(prompt)["context"]["user"], index))
            if self.block_model and index == 0:
                self.model_started.set()
                self.model_release.wait(5)
            return {"reply": self.intent_reply, "tools": [self.tool]} if index == 0 or self.repeat_tool else {"reply": "执行未获授权或要求不明确，未执行。", "tools": []}
        return {"conversation.model": model}

    def tearDown(self):
        self.model_release.set()
        super().tearDown()

    def send(self, client, message="保留原图", key="direct", **extra):
        response = client.post(f"/api/v1/projects/{self.project_id}/conversation/messages",
                               json={"message": message, **extra}, headers=self.headers(key))
        self.assertEqual(202, response.status_code, response.text)
        return response.json()

    def test_direct_command_runs_without_adoption_click_or_extra_model_round(self):
        with TestClient(self.app) as client:
            self.confirm_review(client)
            turn = self.wait_job(client, self.send(client)["id"])
            self.assertEqual("succeeded", turn["status"], turn)
            execution = self.wait_job(client, turn["result"]["executions"][0]["id"])
            self.assertEqual("succeeded", execution["status"], execution)
            self.assertEqual("completed", execution["result"]["status"])
            self.assertIsNotNone(self.app.state.workflow_repository.get_current_artifact(self.first.user_id, self.project_id, FIGURE_MANIFEST))
            self.assertEqual([("保留原图", 0)], self.model_calls)
            repeated = self.send(client)
            self.assertEqual(turn["id"], repeated["id"])
            self.assertEqual(1, len(self.app.state.workflow_repository.list_project_jobs(self.first.user_id, self.project_id, job_type="conversation.plan")))

    def test_dialogue_continuation_retries_existing_failed_section_job(self):
        from review_writer_api.errors import WorkflowConflict
        from review_writer_api.job_handlers.stage_execution import section_retry_checkpoints
        message = "继续生成章节正文"
        self.tool = {"name": "execute_action", "arguments": {
            "action": "sections.generate", "arguments": {}, "request_quote": message}}
        jobs = self.app.state.job_service
        services = self.app.state.conversation_service.actions.services
        calls = []
        checkpoint = {"entries": {"S01": {"output": {"text": "Already completed"}}}, "authoring_states": {}}
        def section_fixture(context, payload):
            calls.append(context.retry_of_job_id)
            if not context.retry_of_job_id:
                context.report_partial_result({"section_checkpoint": checkpoint})
                raise WorkflowConflict("Offline interrupted review")
            restored, _ = section_retry_checkpoints(context)
            self.assertEqual(checkpoint["entries"], restored["entries"])
            return {"retained": ["S01"]}
        with TestClient(self.app) as client, patch.dict(jobs._handlers, {"sections.generate": section_fixture}):
            payload = {"source_blueprint_artifact_id": "blueprint", "source_matrix_artifact_id": "matrix",
                       "source_outline_artifact_id": "outline", "tasks": [{"section_id": "S01"}], "expected_sections_revision": 0}
            source = jobs.submit(self.first, scope="project", project_id=self.project_id,
                                 job_type="sections.generate", idempotency_key="offline-original", payload=payload)
            self.assertEqual("failed", self.wait_job(client, source.id)["status"])
            with patch.object(services.sections_service, "generation_payload", return_value=payload), patch.object(
                    services.sections_service, "validate_generation_inputs"):
                turn = self.wait_job(client, self.send(client, message, "resume-sections")["id"])
                self.assertEqual("succeeded", turn["status"], turn)
                plan = self.wait_job(client, turn["result"]["executions"][0]["id"])
                self.assertEqual("succeeded", plan["status"], plan)
            records = self.app.state.workflow_repository.list_project_jobs(self.first.user_id, self.project_id, job_type="sections.generate")
            self.assertEqual(2, len(records))
            self.assertEqual(source.id, records[0].retry_of_job_id)
            self.assertEqual({"retained": ["S01"]}, records[0].result)
            self.assertEqual([None, source.id], calls)

    def test_identical_rejected_plans_stop_with_specific_reason(self):
        self.repeat_tool = True
        with TestClient(self.app) as client:
            turn = self.wait_job(client, self.send(client, "保留原图有什么用？", key="repeat-question")["id"])
            self.assertEqual("failed", turn["status"], turn)
            self.assertIn("这是询问", turn["error_message"])
            self.assertIn("停止重复尝试", turn["error_message"])
            self.assertEqual(2, len(self.model_calls))
            self.assertEqual([], self.app.state.workflow_repository.list_project_jobs(self.first.user_id, self.project_id, job_type="conversation.plan"))

    def test_two_steps_yield_and_share_actual_versions(self):
        self.tool = {"name": "execute_plan", "arguments": {"request_quote": "保留原图并确认图像", "steps": [
            {"action": "images.preserve_sources", "arguments": {}}, {"action": "images.confirm", "arguments": {}}]}}
        with TestClient(self.app) as client:
            self.confirm_review(client)
            turn = self.wait_job(client, self.send(client, "保留原图并确认图像")["id"])
            plan = self.wait_job(client, turn["result"]["executions"][0]["id"])
            self.assertEqual("succeeded", plan["status"], plan)
            self.assertEqual(2, len(plan["result"]["completed"]))
            self.assertEqual("approved", self.app.state.workflow_repository.get_stage_state(self.first.user_id, self.project_id, "figures").status)
            self.assertEqual(1, len(self.model_calls))

    def test_mixed_reply_submits_once_and_never_displays_premature_success(self):
        self.intent_reply = "图像已经全部生成并保存。"
        with TestClient(self.app) as client:
            self.confirm_review(client)
            turn = self.wait_job(client, self.send(client, key="mixed-execution")["id"])
            self.assertEqual("succeeded", turn["status"], turn)
            self.assertNotEqual(self.intent_reply, turn["result"]["reply"])
            self.assertEqual("submitted", turn["result"]["activity"]["phase"])
            self.assertEqual(1, len(self.model_calls))
            plan_id = turn["result"]["executions"][0]["id"]
            self.assertEqual("succeeded", self.wait_job(client, plan_id)["status"])
            self.assertEqual(turn["id"], self.send(client, key="mixed-execution")["id"])
            self.assertEqual(1, len(self.app.state.workflow_repository.list_project_jobs(self.first.user_id, self.project_id, job_type="conversation.plan")))

    def test_mixed_reply_cannot_turn_a_question_into_execution_authorization(self):
        self.intent_reply = "好的，已经执行。"
        with TestClient(self.app) as client:
            self.confirm_review(client)
            turn = self.wait_job(client, self.send(client, "保留原图有什么用？", key="mixed-question")["id"])
            self.assertEqual("failed", turn["result"]["tool_events"][0]["status"])
            self.assertNotEqual(self.intent_reply, turn["result"]["reply"])
            self.assertEqual([], self.app.state.workflow_repository.list_project_jobs(self.first.user_id, self.project_id, job_type="conversation.plan"))

    def test_question_or_paper_quote_cannot_authorize_execution(self):
        self.tool["arguments"]["request_quote"] = "保留原图"
        with TestClient(self.app) as client:
            self.confirm_review(client)
            turn = self.wait_job(client, self.send(client, "论文里说保留原图，这合理吗？")["id"])
            self.assertEqual([], self.app.state.workflow_repository.list_project_jobs(self.first.user_id, self.project_id, job_type="conversation.plan"))
            self.assertEqual("failed", turn["result"]["tool_events"][0]["status"])

    def test_reading_history_never_replays_completed_execution(self):
        with TestClient(self.app) as client:
            self.confirm_review(client)
            turn = self.wait_job(client, self.send(client)["id"])
            self.wait_job(client, turn["result"]["executions"][0]["id"])
            before = self.app.state.workflow_repository.get_current_artifact(self.first.user_id, self.project_id, FIGURE_MANIFEST)
            for _ in range(3):
                self.assertEqual(200, client.get(f"/api/v1/projects/{self.project_id}/conversation/messages").status_code)
            self.assertEqual(before, self.app.state.workflow_repository.get_current_artifact(self.first.user_id, self.project_id, FIGURE_MANIFEST))

    def test_followups_are_accepted_but_model_loops_stay_serial(self):
        self.block_model = True
        self.tool = {"name": "project_state", "arguments": {}}
        with TestClient(self.app) as client:
            first = self.send(client, "看看项目状态", "first")
            self.assertTrue(self.model_started.wait(3))
            second = self.send(client, "后面再看第二章", "second")
            time.sleep(.1)
            self.assertEqual("queued", client.get("/api/v1/jobs/" + second["id"]).json()["status"])
            self.assertEqual([("看看项目状态", 0)], self.model_calls)
            self.block_model = False
            self.model_release.set()
            self.wait_job(client, first["id"])
            self.wait_job(client, second["id"])
            self.assertEqual(["看看项目状态", "看看项目状态", "后面再看第二章", "后面再看第二章"], [message for message, _ in self.model_calls])

    def test_steering_stops_unsubmitted_tool_after_inflight_model_returns(self):
        self.block_model = True
        with TestClient(self.app) as client:
            self.confirm_review(client)
            original = self.send(client)
            self.assertTrue(self.model_started.wait(3))
            adjustment = self.send(client, "先别采用，第二章保留失败案例", "steering", delivery="steer")
            self.block_model = False
            self.model_release.set()
            original = self.wait_job(client, original["id"])
            self.assertEqual(adjustment["id"], original["result"]["interrupted_by"])
            self.wait_job(client, adjustment["id"])
            self.assertEqual([], self.app.state.workflow_repository.list_project_jobs(self.first.user_id, self.project_id, job_type="conversation.plan"))

    def test_stop_continuation_does_not_cancel_already_submitted_business_task(self):
        with TestClient(self.app) as client:
            self.confirm_review(client)
            source = self.send(client, "保留原图", "source")
            self.wait_job(client, source["id"])
            response = client.post(f"/api/v1/projects/{self.project_id}/conversation/continuations/stop")
            self.assertEqual(200, response.status_code)
            self.assertFalse(response.json()["business_jobs_cancelled"])
            self.assertEqual(0, self.redraw_calls)

    def test_metadata_read_and_literal_patch_do_not_require_parsed_fulltext(self):
        service = self.app.state.conversation_service
        library = service.actions.services.library_service
        initial = library.update_metadata(self.first, "P001", {"paper_id": "P001", "title": {"value": "Source P001", "human_checked": True},
            "year": {"value": 2012, "human_checked": True}, "journal": {"value": "Old", "human_checked": True}})
        read = service.execute_read(self.first, self.project_id, "paper_metadata", {"paper_id": "P001"})
        self.assertEqual(initial.artifact_ids["metadata"], read["metadata_artifact_id"])
        message = "把P001的期刊改为Organic Letters"
        self.tool = {"name": "execute_action", "arguments": {"action": "library.metadata", "arguments": {
            "paper_id": "P001", "metadata_artifact_id": read["metadata_artifact_id"], "fields": {"journal": "Organic Letters"}}, "request_quote": message}}
        with TestClient(self.app) as client:
            turn = self.wait_job(client, self.send(client, message)["id"])
            plan = self.wait_job(client, turn["result"]["executions"][0]["id"])
            self.assertEqual("succeeded", plan["status"], plan)
            saved = library.get(self.first, "P001")
            self.assertEqual("Organic Letters", saved.metadata["journal"]["value"])
            self.assertTrue(saved.metadata["journal"]["human_checked"])
            self.assertEqual(initial.metadata["year"], saved.metadata["year"])
            self.assertEqual(saved.artifact_ids["metadata"], plan["result"]["completed"][0]["exported"]["metadata_artifact_id"])

    def test_new_checked_metadata_blocks_stale_patch(self):
        service = self.app.state.conversation_service
        library = service.actions.services.library_service
        old = library.update_metadata(self.first, "P001", {"paper_id": "P001", "journal": {"value": "Old", "human_checked": True}})
        library.update_metadata(self.first, "P001", {**old.metadata, "journal": {"value": "New checked journal", "human_checked": True}})
        message = "把P001的期刊改为Organic Letters"
        self.tool = {"name": "execute_action", "arguments": {"action": "library.metadata", "arguments": {
            "paper_id": "P001", "metadata_artifact_id": old.artifact_ids["metadata"], "fields": {"journal": "Organic Letters"}}, "request_quote": message}}
        with TestClient(self.app) as client:
            turn = self.wait_job(client, self.send(client, message)["id"])
            plan = self.wait_job(client, turn["result"]["executions"][0]["id"])
            self.assertEqual("failed", plan["status"])
            self.assertEqual("New checked journal", library.get(self.first, "P001").metadata["journal"]["value"])

    def test_natural_stop_is_durable_and_does_not_require_a_model(self):
        from sqlalchemy import update
        from review_writer_api.database import Project, database_session
        import uuid
        with TestClient(self.app) as client:
            repository = self.app.state.workflow_repository
            with database_session(repository.session_factory) as session:
                session.execute(update(Project).where(Project.id == uuid.UUID(self.project_id)).values(model_tier="missing-test-model"))
            response = self.send(client, "停止接续，不调用模型", "stop-control")
            control = self.wait_job(client, response["id"])
            self.assertEqual("conversation.control", control["job_type"])
            self.assertEqual("succeeded", control["status"], control)
            self.assertFalse(control["result"]["business_jobs_cancelled"])
            self.assertEqual([], self.model_calls)
            history = client.get(f"/api/v1/projects/{self.project_id}/conversation/messages").json()
            self.assertEqual(control["id"], history["turns"][0]["id"])
            self.assertEqual(control["id"], self.send(client, "停止接续，不调用模型", "stop-control")["id"])

    def test_unsupported_hard_limits_are_rejected_before_model_admission(self):
        with TestClient(self.app) as client:
            for index, message in enumerate(("生成所有章节，预算不超过10元", "生成摘要，最多重试一次", "看初稿，不调用模型")):
                response = client.post(f"/api/v1/projects/{self.project_id}/conversation/messages", json={"message": message}, headers=self.headers(f"limit-{index}"))
                self.assertEqual(422, response.status_code, response.text)
            self.assertEqual([], self.model_calls)
            self.assertEqual([], self.app.state.workflow_repository.list_project_jobs(self.first.user_id, self.project_id, job_type="conversation.turn"))

    def test_trusted_model_source_preserves_snapshot_when_project_default_changes(self):
        from sqlalchemy import update
        from review_writer_api.database import database_session
        from review_writer_api.database import Project
        import uuid
        self.tool = {"name": "project_state", "arguments": {}}
        with TestClient(self.app) as client:
            turn = self.wait_job(client, self.send(client, "看看项目状态")["id"])
            repository = self.app.state.workflow_repository
            source = repository.get_job(self.first.user_id, turn["id"])
            # Corrupt ONLY this isolated fixture's default to prove it is not consulted.
            with database_session(repository.session_factory) as session:
                session.execute(update(Project).where(Project.id == uuid.UUID(self.project_id)).values(model_tier="missing-test-model"))
            child = repository.create_or_get_job(self.first.user_id, self.project_id, "project", "draft.optimize", "frozen-child", {}, model_snapshot_source_job_id=source.id)
            from review_writer_api.model_catalog import SNAPSHOT_KEY
            self.assertEqual(source.payload[SNAPSHOT_KEY]["id"], child.payload[SNAPSHOT_KEY]["id"])
            repository.request_job_cancellation(self.first.user_id, child.id)
            with self.assertRaises(WorkflowValidationError):
                repository.create_or_get_job(self.first.user_id, self.project_id, "project", "draft.optimize", "wrong-source", {}, model_snapshot_source_job_id=str(uuid.uuid4()))

    def test_blueprint_proposal_uses_owned_bounded_reader(self):
        repository = self.app.state.workflow_repository
        run = repository.create_stage_run(self.first.user_id, self.project_id, "blueprint", status="succeeded")
        artifact = self._publish(run.id, BLUEPRINT, "blueprint.json", b'{"sections": []}', "json")
        action = self.app.state.conversation_service.actions.proposal(self.first, self.project_id, "blueprint.confirm", {"artifact_id": artifact.id}, "preview")
        self.assertIn("sections", action["preview"]["candidate_text"])
        self.assertFalse(action["preview"]["preview_truncated"])


class ConversationAuthorizationTests(unittest.TestCase):
    def test_stop_classifier_does_not_discard_additional_requests(self):
        from review_writer_api.domain_services.conversation_authorization import is_stop_continuation
        self.assertTrue(is_stop_continuation("请停止接续，不调用模型"))
        self.assertFalse(is_stop_continuation("停止接续，并取消所有生成任务"))

    def test_generation_question_is_not_a_command(self):
        with self.assertRaises(WorkflowValidationError):
            authorize_action("生成所有章节需要多少费用？", "生成所有章节", "sections.generate", {})

    def test_ordinary_continue_does_not_adopt(self):
        with self.assertRaises(WorkflowValidationError):
            authorize_action("继续到初稿", "继续到初稿", "blueprint.confirm", {"artifact_id": "id"})

    def test_automatic_adoption_has_cutoff_and_no_extra_acquisition_or_redraw(self):
        message = "按你的建议自动采用并继续到初稿，保留原图"
        authorize_action(message, message, "blueprint.confirm", {}, automatic=True, stop_at="draft")
        for action, args in (("final.build", {}), ("images.redraw", {"figure_ids": ["f"]})):
            with self.assertRaises(WorkflowValidationError):
                authorize_action(message, message, action, args, automatic=True, stop_at="draft")

    def test_negative_constraints_never_become_positive_authority(self):
        for message, name, args in (("取消生成正文", "sections.generate", {}),
                                    ("不联网，只检索本地论文", "discovery.search", {"web_search": True}),
                                    ("生成章节规划，不采用规划", "blueprint.confirm", {"artifact_id": "id"}),
                                    ("修改正文的比较部分", "sections.generate", {})):
            with self.assertRaises(WorkflowValidationError):
                authorize_action(message, message, name, args)
        authorize_action("不联网，只检索本地论文", "不联网，只检索本地论文", "discovery.search", {"web_search": False})

    def test_save_fields_never_approves_or_changes_unrelated_fields(self):
        for name, args in (("draft.approve", {}), ("blueprint.confirm", {"artifact_id": "id"}),
                           ("draft.manuscript_fields", {"fields": {"keywords": ["Copper"]}})):
            with self.assertRaises(WorkflowValidationError):
                authorize_action("把初稿标题改为Copper", "把初稿标题改为Copper", name, args)
        authorize_action("把初稿标题改为Copper", "把初稿标题改为Copper", "draft.manuscript_fields", {"fields": {"title": "Copper"}})
        with self.assertRaises(WorkflowValidationError):
            authorize_action("更新期刊", "更新期刊", "library.metadata", {"paper_id": "P001", "fields": {"journal": ""}})

    def test_draft_cutoff_never_approves_and_redraw_never_discards_parameters(self):
        with self.assertRaises(WorkflowValidationError):
            authorize_action("自动采用并继续到初稿", "自动采用并继续到初稿", "draft.approve", {}, automatic=True, stop_at="draft")
        with self.assertRaises(WorkflowValidationError):
            authorize_action("重绘图像，配色改为绿色", "重绘图像", "images.redraw", {"figure_ids": ["f"]})

    def test_no_single_chapter_request_is_expanded_to_all_chapters(self):
        with self.assertRaises(WorkflowValidationError):
            authorize_action("修改第二章", "修改第二章", "sections.generate", {})


class ConversationScopedSaveTests(unittest.TestCase):
    def test_discovery_selection_reuses_original_save_without_confirming_or_changing_other_rows(self):
        from review_writer_api.tests.test_discovery_v1 import DiscoveryV1Tests
        from review_writer_api.domain_services.discovery import _candidate_id
        from review_writer_api.errors import WorkflowNotFound
        case = DiscoveryV1Tests("runTest")
        case.setUp()
        try:
            with TestClient(case.app) as client:
                case.discover(client)
                service = case.app.state.discovery_service
                actions = case.app.state.conversation_service.actions
                before = service.get(case.first, case.project_id)
                target = _candidate_id(before["results"][0]["local_results"][0], external=False)
                action = actions.proposal(case.first, case.project_id, "discovery.selection", {"candidate_ids": [target], "selected": True}, "select")
                actions.validate_baseline(case.first, case.project_id, action)
                actions.execute(case.first, case.project_id, action, "select")
                after = service.get(case.first, case.project_id)
                self.assertEqual(before["revision"] + 1, after["revision"])
                for old_group, new_group in zip(before["results"], after["results"]):
                    self.assertEqual(old_group["web_results"], new_group["web_results"])
                    for old, new in zip(old_group["local_results"], new_group["local_results"]):
                        if _candidate_id(old, external=False) == target:
                            self.assertTrue(new["selected_for_matrix"])
                            self.assertEqual(old["title"], new["title"])
                        else:
                            self.assertEqual(old, new)
                broken = actions.proposal(case.first, case.project_id, "discovery.selection", {"candidate_ids": ["foreign-paper"], "selected": False}, "missing")
                with self.assertRaises(WorkflowNotFound):
                    actions.execute(case.first, case.project_id, broken, "missing")
                self.assertEqual(after["revision"], service.get(case.first, case.project_id)["revision"])
                self.assertEqual([], case.app.state.workflow_repository.list_project_jobs(case.first.user_id, case.project_id, job_type="matrix.enrich"))
        finally:
            case.tearDown()

    def test_literal_title_and_publication_edits_preserve_other_fields_and_source_states(self):
        from review_writer_api.tests.test_paragraph_dialogue import ParagraphDialogueTests
        from review_writer_core.workflow.artifacts import DRAFT_MANUSCRIPT, FINAL_FRONT_MATTER
        case = ParagraphDialogueTests("runTest")
        requested = {}
        original = case.extra_native_workflow_overrides
        case.extra_native_workflow_overrides = lambda: {**original(), "conversation.model": lambda prompt, index: {"reply": "", "tools": [{"name": "execute_action", "arguments": requested}]}}
        case.setUp()
        try:
            with TestClient(case.app) as client:
                case.prepare_draft(client)
                repository = case.app.state.workflow_repository
                original_artifact = repository.get_current_artifact(case.first.user_id, case.project_id, DRAFT_MANUSCRIPT)
                before = case.app.state.drafts_service.get(case.first, case.project_id)
                message = "把初稿标题改为Scoped title"
                requested.update(action="draft.manuscript_fields", arguments={"fields": {"title": "Scoped title"}}, request_quote=message)
                response = client.post(f"/api/v1/projects/{case.project_id}/conversation/messages", json={"message": message}, headers=case.headers("title-patch"))
                turn = case.wait_job(client, response.json()["id"])
                plan = case.wait_job(client, turn["result"]["executions"][0]["id"])
                self.assertEqual("succeeded", plan["status"], plan)
                after = case.app.state.drafts_service.get(case.first, case.project_id)
                self.assertEqual("Scoped title", after["manuscript_fields"]["title"])
                self.assertEqual(before["manuscript_fields"]["keywords"], after["manuscript_fields"]["keywords"])
                changed = repository.get_current_artifact(case.first.user_id, case.project_id, DRAFT_MANUSCRIPT)
                self.assertEqual(original_artifact.metadata.get("keywords_user_omitted"), changed.metadata.get("keywords_user_omitted"))
                message = "把初稿作者改为Alice"
                requested.update(action="draft.publication", arguments={"fields": {"authors": ["Alice"]}}, request_quote=message)
                response = client.post(f"/api/v1/projects/{case.project_id}/conversation/messages", json={"message": message}, headers=case.headers("authors-patch"))
                turn = case.wait_job(client, response.json()["id"])
                plan = case.wait_job(client, turn["result"]["executions"][0]["id"])
                self.assertEqual("succeeded", plan["status"], plan)
                final = case.app.state.conversation_service.actions.services.final_service
                publication, _ = final._read_json(case.first, case.project_id, FINAL_FRONT_MATTER)
                self.assertEqual(["Alice"], publication["authors"])
                self.assertEqual("user_modified", publication["field_states"]["authors"])
                self.assertNotEqual("user_modified", publication["field_states"].get("affiliations"))
        finally:
            case.tearDown()

    def test_revise_and_save_uses_original_revision_worker_and_only_its_candidates(self):
        # Compose an existing isolated fixture, not its inherited test suite.
        from review_writer_api.tests.test_paragraph_dialogue import ParagraphDialogueTests
        case = ParagraphDialogueTests("runTest")
        original = case.extra_native_workflow_overrides
        requested = {}
        def model(prompt, index):
            return {"reply": "", "tools": [{"name": "execute_action", "arguments": requested}]}
        case.extra_native_workflow_overrides = lambda: {**original(), "conversation.model": model}
        case.setUp()
        try:
            with TestClient(case.app) as client:
                case.prepare_draft(client)
                base = f"/api/v1/projects/{case.project_id}/draft"
                before = client.get(base).json()
                section = before["sections"][0]
                message = "修改并保存初稿这一章，明确比较关系"
                requested.update(action="draft.revise_section", arguments={"section_id": section["section_id"], "message": message}, request_quote=message)
                response = client.post(f"/api/v1/projects/{case.project_id}/conversation/messages", json={"message": message}, headers=case.headers("save-chapter"))
                case.job_poll_timeout_seconds = 30
                turn = case.wait_job(client, response.json()["id"])
                plan = case.wait_job(client, turn["result"]["executions"][0]["id"])
                self.assertEqual("succeeded", plan["status"], plan)
                after = client.get(base).json()
                self.assertNotEqual(before["draft_artifact_id"], after["draft_artifact_id"])
                candidates = [row for row in after["rewrite_candidates"] if row.get("batch_job_id") == plan["result"]["completed"][0]["business_job_ids"][0]]
                self.assertTrue(candidates)
                self.assertTrue(all(row["status"] == "accepted" for row in candidates))
                for other in before["sections"][1:]:
                    self.assertEqual(other, next(row for row in after["sections"] if row["section_id"] == other["section_id"]))
        finally:
            case.tearDown()
