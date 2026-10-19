"""Optional read-only project dialogue. Workflow jobs hold durable turns.

No stage service calls into this module; reading never approves or publishes work.
Write/continuous tools are intentionally not exposed until their shared contracts
have been implemented and tested.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import select, or_, and_

from review_writer_api.database import AIModelRequest, database_session
from review_writer_api.domain_services.base import OwnedProjectService
from review_writer_api.errors import ConversationModelFailure, WorkflowValidationError, WorkflowNotFound, WorkflowConflict, WorkflowStageNotReady
from review_writer_api.job_service import job_payload
from review_writer_api.security import Principal, Permission
from review_writer_api.workflow_contracts import STAGE_CHECKPOINTS
from review_writer_api.workflow_models import WorkflowJob, LibraryPaper
from review_writer_core.workflow import artifacts as names

# Public labels are independent of legacy artifact prefixes and job names.
RESULTS = (
    ("discovery", "筛选结果", names.DISCOVERY_REVIEW),
    ("planning", "文献分析", names.MATRIX),
    ("planning", "选定大纲", names.PLANNING_OUTLINE),
    ("sections", "章节规划", names.BLUEPRINT),
    ("sections", "章节正文", names.SECTION_DRAFTS),
    ("sections", "正文来源绑定", names.SECTION_EVIDENCE_PACKAGE),
    ("images", "原图候选", names.SECTION_PAPER_FIGURE_CANDIDATES),
    ("images", "图像选择", names.FIGURE_REVIEW_SELECTIONS),
    ("images", "图像成果", names.FIGURE_MANIFEST),
    ("draft", "初稿", names.DRAFT_MANUSCRIPT),
    ("draft", "初稿修改候选", names.DRAFT_REWRITE_CANDIDATES),
    ("draft", "总览图", names.FINAL_OVERVIEW_IMAGE),
    ("draft", "总览图说明", names.FINAL_OVERVIEW_TEXT),
    ("final", "出版信息", names.FINAL_FRONT_MATTER),
    ("final", "终稿", names.FINAL_MANUSCRIPT),
    ("final", "终稿问题", names.FINAL_VALIDATION),
    ("final", "发布状态", names.FINAL_RELEASE),
    ("final", "Word", names.FINAL_DOCX),
    ("final", "PDF", names.FINAL_PDF),
)
ALLOWED_RESULTS = {name for _, _, name in RESULTS}
ACTIVE = {"queued", "running", "cancel_requested"}


class ConversationService(OwnedProjectService):
    def __init__(self, repository, artifacts, library):
        self.repository, self.artifacts, self.library = repository, artifacts, library

    def _owned_project(self, principal, project_id):
        if project_id is None:
            principal.require(Permission.PROJECT_READ)
            return None  # actual account preparation scope, never a placeholder project
        return super()._owned_project(principal, project_id)

    def overview(self, principal, project_id):
        if project_id is None:
            principal.require(Permission.PROJECT_READ)
            return {"scope": "account_preparation", "project_id": None, "stages": [], "artifacts": [], "jobs": [],
                    "capabilities": {"discussion": True, "project_proposals": True, "stage_execution": False}}
        project = self._owned_project(principal, project_id)
        stages = []
        cards = []
        for stage, checkpoints in STAGE_CHECKPOINTS.items():
            states = [self.repository.get_stage_state(principal.user_id, project_id, key) for key in checkpoints]
            stages.append({"id": stage, "checkpoints": [
                {"id": key, "status": state.status if state else "pending", "revision": state.revision if state else 0}
                for key, state in zip(checkpoints, states)
            ]})
        for stage, label, name in RESULTS:
            artifact = self.repository.get_current_artifact(principal.user_id, project_id, name)
            if artifact:
                cards.append(self.card(artifact, stage, label, True))
        jobs = self.repository.list_project_jobs(principal.user_id, project_id, limit=100, include_payload=False)
        return {"project_id": project_id, "topic": project.topic, "model_tier": project.model_tier,
                "stages": stages, "artifacts": cards,
                "jobs": [job_payload(j) for j in jobs if j.status in ACTIVE],
                "capabilities": {"discussion": True, "read_tools": True,
                                 "natural_language_writes": hasattr(self, "execution"), "action_proposals": hasattr(self, "actions"),
                                 "continuous_execution": hasattr(self, "execution")}}

    @staticmethod
    def card(artifact, stage, label, current):
        suffix = Path(artifact.logical_name).suffix.lower()
        return {"id": artifact.id, "stage": stage, "label": label,
                "logical_name": artifact.logical_name, "current": current,
                "created_at": artifact.created_at.isoformat() if artifact.created_at else "",
                "format": suffix.lstrip("."), "sha256": artifact.content_sha256,
                "url": f"/api/v1/artifacts/{artifact.id}/content"}

    def _artifact_text(self, principal, project_id, artifact_id):
        self._owned_project(principal, project_id)
        resolved = self.artifacts.resolve_owned_artifact(principal.user_id, artifact_id)
        artifact = resolved.artifact
        if artifact.project_id != project_id or artifact.logical_name not in ALLOWED_RESULTS:
            raise WorkflowNotFound("Conversation artifact not found.")
        if Path(artifact.logical_name).suffix not in {".md", ".json"}:
            raise WorkflowValidationError("Open this image or download file in the viewer.")
        # Bound reads before decoding; never load a whole PDF/image into a prompt.
        if artifact.size_bytes > 8 * 1024 * 1024:
            raise WorkflowValidationError("This artifact is too large for conversation preview.")
        value = resolved.path.read_text(encoding="utf-8")
        current = self.repository.get_current_artifact(principal.user_id, project_id, artifact.logical_name)
        return value, {"artifact_id": artifact.id, "logical_name": artifact.logical_name,
                       "current": bool(current and current.id == artifact.id)}

    @staticmethod
    def _read_range(value, metadata, offset, limit):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 20000:
            raise WorkflowValidationError("Invalid read range.")
        return {**metadata, "offset": offset,
                "text": value[offset:offset + limit], "total_chars": len(value),
                "complete": offset == 0 and len(value) <= limit,
                "next_offset": offset + limit if offset + limit < len(value) else None}

    def read_artifact(self, principal, project_id, artifact_id, *, offset=0, limit=12000, view="raw", language="zh"):
        # UI/download and write-version checks retain the exact raw artifact contract.
        value, metadata = self._artifact_text(principal, project_id, artifact_id)
        if view not in {"raw", "readable"} or language not in {"zh", "en"}:
            raise WorkflowValidationError("Unknown artifact presentation.")
        if view == "readable":
            if metadata["logical_name"] == names.SECTION_DRAFTS:
                from review_writer_api.domain_services.conversation_reading import section_view
                value, details = section_view(value, section_id="*")
                metadata = {**metadata, **details}
            elif metadata["logical_name"].endswith(".json"):
                from review_writer_api.domain_services.conversation_artifact_display import readable_artifact
                value = readable_artifact(value, language=language)
            metadata = {**metadata, "presentation": "markdown"}
        return self._read_range(value, metadata, offset, limit)

    def read_section(self, principal, project_id, *, artifact_id=None, section_id="", paragraph_id="", view="prose", offset=0, limit=12000):
        self._owned_project(principal, project_id)
        if artifact_id is None:
            current = self.repository.get_current_artifact(principal.user_id, project_id, names.SECTION_DRAFTS)
            if current is None:
                return {"available": False, "scope": "sections", "note": "No saved chapter body yet."}
            artifact_id = current.id
        value, metadata = self._artifact_text(principal, project_id, artifact_id)
        if metadata["logical_name"] != names.SECTION_DRAFTS:
            raise WorkflowValidationError("Read chapters from the section drafts artifact, not Draft or another result.")
        from review_writer_api.domain_services.conversation_reading import section_view
        text, details = section_view(value, section_id=section_id, paragraph_id=paragraph_id, view=view)
        return self._read_range(text, {**metadata, **details}, offset, limit)

    def read_draft(self, principal, project_id, *, section_id="", paragraph_id="", offset=0, limit=12000):
        self._owned_project(principal, project_id)
        current = self.repository.get_current_artifact(principal.user_id, project_id, names.DRAFT_MANUSCRIPT)
        if not current:
            return {"available": False, "scope": "draft", "note": "No current Draft yet."}
        text, metadata = self._artifact_text(principal, project_id, current.id)
        if not metadata["current"]:
            raise WorkflowConflict("初稿读取期间已更新，请读取最新版本。")
        from review_writer_core.paragraph_revision import dialogue_sections
        sections = dialogue_sections(text, current.metadata, current.id)
        if not section_id:
            if paragraph_id:
                raise WorkflowValidationError("请先定位初稿章节，再读取其中的段落。")
            value = json.dumps([{"section_id":row["section_id"],"title":row["title"],
                "paragraphs":[{"paragraph_id":p["paragraph_id"],"excerpt":p["text"][:160]} for p in row["paragraphs"]]}
                for row in sections], ensure_ascii=False)
        else:
            section = next((row for row in sections if row["section_id"] == section_id), None)
            if not section:
                raise WorkflowNotFound("初稿章节不存在。")
            paragraphs = [p for p in section["paragraphs"] if not paragraph_id or p["paragraph_id"] == paragraph_id]
            if paragraph_id and not paragraphs:
                raise WorkflowNotFound("指定段落不属于这一章。")
            value = "\n\n".join(f"[{p['paragraph_id']}]\n{p['text']}" for p in paragraphs)
        return self._read_range(value, {**metadata,"scope":"draft","section_id":section_id,"paragraph_id":paragraph_id}, offset, limit)

    def history(self, principal, project_id, *, before="", limit=20, session_id=""):
        self._owned_project(principal, project_id)
        self.sessions.get(principal, project_id, session_id)
        with database_session(self.repository.session_factory) as session:
            query = select(WorkflowJob).where(
                WorkflowJob.user_id == uuid.UUID(principal.user_id),
                (and_(WorkflowJob.project_id.is_(None), WorkflowJob.job_type == "conversation.prepare",
                      WorkflowJob.result_json["bound_project_id"].as_string().is_(None)) if project_id is None else
                 or_(and_(WorkflowJob.project_id == uuid.UUID(project_id), WorkflowJob.job_type.in_(("conversation.turn", "conversation.control"))),
                     and_(WorkflowJob.job_type == "conversation.prepare", WorkflowJob.result_json["bound_project_id"].as_string() == project_id))),
            )
            session_value = WorkflowJob.payload_json["session_id"].as_string()
            query = query.where(session_value == session_id if session_id else or_(session_value.is_(None), session_value == ""))
            if before:
                cursor = self.repository.get_job(principal.user_id, before)
                if not cursor or (cursor.payload.get("session_id") or "") != session_id or not ((cursor.project_id == project_id and cursor.job_type in {"conversation.turn", "conversation.control"}) or
                    (cursor.job_type == "conversation.prepare" and cursor.project_id is None and cursor.result.get("bound_project_id") == project_id)):
                    raise WorkflowNotFound("Conversation cursor not found.")
                query = query.where((WorkflowJob.created_at < cursor.created_at) |
                    ((WorkflowJob.created_at == cursor.created_at) & (WorkflowJob.id < uuid.UUID(cursor.id))))
            rows = list(session.scalars(query.order_by(WorkflowJob.created_at.desc(), WorkflowJob.id.desc()).limit(limit + 1)))
            records = [self.repository._job_record(row) for row in rows[:limit]]
            # Read-only compatibility for old opaque failures. Use the actual
            # owned gateway record, never guess a timeout from elapsed time.
            legacy = {j.id for j in records if j.status == "failed" and j.error_code in {"JOB_EXECUTION_FAILED", ConversationModelFailure.code}}
            failures = {}
            if legacy:
                failed_models = session.scalars(select(AIModelRequest).where(
                    AIModelRequest.user_id == uuid.UUID(principal.user_id), AIModelRequest.status == "failed",
                    AIModelRequest.job_id.in_([uuid.UUID(value) for value in legacy])).order_by(AIModelRequest.created_at.desc()))
                for model in failed_models:
                    key = str(model.job_id)
                    if key not in failures:
                        try:
                            error = json.loads(model.error_message)
                        except (ValueError, TypeError):
                            continue
                        if isinstance(error, dict) and isinstance(error.get("details"), dict):
                            failures[key] = str(ConversationModelFailure.from_provider(error))
        turns = [{**job_payload(j), "message": j.payload.get("message", ""),
                  "object_id": j.payload.get("object_id", ""),
                  "attachments": self.attachments.descriptors(principal, j.payload.get("attachment_ids", []))} for j in reversed(records)]
        for turn in turns:
            if turn["id"] in failures:
                turn.update(error_code=ConversationModelFailure.code, error_message=failures[turn["id"]])
        return {"turns": turns, "next_cursor": records[-1].id if len(rows) > limit else None}

    def bind_preparation(self, principal, project_id, turn_ids, *, session_id=""):
        principal.require(Permission.PROJECT_WRITE)
        self._owned_project(principal, project_id)
        with database_session(self.repository.session_factory) as session:
            rows = list(session.scalars(select(WorkflowJob).where(WorkflowJob.id.in_(turn_ids),
                WorkflowJob.user_id == uuid.UUID(principal.user_id), WorkflowJob.project_id.is_(None),
                WorkflowJob.job_type == "conversation.prepare").with_for_update()))
            if len(rows) != len(set(turn_ids)):
                raise WorkflowNotFound("Preparation messages not found.")
            for row in rows:
                value = dict(row.result_json or {})
                if row.status in ACTIVE or value.get("bound_project_id") not in {None, project_id}:
                    raise WorkflowConflict("Preparation is still running or already belongs to another project.")
                row.result_json = {**value, "bound_project_id": project_id}
            session_ids = {uuid.UUID(row.payload_json["session_id"]) for row in rows if (row.payload_json or {}).get("session_id")}
            if session_id:
                session_ids.add(uuid.UUID(session_id))
            for identifier in session_ids:
                record = session.scalar(select(WorkflowJob).where(WorkflowJob.id == identifier,
                    WorkflowJob.user_id == uuid.UUID(principal.user_id), WorkflowJob.job_type == "conversation.session").with_for_update())
                if not record or record.status != "succeeded" or record.project_id is not None or (record.result_json or {}).get("bound_project_id") not in {None, project_id}:
                    raise WorkflowConflict("Discussion cannot be bound to this project.")
                record.result_json = {**(record.result_json or {}), "bound_project_id": project_id}
                # A named preparation discussion moves as a whole, including older pages.
                members = list(session.scalars(select(WorkflowJob).where(WorkflowJob.user_id == uuid.UUID(principal.user_id),
                    WorkflowJob.project_id.is_(None), WorkflowJob.job_type == "conversation.prepare",
                    WorkflowJob.payload_json["session_id"].as_string() == str(identifier)).with_for_update()))
                for member in members:
                    if member.status in ACTIVE or (member.result_json or {}).get("bound_project_id") not in {None, project_id}:
                        raise WorkflowConflict("Preparation is still active or bound elsewhere; no history was moved.")
                    member.result_json = {**(member.result_json or {}), "bound_project_id": project_id}
        return {"project_id": project_id, "bound_turn_ids": [str(value) for value in turn_ids]}

    def steering_after(self, principal, project_id, turn_id, *, include_finished=False):
        if project_id is None:
            return None
        current = self.repository.get_job(principal.user_id, turn_id)
        candidates = [row for kind in ("conversation.turn", "conversation.control")
                      for row in self.repository.list_project_jobs(principal.user_id, project_id, job_type=kind, limit=100)]
        for candidate in sorted(candidates, key=lambda row: (row.created_at, row.id)):
            if ((candidate.status == "queued" or (include_finished and candidate.status != "cancelled")) and candidate.payload.get("delivery") == "steer"
                    and candidate.payload.get("session_id", "") == current.payload.get("session_id", "")
                    and (candidate.created_at, candidate.id) > (current.created_at, current.id)):
                return candidate
        return None

    def execute_read(self, principal: Principal, project_id: str, name: str, args: dict):
        if name == "project_papers" and set(args) <= {"query", "offset", "limit"}:
            from .project_papers import library_choices
            return library_choices(self.actions.services.discovery_service, principal, project_id, **args)
        if name == "project_state" and not args:
            return self.overview(principal, project_id)
        if name == "stage_details" and set(args) == {"stage"}:
            self._owned_project(principal, project_id)
            stage = args["stage"]
            services = self.actions.services
            readers = {"discovery": services.discovery_service, "planning": services.planning_service,
                       "sections": services.sections_service, "images": services.figures_service,
                       "draft": services.drafts_service, "final": services.final_service}
            if not isinstance(stage, str) or stage not in readers:
                raise WorkflowValidationError("Unknown stage reader.")
            from .conversation_context import stage_summary
            try:
                details = readers[stage].get(principal, project_id)
            except WorkflowStageNotReady:
                return {"stage": stage, "availability": "not_generated", "details": {}, "complete": True,
                        "note": "No current output yet. This is a normal workflow state, not a read failure. Use project_state and available_actions to continue from existing prerequisites.",
                        "artifacts": []}
            return {"stage": stage, "availability": "available", **stage_summary(details),
                    "artifacts": [a for a in self.overview(principal, project_id)["artifacts"] if a["stage"] == stage]}
        if name == "read_section" and set(args) <= {"artifact_id", "section_id", "paragraph_id", "view", "offset", "limit"}:
            return self.read_section(principal, project_id, **args)
        if name == "read_draft" and set(args) <= {"section_id", "paragraph_id", "offset", "limit"}:
            return self.read_draft(principal, project_id, **args)
        if name == "read_artifact" and set(args) <= {"artifact_id", "offset", "limit", "view"}:
            if not args.get("artifact_id"):
                raise WorkflowValidationError("Select an artifact ID to read.")
            view = args.get("view", "prose")
            if view not in {"prose", "raw"}:
                raise WorkflowValidationError("Unknown artifact reading view.")
            options = {key: value for key, value in args.items() if key != "view"}
            if view == "prose":
                raw, metadata = self._artifact_text(principal, project_id, args["artifact_id"])
                if metadata["logical_name"] == names.SECTION_DRAFTS:
                    # Old/model callers get prose as well, not a random slice of evidence JSON.
                    from review_writer_api.domain_services.conversation_reading import section_view
                    text, details = section_view(raw, section_id="*")
                    return self._read_range(text, {**metadata, **details}, args.get("offset", 0), args.get("limit", 12000))
                return self._read_range(raw, metadata, args.get("offset", 0), args.get("limit", 12000))
            return self.read_artifact(principal, project_id, **options)
        if name == "list_papers" and set(args) <= {"query"}:
            self._owned_project(principal, project_id)
            query_text = str(args.get("query") or "")[:200]
            with database_session(self.repository.session_factory) as session:
                statement = select(LibraryPaper).where(LibraryPaper.user_id == uuid.UUID(principal.user_id),
                    LibraryPaper.deleted_at.is_(None), LibraryPaper.status == "active")
                if query_text:
                    statement = statement.where(LibraryPaper.title.ilike("%" + query_text + "%"))
                rows = list(session.scalars(statement.order_by(LibraryPaper.updated_at.desc()).limit(30)))
                return {"scope": "account_library_not_project_selection", "limit": 30,
                        "papers": [{"paper_id": row.paper_id, "title": row.title} for row in rows]}
        if name == "paper_metadata" and set(args) == {"paper_id"}:
            self._owned_project(principal, project_id)
            record = self.library.get(principal, args["paper_id"])
            return {"paper_id": record.paper_id, "title": record.title, "metadata": record.metadata,
                    "metadata_artifact_id": record.artifact_ids.get("metadata", ""), "scope": "account_library_not_project_selection"}
        if name == "read_paper" and set(args) <= {"paper_id", "offset", "limit"}:
            self._owned_project(principal, project_id)
            paper_id = str(args.get("paper_id") or "")
            # LibraryService applies the current-user ownership check.
            metadata = self.library.get(principal, paper_id).metadata
            offset, limit = int(args.get("offset", 0)), int(args.get("limit", 12000))
            if offset < 0 or not 1 <= limit <= 20000:
                raise WorkflowValidationError("Invalid read range.")
            path = self.library.file(principal, paper_id, "markdown")
            if path.stat().st_size > 8 * 1024 * 1024:
                raise WorkflowValidationError("This paper is too large for conversation preview.")
            markdown = path.read_text(encoding="utf-8", errors="replace")
            return {"paper_id": paper_id, "metadata": metadata, "text": markdown[offset:offset + limit],
                    "offset": offset, "total_chars": len(markdown),
                    "complete": offset == 0 and len(markdown) <= limit,
                    "next_offset": offset + limit if offset + limit < len(markdown) else None}
        raise WorkflowValidationError("This conversation tool or its parameters are not available.")
