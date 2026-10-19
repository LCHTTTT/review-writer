"""Shared conservative upstream-to-Draft synchronization; no model or reassembly writes."""
import json
from review_writer_core.manuscript_sync import merge_prose
from review_writer_core.workflow import artifacts as names
from review_writer_api.errors import WorkflowConflict, WorkflowNotFound
from review_writer_api.security import Permission


class DraftSynchronization:
    def __init__(self, drafts):
        self.drafts = drafts

    def preview(self, principal, project_id, section_id):
        d = self.drafts
        project = d._owned_project(principal, project_id)
        current_text, current = d._read_text(principal, project_id, names.DRAFT_MANUSCRIPT)
        current_index, index = d._read_json(principal, project_id, names.SECTION_DRAFTS)
        baseline_id = (current.metadata.get("section_sync_sources") or {}).get(section_id) or current.metadata.get("source_sections_artifact_id")
        if not baseline_id:
            raise WorkflowConflict("This Draft has no assembly baseline; no automatic merge was attempted.")
        baseline_draft_id = (current.metadata.get("section_sync_baselines") or {}).get(section_id)
        initial = next((item for item in d.repository.list_artifacts(principal.user_id, project_id, names.DRAFT_INITIAL_MANUSCRIPT)
                        if (item.id == baseline_draft_id if baseline_draft_id else item.metadata.get("source_sections_artifact_id") == baseline_id)), None)
        if not initial:
            raise WorkflowConflict("Saved assembly baseline unavailable; preserve manual edits and compare versions.")
        baseline = d.artifacts.resolve_owned_artifact(principal.user_id, initial.id).path.read_text(encoding="utf-8")
        source = d.artifacts.resolve_owned_artifact(principal.user_id, baseline_id)
        if source.artifact.project_id != project_id or source.artifact.logical_name != names.SECTION_DRAFTS:
            raise WorkflowNotFound("Section baseline not found.")
        old_index = json.loads(source.path.read_text(encoding="utf-8"))
        before_section = next((row for row in old_index.get("sections", []) if row.get("section_id") == section_id), None)
        after_section = next((row for row in current_index.get("sections", []) if row.get("section_id") == section_id), None)
        if not before_section or not after_section:
            raise WorkflowConflict("Section identity changed; compare structural changes before synchronizing.")
        if any(before_section.get(key) != after_section.get(key) for key in ("heading", "title", "parent_section_id", "level")):
            raise WorkflowConflict("Section heading or hierarchy changed; this prose-only merge cannot silently omit structural changes.")
        matrix, matrix_artifact = d._read_json(principal, project_id, names.MATRIX)
        if not d._matrix_dependency_matches(principal, project_id, current.metadata.get("source_matrix_artifact_id"), matrix_artifact):
            raise WorkflowConflict("Scientific inputs changed; this prose-only merge cannot replace evidence review.")
        manifest_id = current.metadata.get("source_figure_manifest_artifact_id")
        manifest_source = d.artifacts.resolve_owned_artifact(principal.user_id, manifest_id)
        if manifest_source.artifact.project_id != project_id or manifest_source.artifact.logical_name != names.FIGURE_MANIFEST:
            raise WorkflowNotFound("Assembly figure baseline unavailable.")
        manifest = json.loads(manifest_source.path.read_text(encoding="utf-8"))
        evidence, evidence_artifact = d._read_json(principal, project_id, names.SECTION_EVIDENCE_PACKAGE, required=False)
        generated = d._assemble_markdown(str(project.topic or project.slug), current_index, manifest, matrix, evidence,
                                        reaction_quickref_tables=d.reaction_quickref_tables)
        # Whole-document citation identity/order must stay stable before local prose can be copied.
        def citation_map(text):
            import re
            return re.findall(r"<!--\s*citation_map:.*?-->", text, flags=re.S)
        if citation_map(baseline) != citation_map(generated) or citation_map(baseline) != citation_map(current_text):
            raise WorkflowConflict("Citation numbering changed; no local prose was copied with mismatched references.")
        def unique_spans(text):
            spans = d._paragraph_spans(text)
            result = {row["paragraph_id"]: row for row in spans}
            if len(result) != len(spans):
                raise WorkflowConflict("Duplicate paragraph identities; no ambiguous text was merged.")
            return result
        old, new, local = (unique_spans(text) for text in (baseline, generated, current_text))
        identifiers = {str(row.get("paragraph_id")) for row in before_section.get("paragraphs", [])}
        after_ids = {str(row.get("paragraph_id")) for row in after_section.get("paragraphs", [])}
        if identifiers != after_ids or not identifiers or not identifiers <= old.keys() or not identifiers <= new.keys() or not identifiers <= local.keys():
            raise WorkflowConflict("Paragraph structure or manual deletion changed; no automatic structural merge was attempted.")
        changes, conflicts = [], []
        replacements = []
        for identifier in sorted(identifiers):
            value = merge_prose(old[identifier]["text"], new[identifier]["text"], local[identifier]["text"])
            if value["conflicts"]:
                conflicts.append({"paragraph_id": identifier, **value})
            if value["text"] != local[identifier]["text"]:
                changes.append({"paragraph_id": identifier, "original_text": local[identifier]["text"], "candidate_text": value["text"]})
                replacements.append((local[identifier]["start"], local[identifier]["end"], value["text"]))
        candidate = current_text
        for start, end, value in sorted(replacements, reverse=True):
            candidate = candidate[:start] + value + candidate[end:]
        return {"section_id": section_id, "draft_artifact_id": current.id, "sections_artifact_id": index.id,
                "baseline_sections_artifact_id": baseline_id, "revision": d._revision(principal, project_id),
                "changes": changes, "conflicts": conflicts, "candidate_text": candidate, "upstream_baseline_text": generated,
                "expected_artifacts": {names.DRAFT_MANUSCRIPT: current.id, names.SECTION_DRAFTS: index.id,
                                       names.MATRIX: matrix_artifact.id, names.SECTION_EVIDENCE_PACKAGE: evidence_artifact.id if evidence_artifact else None}}

    def save(self, principal, project_id, section_id, *, expected_draft_artifact_id, expected_sections_artifact_id):
        principal.require(Permission.PROJECT_WRITE)
        value = self.preview(principal, project_id, section_id)
        if value["draft_artifact_id"] != expected_draft_artifact_id or value["sections_artifact_id"] != expected_sections_artifact_id:
            raise WorkflowConflict("Versions changed; the synchronization preview was not saved.")
        if value["conflicts"]:
            raise WorkflowConflict("Manual edits overlap upstream changes; both versions were preserved.", details={"conflicts": value["conflicts"]})
        if not value["changes"] and value["baseline_sections_artifact_id"] == value["sections_artifact_id"]:
            return {"draft_artifact_id": value["draft_artifact_id"], "revision": value["revision"],
                    "section_id": section_id, "changed_paragraph_ids": [], "scientific_bindings_updated": False,
                    "unchanged": True}
        d = self.drafts
        _, artifact = d._read_text(principal, project_id, names.DRAFT_MANUSCRIPT)
        metadata = dict(artifact.metadata)
        sources = dict(metadata.get("section_sync_sources") or {})
        sources[section_id] = value["sections_artifact_id"]
        metadata.update(section_sync_sources=sources, previous_draft_artifact_id=artifact.id, operation="upstream-section-sync")
        # Keep global scientific-source bindings conservative until every changed section is resolved.
        metadata["unverified_manual_paragraph_ids"] = sorted(set(metadata.get("unverified_manual_paragraph_ids", [])) |
            {row["paragraph_id"] for row in value["changes"]})
        with d._write_lock:
            published, state = d._publish_files(principal, project_id,
                {names.DRAFT_INITIAL_MANUSCRIPT: (value["upstream_baseline_text"].encode(), "markdown"),
                 names.DRAFT_MANUSCRIPT: (value["candidate_text"].encode(), "markdown")},
                expected_revision=value["revision"], metadata=metadata,
                metadata_builder=lambda logical, records: ({**metadata, "source_sections_artifact_id": value["sections_artifact_id"]}
                    if logical == names.DRAFT_INITIAL_MANUSCRIPT else {**metadata, "section_sync_baselines": {
                        **(metadata.get("section_sync_baselines") or {}), section_id: records[names.DRAFT_INITIAL_MANUSCRIPT].id}}),
                expected_current_artifacts=value["expected_artifacts"])
        return {"draft_artifact_id": published[names.DRAFT_MANUSCRIPT].id, "revision": state.revision,
                "section_id": section_id, "changed_paragraph_ids": [row["paragraph_id"] for row in value["changes"]],
                "scientific_bindings_updated": False, "note": "Only authorized prose changed; other chapters and manual manuscript fields were preserved."}
