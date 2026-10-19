"""Version-bound action proposals, executed only by a user's card adoption.

This adapter calls the same domain/application operations as the stage pages.
It does not invent scientific write executors or expose arbitrary scripts.
"""
from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from review_writer_api.errors import WorkflowConflict, WorkflowNotFound, WorkflowValidationError
from review_writer_api.job_service import job_payload
from review_writer_api.security import Permission
from review_writer_api.workflow_schemas import DiscoverySearchRequest, FigureRedrawRequest, DraftOverviewGenerateRequest, OverviewStructureReference
from review_writer_api.application_operations import (confirm_discovery, submit_blueprint, submit_sections, assemble_draft,
    recommend_topic_outline, acquire_discovery_candidate, generate_overview, job_belongs_to_project)
from review_writer_api.planning_jobs import queue_matrix_enrichment
from review_writer_core.workflow import artifacts as names


class ActionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    topic: str = Field(default="", max_length=10000)
    keywords: str = Field(default="", max_length=10000)
    web_search: bool = False
    restart: bool = False
    artifact_id: str = Field(default="", max_length=36)
    outline_md: str = Field(default="", max_length=250000)
    section_id: str = Field(default="", max_length=96)
    message: str = Field(default="", max_length=12000)
    role: Literal["abstract", "conclusion"] = "abstract"
    figure_ids: list[str] = Field(default_factory=list, max_length=50)
    figure_type: str = Field(default="auto", max_length=64)
    edit_instructions: str = Field(default="", max_length=4000)
    palette: Literal["", "monochrome", "source"] = ""
    language_profile: Literal["en", "zh"] = "en"
    candidate_id: str = Field(default="", max_length=2048)
    image_id: str = Field(default="", max_length=36)
    title: str = Field(default="", max_length=4000)
    instructions: str = Field(default="", max_length=4000)
    structure_references: list[OverviewStructureReference] = Field(default_factory=list, max_length=4)
    source_job_id: str = Field(default="", max_length=36)
    job_id: str = Field(default="", max_length=36)
    paper_id: str = Field(default="", max_length=96)
    metadata_artifact_id: str = Field(default="", max_length=36)
    fields: dict[str, str | int | list[str]] = Field(default_factory=dict, max_length=10)
    candidate_ids: list[str] = Field(default_factory=list, max_length=200)
    selected: bool = False
    paragraph_id: str = Field(default="", max_length=96)
    old_text: str = Field(default="", max_length=12000)
    new_text: str = Field(default="", max_length=12000)


# Each action accepts only its relevant arguments, not the union above.
ACTIONS = {
    "library.metadata": ("保存指定文献书目字段（账号文献库）", {"paper_id", "metadata_artifact_id", "fields"}),
    "discovery.selection": ("纳入／移出指定文献库论文", {"candidate_ids", "selected"}),
    "discovery.select_library": ("纳入全部文献库论文", set()),
    "draft.manuscript_fields": ("保存指定标题／关键词字段", {"fields"}),
    "draft.publication": ("保存指定作者／单位字段", {"fields"}),
    "discovery.search": ("检索论文", {"topic", "keywords", "web_search"}),
    "discovery.confirm": ("采用当前筛选并分析文献", set()),
    "discovery.acquire": ("下载并解析指定联网候选", {"candidate_id"}),
    "planning.analyze": ("继续文献分析", set()),
    "planning.recommend_outline": ("生成主题推荐大纲候选", set()),
    "planning.apply_recommendation": ("采用已生成的主题推荐大纲", {"source_job_id"}),
    "planning.save_outline": ("采用大纲候选", {"outline_md"}),
    "planning.blueprint": ("生成章节规划候选", set()),
    "blueprint.fields": ("保存指定章节规划字段", {"artifact_id", "section_id", "fields"}),
    "blueprint.confirm": ("采用指定章节规划", {"artifact_id"}),
    "sections.generate": ("生成／续跑章节正文", {"restart"}),
    "sections.rewrite": ("生成指定上游整章改写候选", {"section_id", "message", "artifact_id"}),
    "sections.accept_rewrite": ("采用上游章节改写候选", {"artifact_id"}),
    "sections.confirm": ("确认当前章节正文", set()),
    "images.preserve_sources": ("保留原图，不重绘", set()),
    "images.redraw": ("重绘指定图像", {"figure_ids", "figure_type", "edit_instructions", "palette"}),
    "images.confirm": ("确认当前图像选择", set()),
    "draft.assemble": ("组装初稿", set()),
    "draft.sync_section": ("同步指定章节的上游正文到初稿", {"section_id"}),
    "draft.revise_section": ("生成指定初稿章节修改候选", {"section_id", "message"}),
    "draft.revise_paragraph": ("生成指定初稿段落修改候选", {"artifact_id", "section_id", "paragraph_id", "message"}),
    "draft.replace_text": ("精确替换指定初稿段落中的文字", {"artifact_id", "section_id", "paragraph_id", "old_text", "new_text"}),
    "draft.synthesis": ("生成摘要／结论候选", {"role"}),
    "draft.overview": ("生成总览图候选", {"instructions", "structure_references"}),
    "draft.adopt_overview": ("保存指定总览图到稿件", {"image_id", "title"}),
    "draft.accept_candidate": ("采用指定初稿修改候选", {"candidate_id"}),
    "draft.accept_generated": ("保存本次指定范围生成的修改", {"source_job_id", "section_id", "paragraph_id"}),
    "draft.approve": ("批准当前初稿（终稿另行组装）", set()),
    "final.build": ("组装终稿", set()),
    "final.export": ("导出 Word", set()),
    "final.pdf": ("导出 PDF", {"language_profile"}),
    "tasks.cancel": ("申请取消指定项目任务", {"job_id"}),
}


