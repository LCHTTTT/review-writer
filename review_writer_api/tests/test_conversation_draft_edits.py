"""Isolated paragraph writes and candidates; no live providers or user data."""
import pytest
from fastapi.testclient import TestClient
from review_writer_api.domain_services.conversation_authorization import authorize_action, revision_save_requested
from review_writer_api.domain_services.conversation_draft_edits import replacement_preview, readable_preview
from review_writer_api.errors import WorkflowValidationError, WorkflowConflict, WorkflowNotFound
from types import SimpleNamespace


@pytest.fixture
def prepared():
    from review_writer_api.tests.test_paragraph_dialogue import ParagraphDialogueTests
    case = ParagraphDialogueTests("runTest")
    original = case.extra_native_workflow_overrides
    requested = {}
    calls = []
    def model(prompt, index):
        calls.append(index)
        return {"reply":"", "tools":[{"name":"execute_action", "arguments":requested}]}
    case.extra_native_workflow_overrides = lambda: {**original(), "conversation.model":model}
    case.setUp()
    case.job_poll_timeout_seconds = 30
    try:
        with TestClient(case.app) as client:
            case.prepare_draft(client)
            base = f"/api/v1/projects/{case.project_id}/draft"
            before = client.get(base).json()
            section = before["sections"][0]
            paragraph = section["paragraphs"][0]
            args = {"artifact_id":before["draft_artifact_id"], "section_id":section["section_id"], "paragraph_id":paragraph["paragraph_id"]}
            yield case, client, base, before, paragraph, requested, args, calls
    finally:
        case.tearDown()


def run(prepared, action, message, arguments):
    case, client, _, _, _, requested, _, _ = prepared
    requested.update(action=action, arguments=arguments, request_quote=message)
    response = client.post(f"/api/v1/projects/{case.project_id}/conversation/messages", json={"message":message}, headers=case.headers("scoped-edit"))
    assert response.status_code == 202, response.text
    turn = case.wait_job(client, response.json()["id"])
    assert turn["status"] == "succeeded", turn
    plan = case.wait_job(client, turn["result"]["executions"][0]["id"])
    assert plan["status"] == "succeeded", plan
    return plan


@pytest.mark.parametrize("save", [True, False])
def test_one_paragraph_revision_reuses_worker_and_only_saves_when_requested(prepared, save):
    case, client, base, before, paragraph, _, args, calls = prepared
    message = "润色初稿这一段并保存，不要修改其他段落" if save else "润色初稿这一段，先不要保存"
    plan = run(prepared, "draft.revise_paragraph", message, {**args,"message":message})
    after = client.get(base).json()
    assert case.rewrite_paragraph_ids == [paragraph["paragraph_id"]]
    assert len(calls) == 1
    assert (after["draft_artifact_id"] != before["draft_artifact_id"]) == save
    for row in before["paragraphs"]:
        current = next(p for p in after["paragraphs"] if p["paragraph_id"] == row["paragraph_id"])
        if row["paragraph_id"] != paragraph["paragraph_id"]:
            assert current["text"] == row["text"]
    assert "edit_preview" in plan["result"]["completed"][-1]["exported"], plan["result"]["completed"]
    preview = plan["result"]["completed"][-1]["exported"]["edit_preview"]
    assert preview["status"] == ("accepted" if save else "pending")
    assert preview["paragraph_id"] == paragraph["paragraph_id"]


def test_literal_replace_uses_manual_save_without_revision_model(prepared):
    case, client, base, before, paragraph, _, args, calls = prepared
    old = paragraph["text"]
    new = old + " Explicit user wording."
    message = f'把初稿这一段的“{old}”替换为“{new}”并保存'
    plan = run(prepared, "draft.replace_text", message, {**args,"old_text":old,"new_text":new})
    after = client.get(base).json()
    assert len(calls) == 1
    assert not getattr(case,"rewrite_paragraph_ids", [])
    for row in before["paragraphs"]:
        current = next(p for p in after["paragraphs"] if p["paragraph_id"] == row["paragraph_id"])
        assert current["text"] == (new if row["paragraph_id"] == paragraph["paragraph_id"] else row["text"])
    assert plan["result"]["completed"][0]["exported"]["edit_preview"]["status"] == "accepted"
    repeated = client.post(f"/api/v1/projects/{case.project_id}/conversation/messages",json={"message":message},headers=case.headers("scoped-edit"))
    assert repeated.status_code == 202
    assert client.get(base).json()["draft_artifact_id"] == after["draft_artifact_id"]
    assert len(calls) == 1


