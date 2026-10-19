"""Literal Blueprint field patches share candidate publication and adoption contracts."""
from copy import deepcopy
import re
from review_writer_core.workflow import artifacts as names
from review_writer_api.errors import WorkflowConflict, WorkflowNotFound, WorkflowValidationError
from review_writer_api.security import Permission

ALLOWED_FIELDS = {"title", "review_problem"}


def patch_blueprint_fields(planning, principal, project_id, *, section_id, fields, expected_artifact_id, save=True):
    principal.require(Permission.PROJECT_WRITE)
    planning._owned_project(principal, project_id)
    if not fields or set(fields) - ALLOWED_FIELDS or any(not isinstance(value, str) or not value.strip() or len(value) > 4000 for value in fields.values()):
        raise WorkflowValidationError("Specify bounded editable Blueprint text fields; scientific facts and source excerpts are immutable.")
    with planning._write_lock:
        blueprint, artifact = planning._read_json(principal, project_id, names.BLUEPRINT)
        if artifact.id != expected_artifact_id:
            raise WorkflowConflict("Blueprint changed; no new fields were overwritten.")
        candidate = deepcopy(blueprint)
        section = next((row for row in candidate.get("sections", []) if row.get("section_id") == section_id), None)
        if not section:
            raise WorkflowNotFound("Blueprint section not found.")
        before = {key: deepcopy(section.get(key)) for key in fields}
        fields = {key: value for key, value in fields.items() if value != before[key]}
        if not fields:
            state = planning.repository.get_stage_state(principal.user_id, project_id, "blueprint")
            return {"blueprint_artifact_id": artifact.id, "section_blueprint": blueprint, "changed_fields": [],
                    "section_id": section_id, "unchanged": True, "revision": state.revision if state else 0}
        if "review_problem" in fields:
            if save:
                raise WorkflowValidationError("Changing the review question requires renewed chapter planning; save a provisional candidate first, not an approved plan with old arguments.")
            candidate["academic_planning"] = {**(candidate.get("academic_planning") or {}), "status": "incomplete"}
            section["planning_status"] = "pending"
        if "title" in fields:
            if any(character in fields["title"] for character in "\r\n"):
                raise WorkflowValidationError("A section title must occupy one line.")
            lines = str(candidate.get("resolved_outline_md") or "").splitlines(keepends=True)
            marker = re.compile(r"<!--\s*section_id:\s*" + re.escape(section_id) + r"\s*-->")
            locations = [index for index, line in enumerate(lines) if marker.search(line)]
            if len(locations) != 1:
                raise WorkflowConflict("The saved outline has no unique section identity; no heading was guessed.")
            heading_index = next((index for index in range(locations[0] - 1, -1, -1) if re.match(r"^#{1,6}\s", lines[index])), None)
            old_title = str(section.get("title") or "")
            if heading_index is None or not old_title or not lines[heading_index].rstrip("\r\n").endswith(old_title):
                raise WorkflowConflict("The Blueprint and saved outline headings disagree; review them before editing.")
            line = lines[heading_index]
            ending = line[len(line.rstrip("\r\n")):]
            lines[heading_index] = line.rstrip("\r\n")[:-len(old_title)] + fields["title"] + ending
            candidate["resolved_outline_md"] = "".join(lines)
        section.update(fields)
        # Keep the scientific context, allocations, stable IDs, hierarchy and other fields byte-for-byte as data.
        candidate["human_field_edits"] = [*(candidate.get("human_field_edits") or []),
            {"section_id": section_id, "fields": dict(fields), "previous": before, "source": "explicit_field_patch"}]
        matrix_state = planning.repository.get_stage_state(principal.user_id, project_id, "matrix")
        blueprint_state = planning.repository.get_stage_state(principal.user_id, project_id, "blueprint")
        prepared = {"section_blueprint": candidate, "base_blueprint_artifact_id": artifact.id,
                    "blueprint_revision": blueprint_state.revision if blueprint_state else 0,
                    "matrix_revision": matrix_state.revision if matrix_state else 0}
        result = planning.publish_blueprint_candidate(principal, project_id, prepared)
        if save:
            result.update(planning.confirm_blueprint(principal, project_id, revision=prepared["blueprint_revision"], artifact_id=result["blueprint_artifact_id"]))
        result.update(changed_fields=list(fields), section_id=section_id, previous_fields=before, saved_fields=dict(fields))
        return result