def action_dependencies(name):
    if name == "draft.replace_text":
        return {names.DRAFT_MANUSCRIPT}, {"draft"}
    if name.startswith("library."):
        return set(), set()
    if name == "draft.publication":
        return {names.DRAFT_MANUSCRIPT, names.FINAL_FRONT_MATTER}, {"final"}
    if name == "draft.adopt_overview":
        return {names.DRAFT_MANUSCRIPT, names.FINAL_OVERVIEW_IMAGE, names.FINAL_OVERVIEW_TEXT}, {"draft", "final"}
    if name.startswith("tasks."):
        return set(), set()
    if name.startswith("discovery."):
        return {names.DISCOVERY_REVIEW}, {"discovery"}
    if name == "planning.analyze":
        return {names.MATRIX}, {"matrix"}
    if name == "planning.recommend_outline":
        return {names.MATRIX}, {"matrix"}
    if name in {"planning.save_outline", "planning.apply_recommendation"}:
        return {names.MATRIX, names.PLANNING_OUTLINE}, {"matrix"}
    if name in {"planning.blueprint", "blueprint.confirm", "blueprint.fields"}:
        return {names.MATRIX, names.PLANNING_OUTLINE, names.BLUEPRINT}, {"matrix", "blueprint"}
    if name.startswith("sections."):
        return {names.MATRIX, names.PLANNING_OUTLINE, names.BLUEPRINT, names.SECTION_DRAFTS, names.SECTION_EVIDENCE_PACKAGE}, {"matrix", "blueprint", "sections"}
    if name.startswith("images."):
        return {names.SECTION_DRAFTS, names.FIGURE_REVIEW_SELECTIONS, names.FIGURE_MANIFEST}, {"sections", "figure-review", "figures"}
    if name.startswith("draft."):
        return {names.MATRIX, names.BLUEPRINT, names.SECTION_DRAFTS, names.SECTION_EVIDENCE_PACKAGE,
                names.FIGURE_MANIFEST, names.DRAFT_MANUSCRIPT, names.DRAFT_REWRITE_CANDIDATES,
                names.FINAL_FRONT_MATTER, names.FINAL_OVERVIEW_IMAGE}, {"draft", "sections", "figures"}
    return {names.DRAFT_MANUSCRIPT, names.FINAL_MANUSCRIPT, names.FINAL_RELEASE, names.FINAL_FRONT_MATTER,
            names.FINAL_OVERVIEW_IMAGE, names.FIGURE_MANIFEST, names.MATRIX}, {"draft", "final", "figures"}