def test_targeted_draft_read_returns_current_identifiers_without_writes(prepared):
    case, client, base, before, paragraph, _, args, _ = prepared
    service = case.app.state.conversation_service
    directory = service.read_draft(case.first, case.project_id)
    assert directory["artifact_id"] == before["draft_artifact_id"]
    selected = service.read_draft(case.first, case.project_id, section_id=args["section_id"],paragraph_id=args["paragraph_id"])
    assert paragraph["text"] in selected["text"]
    assert selected["paragraph_id"] == args["paragraph_id"]
    assert client.get(base).json()["draft_artifact_id"] == before["draft_artifact_id"]
    with pytest.raises(WorkflowNotFound):service.read_draft(case.second, case.project_id, section_id=args["section_id"])


def test_literal_target_requires_current_artifact_and_unique_text():
    paragraph={"paragraph_id":"S1-p1","paragraph_key":"key","text":"A A","text_sha256":"hash"}
    drafts=SimpleNamespace(get=lambda *a:{"draft_artifact_id":"current"},draft_dialogue_sections=lambda *a:[{"section_id":"S1","paragraphs":[paragraph]}])
    args={"section_id":"S1","paragraph_id":"S1-p1","artifact_id":"current","old_text":"A","new_text":"B"}
    with pytest.raises(WorkflowValidationError):replacement_preview(drafts,None,"p",args)
    with pytest.raises(WorkflowConflict):replacement_preview(drafts,None,"p",{**args,"artifact_id":"stale"})
    with pytest.raises(WorkflowNotFound):replacement_preview(drafts,None,"p",{**args,"section_id":"other"})


def test_authorization_does_not_expand_scope_or_invent_literal_text():
    message="只改初稿第三段，先不要保存"
    authorize_action(message,message,"draft.revise_paragraph",{"message":message})
    assert not revision_save_requested(message)
    with pytest.raises(WorkflowValidationError):authorize_action(message,message,"draft.revise_section",{})
    with pytest.raises(WorkflowValidationError):authorize_action("把 A 替换为 B","把 A 替换为 B","draft.replace_text",{"old_text":"A","new_text":"invented"})
    with pytest.raises(WorkflowValidationError):authorize_action("把 A 替换为 B，只预览","把 A 替换为 B，只预览","draft.replace_text",{"old_text":"A","new_text":"B"})
    assert readable_preview({"original_text":"a"*20001,"candidate_text":"b"})["truncated"]


def test_generated_candidate_cannot_be_adopted_for_another_target():
    from review_writer_api.domain_services.conversation_draft_edits import generated_candidates
    request={"paragraph_id":"S1-p1","paragraph_key":"key","turn_id":"candidate"}
    source=SimpleNamespace(project_id="p",status="succeeded",job_type="draft.rewrite",payload={"section_id":"S1","revision_mode":"dialogue","dialogue":request})
    jobs=SimpleNamespace(status=lambda *a:source)
    drafts=SimpleNamespace(get=lambda *a:{"rewrite_candidates":[{"candidate_id":"candidate","paragraph_key":"key"}]})
    args={"source_job_id":"job","section_id":"S1","paragraph_id":"S1-p2"}
    with pytest.raises(WorkflowValidationError):generated_candidates(drafts,jobs,None,"p",args)
    with pytest.raises(WorkflowValidationError):generated_candidates(drafts,jobs,None,"another-project",{**args,"paragraph_id":"S1-p1"})
