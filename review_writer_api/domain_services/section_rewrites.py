"""Scoped upstream prose revision through the established source writer and publisher.

Generation publishes only an immutable candidate. Adoption revalidates the full
academic bundle and exact saved inputs; no Draft edit or automatic adoption.
"""
from copy import deepcopy
import re
import json
import uuid
from review_writer_api.errors import WorkflowConflict, WorkflowNotFound, WorkflowValidationError
from review_writer_api.security import Permission
from review_writer_core.workflow import artifacts as names

PREFIX = "sections/rewrite-candidates/"
BUNDLE = (names.SECTION_DRAFTS, names.SECTION_SYNTHESIS_STATE, names.SECTION_WRITING_PLAN,
          names.SECTION_PAPER_FIGURE_CANDIDATES, names.SECTION_FIGURE_CANDIDATES, names.SECTION_DEFAULT_FIGURE_REVIEWS)

class SectionRewrites:
    def __init__(self, sections):
        self.sections = sections

    def _guard(self, principal, project_id, expected):
        self.sections._owned_project(principal, project_id)
        for name, identity in expected.items():
            current = self.sections.repository.get_current_artifact(principal.user_id, project_id, name)
            if (current.id if current else None) != identity:
                raise WorkflowConflict("Upstream inputs changed; the scoped rewrite did not overwrite newer work.")

    def prepare(self, principal, project_id, section_id, message, expected_sections_artifact_id):
        principal.require(Permission.PROJECT_WRITE)
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise WorkflowValidationError("Specify a bounded chapter revision request.")
        from .conversation_authorization import validate_supported_limits
        validate_supported_limits(message)
        if re.search(r"不联网|不要联网|no web|offline|local only", message, re.I):
            raise WorkflowValidationError("Scoped revision cannot yet guarantee a no-network evidence supplement; no job was submitted.")
        if re.search(r"这一段|这段|仅.*段|只.*段|(?:only|just|this|that)\s+(?:this\s+|that\s+|one\s+)?paragraph|paragraph\s+\d|增删章节|合并章节|删除章节", message, re.I):
            raise WorkflowValidationError("This operation revises one whole existing chapter; paragraph-only or structural requests must not be expanded silently.")
        s = self.sections
        index, artifact = s._read_json_artifact(principal, project_id, names.SECTION_DRAFTS)
        if artifact.id != expected_sections_artifact_id:
            raise WorkflowConflict("Read the current upstream chapter version before rewriting.")
        original = next((row for row in index.get("sections", []) if row.get("section_id") == section_id), None)
        if not original or not original.get("paragraphs"):
            raise WorkflowNotFound("Saved upstream chapter not found.")
        full = s.generation_payload(principal, project_id, defer_evidence=True)
        source_names = ("source_blueprint_artifact_id", "source_matrix_artifact_id", "source_outline_artifact_id")
        if any(index.get(key) != full.get(key) for key in source_names):
            raise WorkflowConflict("The saved chapter is based on older planning inputs; renew upstream planning first.")
        task = next((row for row in full["tasks"] if row.get("section_id") == section_id), None)
        if not task:
            raise WorkflowNotFound("Chapter is not in the approved writing plan.")
        base, expected = {}, {}
        for name in BUNDLE:
            value, record = s._read_json_artifact(principal, project_id, name, required=name in BUNDLE[:3])
            base[name] = value if record else None
            expected[name] = record.id if record else None
        for name in (names.BLUEPRINT, names.MATRIX, names.PLANNING_OUTLINE, names.SECTION_EVIDENCE_PACKAGE):
            record = s.repository.get_current_artifact(principal.user_id, project_id, name)
            expected[name] = record.id if record else None
        self._guard(principal, project_id, expected)
        return {**full, "tasks": [{**deepcopy(task), "revision_request": message, "revision_original_paragraphs": deepcopy(original["paragraphs"])}],
                "evidence_package": {"sections": []},
                "_rewrite_scope": section_id, "_rewrite_expected_artifacts": expected,
                "_rewrite_full_payload": full, "_rewrite_base_bundle": base}

    def hydrate(self, principal, project_id, queued, prepared):
        self._guard(principal, project_id, queued["_rewrite_expected_artifacts"])
        sid = queued["_rewrite_scope"]
        task = next((row for row in prepared["tasks"] if row.get("section_id") == sid), None)
        if not task:
            raise WorkflowConflict("The requested chapter disappeared during preparation.")
        request = queued["tasks"][0]
        return {**queued, **prepared, "_rewrite_full_payload": deepcopy(prepared),
                "tasks": [{**deepcopy(task), "revision_request": request["revision_request"],
                           "revision_original_paragraphs": request["revision_original_paragraphs"]}],
                "evidence_package": {**prepared["evidence_package"], "sections": [row for row in prepared["evidence_package"].get("sections", []) if row.get("section_id") == sid]},
                "expected_sections_revision": queued["expected_sections_revision"], "evidence_preparation_pending": False}

    def publish_candidate(self, principal, project_id, payload, built):
        s = self.sections
        self._guard(principal, project_id, payload["_rewrite_expected_artifacts"])
        s.validate_generation_inputs(principal, project_id, payload)
        sid = payload["_rewrite_scope"]
        if [row.get("section_id") for row in built.get("sections", [])] != [sid]:
            raise WorkflowValidationError("Scoped generation returned an unexpected chapter set.")
        s.publish_generation(principal, project_id, payload, built, attempts=1, validate_only=True)
        run = s.repository.create_stage_run(principal.user_id, project_id, "sections", status="succeeded",
            input_snapshot={"operation": "section-rewrite-candidate", "section_id": sid})
        directory = s.artifacts.stage_run_directory(principal.user_id, project_id, run.id)
        filename = "rewrite.json"
        value = {"section_id": sid, "payload": payload, "built": {key: value for key, value in built.items() if not key.startswith("_")}}
        (directory / filename).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        artifact = s.artifacts.publish(principal.user_id, project_id, run.id, filename,
            logical_name=PREFIX + str(uuid.uuid4()) + ".json", artifact_type="json", producer_stage="sections", make_current=False,
            metadata={"operation": "section-rewrite-candidate", "section_id": sid})
        return {"artifact_id": artifact.id, "candidate_artifact_id": artifact.id, "section_id": sid, "candidate_pending": True,
                "current_sections_unchanged": True, "base_sections_artifact_id": payload["_rewrite_expected_artifacts"][names.SECTION_DRAFTS],
                "original_section": next(row for row in payload["_rewrite_base_bundle"][names.SECTION_DRAFTS]["sections"] if row.get("section_id") == sid),
                "section": built["sections"][0]}

    def accept(self, principal, project_id, candidate_artifact_id):
        principal.require(Permission.PROJECT_WRITE)
        s = self.sections
        s._owned_project(principal, project_id)
        resolved = s.artifacts.resolve_owned_artifact(principal.user_id, candidate_artifact_id)
        if resolved.artifact.project_id != project_id or not resolved.artifact.logical_name.startswith(PREFIX):
            raise WorkflowNotFound("Owned upstream rewrite candidate not found.")
        value = json.loads(resolved.path.read_text(encoding="utf-8"))
        payload, built, sid = value["payload"], value["built"], value["section_id"]
        with s._write_lock:
            current = s.repository.get_current_artifact(principal.user_id, project_id, names.SECTION_DRAFTS)
            if current and current.metadata.get("adopted_rewrite_candidate_artifact_id") == candidate_artifact_id:
                return {"section_index_artifact_id": current.id, "adopted_candidate_artifact_id": candidate_artifact_id,
                        "rewritten_section_ids": [sid], "draft_unchanged": True, "already_adopted": True}
            self._guard(principal, project_id, payload["_rewrite_expected_artifacts"])
            full = deepcopy(payload["_rewrite_full_payload"])
            base = payload["_rewrite_base_bundle"]
            merged = deepcopy(built)
            for key, logical in (("sections", names.SECTION_DRAFTS), ("synthesis_state", names.SECTION_SYNTHESIS_STATE), ("writing_plan", names.SECTION_WRITING_PLAN)):
                old = base[logical]
                old_rows = old.get("sections", [])
                incoming = built[key] if key == "sections" else built[key].get("sections", [])
                if len(incoming) != 1 or incoming[0].get("section_id") != sid:
                    raise WorkflowValidationError("Rewrite academic bundle must describe exactly the requested chapter.")
                rows = [deepcopy(incoming[0]) if row.get("section_id") == sid else deepcopy(row) for row in old_rows]
                merged[key] = rows if key == "sections" else {**deepcopy(old), "sections": rows}
            # Prose revision does not authorize changing the saved figure inventory or selections.
            for key, logical in (("paper_figure_candidates", names.SECTION_PAPER_FIGURE_CANDIDATES),
                                 ("figure_candidates", names.SECTION_FIGURE_CANDIDATES),
                                 ("default_figure_reviews", names.SECTION_DEFAULT_FIGURE_REVIEWS)):
                if base[logical] is None:
                    merged.pop(key, None)
                else:
                    merged[key] = deepcopy(base[logical])
            full["tasks"] = [deepcopy(payload["tasks"][0]) if row.get("section_id") == sid else row for row in full["tasks"]]
            incoming_evidence = payload["evidence_package"].get("sections", [])
            if len(incoming_evidence) != 1 or incoming_evidence[0].get("section_id") != sid:
                raise WorkflowValidationError("Rewrite evidence must describe exactly the requested chapter.")
            full["evidence_package"]["sections"] = [deepcopy(incoming_evidence[0]) if row.get("section_id") == sid else row for row in full["evidence_package"].get("sections", [])]
            full["_rewrite_expected_artifacts"] = payload["_rewrite_expected_artifacts"]
            full["_rewrite_scope"] = sid
            full["_adopted_rewrite_candidate_id"] = candidate_artifact_id
            result = s.publish_generation(principal, project_id, full, merged, attempts=1)
            result.update(adopted_candidate_artifact_id=candidate_artifact_id, rewritten_section_ids=[sid],
                          draft_unchanged=True, note="Only the upstream chapter was adopted. Review Draft synchronization separately.")
            return result