class ConversationActions:
    def __init__(self, conversation, container):
        self.conversation, self.services = conversation, container

    def proposal(self, principal, project_id, name, arguments, operation_id):
        self.conversation._owned_project(principal, project_id)
        if name not in ACTIONS or not isinstance(arguments, dict) or set(arguments) - ACTIONS[name][1]:
            raise WorkflowValidationError("This action or its arguments are not connected.")
        try:
            parsed = ActionArguments.model_validate(arguments)
            if name == "discovery.search":
                DiscoverySearchRequest.model_validate(arguments)
            if name == "images.redraw":
                FigureRedrawRequest.model_validate(arguments)
        except ValidationError as exc:
            raise WorkflowValidationError("Invalid action arguments.") from exc
        if name == "planning.save_outline" and not parsed.outline_md:
            raise WorkflowValidationError("The outline candidate is empty.")
        if name == "planning.apply_recommendation" and not parsed.source_job_id:
            raise WorkflowValidationError("Specify the completed topic-outline job.")
        if name == "discovery.acquire" and not parsed.candidate_id:
            raise WorkflowValidationError("Specify the online candidate to download.")
        if name == "draft.adopt_overview" and (not parsed.image_id or not parsed.title):
            raise WorkflowValidationError("Specify the saved overview image and caption.")
        if name == "blueprint.fields":
            from .planning_field_edits import ALLOWED_FIELDS
            if not parsed.artifact_id or not parsed.section_id or not parsed.fields or set(parsed.fields) - ALLOWED_FIELDS:
                raise WorkflowValidationError("Specify the current Blueprint section and literal editable fields.")
        if name == "blueprint.confirm" and not parsed.artifact_id:
            raise WorkflowValidationError("Choose an exact chapter-planning candidate.")
        if name == "images.redraw" and not parsed.figure_ids:
            raise WorkflowValidationError("Specify the images to redraw; no automatic full batch.")
        if name == "draft.revise_section" and (not parsed.section_id or not parsed.message):
            raise WorkflowValidationError("Specify the Draft chapter and revision request.")
        if name in {"draft.revise_paragraph", "draft.replace_text"}:
            if not parsed.section_id or not parsed.paragraph_id or not parsed.artifact_id:
                raise WorkflowValidationError("请先读取当前初稿，定位章节和段落后再修改。")
            from .conversation_draft_edits import resolve_paragraph
            resolve_paragraph(self.services.drafts_service, principal, project_id, arguments)
            if name == "draft.revise_paragraph" and not parsed.message:
                raise WorkflowValidationError("请提供这一段的修改要求。")
            if name == "draft.replace_text" and (not parsed.old_text or "new_text" not in arguments):
                raise WorkflowValidationError("请明确待替换的文字与替换后的文字。")
        if name == "tasks.cancel" and not parsed.job_id:
            raise WorkflowValidationError("Specify one project task to cancel.")
        if name == "draft.accept_generated" and (not parsed.source_job_id or not parsed.section_id):
            raise WorkflowValidationError("Specify the exact completed revision job and chapter.")
        if name == "draft.accept_candidate" and not parsed.candidate_id:
            raise WorkflowValidationError("Specify a current revision candidate.")
        allowed_fields = {"library.metadata": {"title", "authors", "journal", "year", "volume", "issue", "pages", "doi"},
                          "draft.manuscript_fields": {"title", "keywords"}, "draft.publication": {"authors", "affiliations"}}
        if name in allowed_fields:
            if not parsed.fields or set(parsed.fields) - allowed_fields[name]:
                raise WorkflowValidationError("Only the explicitly specified editable fields may be changed.")
            for key, value in parsed.fields.items():
                if key in {"authors", "keywords", "affiliations"}:
                    if not isinstance(value, list) or not all(isinstance(item, str) and len(item) <= 1000 for item in value):
                        raise WorkflowValidationError("This field requires a list of bounded strings.")
                elif (key == "title" and (not isinstance(value, str) or not value.strip())) or not isinstance(value, (str, int)) or len(str(value)) > 2000:
                    raise WorkflowValidationError("This field requires a bounded text or integer value.")
            if name == "library.metadata":
                record = self.services.library_service.get(principal, parsed.paper_id)
                if not parsed.metadata_artifact_id or record.artifact_ids.get("metadata") != parsed.metadata_artifact_id:
                    raise WorkflowConflict("Read the current registered metadata version first; no newer checked fields were overwritten.")
        if name == "discovery.selection" and (not parsed.candidate_ids or "selected" not in arguments):
            raise WorkflowValidationError("Specify the owned library paper IDs to include or remove.")
        preview = {}
        if name == "draft.replace_text":
            from .conversation_draft_edits import replacement_preview, readable_preview
            _, _, replacement = replacement_preview(self.services.drafts_service, principal, project_id, arguments)
            preview = readable_preview(replacement)
            preview["preview_truncated"] = preview.pop("truncated")
        if name == "blueprint.confirm":
            artifact = self.conversation.read_artifact(principal, project_id, parsed.artifact_id)
            if artifact["logical_name"] != names.BLUEPRINT:
                raise WorkflowValidationError("This is not a chapter-planning candidate.")
            preview = {"candidate_text": artifact["text"], "preview_truncated": not artifact["complete"]}
        if name == "draft.sync_section":
            from .draft_sync import DraftSynchronization
            candidate = DraftSynchronization(self.services.drafts_service).preview(principal, project_id, parsed.section_id)
            preview = {"changes": candidate["changes"], "conflicts": candidate["conflicts"],
                       "candidate_text": candidate["candidate_text"][:20000], "preview_truncated": len(candidate["candidate_text"]) > 20000}
        if name == "draft.accept_candidate":
            candidate = next((row for row in self.services.drafts_service.get(principal, project_id)["rewrite_candidates"]
                              if row.get("candidate_id") == parsed.candidate_id), None)
            if not candidate:
                raise WorkflowNotFound("Draft candidate not found.")
            preview = {key: candidate.get(key) for key in ("status", "source_refs", "validation_errors")}
            preview.update({key: str(candidate.get(key) or "")[:20000] for key in ("original_text", "candidate_text")})
            preview["preview_truncated"] = any(len(str(candidate.get(key) or "")) > 20000 for key in ("original_text", "candidate_text"))
        overview = self.conversation.overview(principal, project_id)
        notice = "执行使用原阶段依赖、版本校验与计费；生成成功不等于候选已采用。"
        if name == "draft.assemble":
            notice += " 首次组装可能自动启动摘要／结论生成并计费；后续提交失败不会撤销已保存正文。"
        if name == "sections.generate":
            notice += (" 将从头生成章节，不复用旧任务；可能产生模型费用。" if parsed.restart else
                       " 优先续跑兼容的未完成任务；有效完成章节和写作检查点仍按原规则复用。")
        if name == "discovery.confirm":
            notice += " 确认后可能自动启动文献分析并计费。"
        return {"preview": preview, "id": operation_id, "name": name, "label": ACTIONS[name][0], "arguments": arguments,
                "status": "pending", "expected_artifacts": {a["logical_name"]: a["id"] for a in overview["artifacts"]},
                "expected_revisions": {s["id"]: s["revision"] for stage in overview["stages"] for s in stage["checkpoints"]},
                "notice": notice}

    def validate_baseline(self, principal, project_id, action):
        if action["name"] == "library.metadata":
            args = action["arguments"]
            if self.services.library_service.get(principal, args["paper_id"]).artifact_ids.get("metadata") != args["metadata_artifact_id"]:
                raise WorkflowConflict("文献书目已变化，未覆盖新校对字段。")
        current = self.conversation.overview(principal, project_id)
        # Conservative cross-stage conflict guard: do not apply a proposal based
        # on results that were replaced after the user saw the action card.
        artifact_names, checkpoints = action_dependencies(action["name"])
        expected = {key: action["expected_artifacts"].get(key, "") for key in artifact_names}
        found = {a["logical_name"]: a["id"] for a in current["artifacts"]}
        actual = {key: found.get(key, "") for key in artifact_names}
        revisions = {s["id"]: s["revision"] for stage in current["stages"] for s in stage["checkpoints"]}
        if actual != expected or any(revisions.get(key, 0) != action["expected_revisions"].get(key, 0) for key in checkpoints):
            raise WorkflowConflict("项目成果已变化，请基于最新版本重新提出操作；未覆盖新成果。")

    def execute(self, principal, project_id, action, operation_key):
        principal.require(Permission.PROJECT_WRITE)
        self.validate_baseline(principal, project_id, action)
        name, args = action["name"], action["arguments"]
        c = self.services
        jobs = c.job_service
        revisions = action["expected_revisions"]
        def submit(job_type, payload, operation=""):
            return {"job": job_payload(jobs.submit(principal, scope="project", project_id=project_id,
                       job_type=job_type, idempotency_key=operation_key, operation_key=operation, payload=payload))}
        if name == "library.metadata":
            record = c.library_service.get(principal, args["paper_id"])
            updated = deepcopy(record.metadata)
            for key, value in args["fields"].items():
                old = updated.get(key)
                updated[key] = {**(old if isinstance(old, dict) else {}), "value": value,
                                "human_checked": True, "source": "human_review", "confidence": 1.0}
                for locator in ("source_page", "source_block_index"):
                    updated[key].pop(locator, None)
            saved = c.library_service.update_metadata(principal, record.paper_id, updated,
                    expected_metadata_artifact_id=args["metadata_artifact_id"])
            return {"status": "saved", "paper_id": saved.paper_id, "metadata_artifact_id": saved.artifact_ids.get("metadata", ""),
                    "changed_fields": list(args["fields"])}
        if name == "discovery.select_library":
            return c.discovery_service.select_all_library_papers(principal, project_id)
        if name == "discovery.selection":
            from .project_papers import patch_selection
            return patch_selection(c.discovery_service, principal, project_id,
                revision=revisions.get("discovery", 0),
                add_ids=args["candidate_ids"] if args["selected"] else [],
                remove_ids=[] if args["selected"] else args["candidate_ids"])
        if name == "draft.manuscript_fields":
            from review_writer_api.application_operations import save_manuscript_fields
            state = c.drafts_service.get(principal, project_id)
            values = {**state["manuscript_fields"], **args["fields"]}
            return save_manuscript_fields(c.drafts_service, principal, project_id, revision=revisions.get("draft", 0),
                    title=values["title"], keywords=values["keywords"], changed_fields=set(args["fields"]),
                    expected_draft_artifact_id=action["expected_artifacts"].get(names.DRAFT_MANUSCRIPT))
        if name == "draft.publication":
            state = c.final_service.publication_metadata(principal, project_id)
            return c.final_service.save_publication_metadata(principal, project_id, revision=revisions.get("final", 0),
                    authors=args["fields"].get("authors", state["authors"]), affiliations=args["fields"].get("affiliations", state["affiliations"]),
                    changed_fields=set(args["fields"]), expected_draft_artifact_id=action["expected_artifacts"].get(names.DRAFT_MANUSCRIPT, ""),
                    expected_front_matter_artifact_id=action["expected_artifacts"].get(names.FINAL_FRONT_MATTER, ""))
        if name == "tasks.cancel":
            job = jobs.status(principal, args["job_id"])
            if not job_belongs_to_project(job, project_id) or job.job_type == "model.dispatch":
                raise WorkflowValidationError("Cancel an explicit parent task in this project, not a foreign/internal model task.")
            cancelled = jobs.request_cancel(principal, job.id)
            return {"status": cancelled.status, "cancelled_job": job_payload(cancelled)}
        if name == "discovery.search":
            request = DiscoverySearchRequest.model_validate(args)
            payload = c.discovery_service.search_payload(principal, project_id, request.model_dump())
            return submit("discovery.search", payload)
        if name == "discovery.acquire":
            return {"job": job_payload(acquire_discovery_candidate(c.discovery_service, jobs, principal, project_id,
                args["candidate_id"], revisions.get("discovery", 0), operation_key))}
        if name == "discovery.confirm":
            return confirm_discovery(c.discovery_service, c.planning_service, jobs, principal, project_id, revisions.get("discovery", 0))
        if name == "planning.analyze":
            return {"job": job_payload(queue_matrix_enrichment(c.planning_service, jobs, principal, project_id, idempotency_key=operation_key))}
        if name == "planning.recommend_outline":
            return {"job": recommend_topic_outline(c.planning_service, jobs, principal, project_id)}
        if name == "planning.apply_recommendation":
            from review_writer_core.stages.planning.topic import TOPIC_GUIDED_STYLE
            from review_writer_core.stages.planning.topic_recommendation import recommendation_inputs_match
            source = jobs.status(principal, args["source_job_id"])
            inputs = c.planning_service.topic_recommendation_input(principal, project_id)
            if (source.project_id != project_id or source.job_type != "planning.topic-outline" or source.status != "succeeded"
                    or not recommendation_inputs_match(source.payload, inputs) or not source.result.get("outline_md")):
                raise WorkflowConflict("推荐大纲或文献来源已变化，请生成或查看当前推荐；未采用旧大纲。")
            return c.planning_service.save_outline(principal, project_id, revision=revisions.get("matrix", 0),
                outline_style=TOPIC_GUIDED_STYLE, outline_md=source.result["outline_md"],
                candidate_outline_md=source.result["outline_md"], manual=False)
        if name == "planning.save_outline":
            return c.planning_service.save_outline(principal, project_id, revision=revisions.get("matrix", 0),
                    outline_style="custom", outline_md=args["outline_md"], manual=True)
        if name == "planning.blueprint":
            return {"job": job_payload(submit_blueprint(c.planning_service, jobs, principal, project_id, revisions.get("blueprint", 0), operation_key))}
        if name == "blueprint.fields":
            from .planning_field_edits import patch_blueprint_fields
            return patch_blueprint_fields(c.planning_service, principal, project_id, section_id=args["section_id"],
                fields=args["fields"], expected_artifact_id=args["artifact_id"])
        if name == "blueprint.confirm":
            return c.planning_service.confirm_blueprint(principal, project_id, revision=revisions.get("blueprint", 0), artifact_id=args["artifact_id"])
        if name == "sections.generate":
            return {"job": job_payload(submit_sections(c.sections_service, jobs, principal, project_id, operation_key,
                                                      resume=not args.get("restart", False), restart=bool(args.get("restart", False))))}
        if name == "sections.rewrite":
            from .section_rewrites import SectionRewrites
            payload = SectionRewrites(c.sections_service).prepare(principal, project_id, args["section_id"], args["message"], args["artifact_id"])
            return submit("sections.generate", payload, "scoped-upstream-rewrite")
        if name == "sections.accept_rewrite":
            from .section_rewrites import SectionRewrites
            return SectionRewrites(c.sections_service).accept(principal, project_id, args["artifact_id"])
        if name == "sections.confirm":
            return c.sections_service.confirm(principal, project_id, revision=revisions.get("sections", 0))
        if name == "images.preserve_sources":
            return c.figures_service.preserve_all_sources(principal, project_id)
        if name == "images.redraw":
            request = FigureRedrawRequest.model_validate(args)
            payload = c.figures_service.redraw_job_payload(principal, project_id, figure_ids=request.figure_ids,
                    figure_type=request.figure_type, retry_of_job_id=None, origin="batch", edit_instructions=request.edit_instructions, palette=request.palette)
            return submit("figures.redraw", payload)
        if name == "images.confirm":
            return c.figures_service.confirm(principal, project_id, revision=revisions.get("figures", 0))
        if name == "draft.assemble":
            return assemble_draft(c.drafts_service, jobs, principal, project_id)
        if name == "draft.sync_section":
            from .draft_sync import DraftSynchronization
            return DraftSynchronization(c.drafts_service).save(principal, project_id, args["section_id"],
                expected_draft_artifact_id=action["expected_artifacts"].get(names.DRAFT_MANUSCRIPT, ""),
                expected_sections_artifact_id=action["expected_artifacts"].get(names.SECTION_DRAFTS, ""))
        if name == "draft.revise_section":
            section_id = args["section_id"]
            sections = c.drafts_service.draft_dialogue_sections(principal, project_id)
            section = next((s for s in sections if s["section_id"] == section_id), None)
            if not section:
                raise WorkflowNotFound("Draft chapter not found.")
            payload, existing = c.drafts_service.section_dialogue_payload(principal, project_id, section_id,
                    action="revise", message=args["message"], base_hashes={p["paragraph_key"]: p["text_sha256"] for p in section["paragraphs"]},
                    branch_id="", initial_artifact_id="", idempotency_key=operation_key)
            return {"job": job_payload(existing)} if existing else submit("draft.optimize", payload, f"section:{section_id}")
        if name == "draft.revise_paragraph":
            from .conversation_draft_edits import resolve_paragraph
            _, paragraph = resolve_paragraph(c.drafts_service, principal, project_id, args)
            payload, existing = c.drafts_service.dialogue_payload(principal, project_id, paragraph["paragraph_key"],
                message=args["message"], base_text_sha256=paragraph["text_sha256"], use_saved=True, idempotency_key=operation_key)
            if payload is not None:
                payload["section_id"] = args["section_id"]
            return {"job": job_payload(existing)} if existing else submit("draft.rewrite", payload, f"paragraph:{paragraph['paragraph_key']}")
        if name == "draft.replace_text":
            from .conversation_draft_edits import replacement_preview
            current, paragraph, preview = replacement_preview(c.drafts_service, principal, project_id, args)
            result = c.drafts_service.save_paragraph(principal, project_id, args["paragraph_id"],
                text=preview["candidate_text"], revision=current["revision"], base_text_sha256=paragraph["text_sha256"])
            from .conversation_draft_edits import readable_preview
            return {**result, "edit_preview": readable_preview(preview, status="accepted")}
        if name == "draft.synthesis":
            role = args.get("role", "abstract")
            payload = c.drafts_service.synthesis_payload(principal, project_id, role)
            payload["candidate_seed"] = str(uuid.uuid5(uuid.NAMESPACE_URL, project_id + ":" + operation_key))
            return submit("draft.optimize", payload, "synthesis:" + role)
        if name == "draft.overview":
            return {"job": job_payload(generate_overview(c.final_service, jobs, principal, project_id,
                DraftOverviewGenerateRequest.model_validate(args), operation_key))}
        if name == "draft.adopt_overview":
            return c.final_service.adopt_overview(principal, project_id, image_id=args["image_id"],
                title=args["title"], revision=revisions.get("final", 0))
        if name == "draft.accept_candidate":
            # Resolve the candidate's exact persisted base, not a model-provided hash.
            candidate = next((row for row in c.drafts_service.get(principal, project_id)["rewrite_candidates"] if row.get("candidate_id") == args["candidate_id"]), None)
            if not candidate:
                raise WorkflowNotFound("Draft candidate not found.")
            if candidate.get("revision_mode") == "section_synthesis":
                return c.drafts_service.decide_synthesis(principal, project_id, args["candidate_id"], decision="accept",
                    expected_base_text_sha256=candidate.get("base_text_sha256"))
            return c.drafts_service.decide_dialogue(principal, project_id, args["candidate_id"], decision="accept",
                    expected_base_text_sha256=candidate.get("base_text_sha256"))
        if name == "draft.accept_generated":
            from .conversation_draft_edits import generated_candidates
            candidates = generated_candidates(c.drafts_service, jobs, principal, project_id, args)
            adopted = []
            for row in candidates:
                if row.get("status") == "kept_original":
                    continue
                c.drafts_service.decide_dialogue(principal, project_id, row["candidate_id"], decision="accept",
                        expected_base_text_sha256=row.get("base_text_sha256"))
                adopted.append(row["candidate_id"])
            state = c.drafts_service.get(principal, project_id)
            from .conversation_draft_edits import readable_preview
            return {"status": "saved" if adopted else "kept_original", "revision": state["revision"], "candidate_ids": adopted,
                    **({"edit_preview": readable_preview(candidates[0], status="accepted" if adopted else "kept_original")}
                       if args.get("paragraph_id") else {})}
        if name == "draft.approve":
            return c.drafts_service.approve(principal, project_id, revision=revisions.get("draft", 0), override_low_score=False, override_reason="")
        if name == "final.build":
            return submit("final.build", c.final_service.build_payload(principal, project_id))
        if name == "final.export":
            return submit("final.export", c.final_service.export_payload(principal, project_id))
        if name == "final.pdf":
            return submit("final.pdf", c.final_service.pdf_payload(principal, project_id, language_profile=args.get("language_profile", "en")))
        raise WorkflowValidationError("Action is not connected.")
