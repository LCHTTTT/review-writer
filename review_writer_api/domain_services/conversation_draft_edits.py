"""Exact Draft targets, using the existing paragraph editor and revision worker."""
import re
from review_writer_api.errors import WorkflowConflict, WorkflowNotFound, WorkflowValidationError


def resolve_paragraph(drafts, principal, project_id, args):
    current = drafts.get(principal, project_id)
    if current.get("draft_artifact_id") != args["artifact_id"]:
        raise WorkflowConflict("初稿已更新，请读取当前正文后再修改；未覆盖新内容。")
    section = next((row for row in drafts.draft_dialogue_sections(principal, project_id)
                    if row["section_id"] == args["section_id"]), None)
    paragraph = next((row for row in (section or {}).get("paragraphs", [])
                      if row["paragraph_id"] == args["paragraph_id"]), None)
    if not paragraph:
        raise WorkflowNotFound("没有找到指定章节中的这一段；请先定位当前初稿段落。")
    return current, paragraph


def replacement_preview(drafts, principal, project_id, args):
    current, paragraph = resolve_paragraph(drafts, principal, project_id, args)
    old, new = args["old_text"], args["new_text"]
    if re.search(r"<!--\s*(?:paragraph_id|deleted_paragraph_id)\s*:", new):
        raise WorkflowValidationError("替换文字不能包含系统内部段落标记。")
    if not old or old == new or paragraph["text"].count(old) != 1:
        raise WorkflowValidationError("待替换文字必须在指定段落中唯一出现；请提供更完整的原句。")
    candidate = paragraph["text"].replace(old, new, 1)
    if not candidate.strip():
        raise WorkflowValidationError("此操作不能删除整段；请明确提出删除段落要求。")
    return current, paragraph, {"paragraph_id": paragraph["paragraph_id"],
        "original_text": paragraph["text"], "candidate_text": candidate, "status": "pending"}


def generated_candidates(drafts, jobs, principal, project_id, args):
    source = jobs.status(principal, args["source_job_id"])
    if source.project_id != project_id or source.status != "succeeded" or source.payload.get("section_id") != args["section_id"]:
        raise WorkflowValidationError("只允许保存本次指定目标已完成的修改。")
    entries = drafts.get(principal, project_id)["rewrite_candidates"]
    if source.job_type == "draft.rewrite" and source.payload.get("revision_mode") == "dialogue":
        request = source.payload["dialogue"]
        if not args.get("paragraph_id") or request.get("paragraph_id") != args["paragraph_id"]:
            raise WorkflowValidationError("生成任务与指定段落不匹配，未采用其他段落的候选。")
        candidates = [row for row in entries if row.get("candidate_id") == request.get("turn_id")
                      and row.get("paragraph_key") == request["paragraph_key"]]
    elif (source.job_type == "draft.optimize" and source.payload.get("revision_mode") == "dialogue_batch"
          and source.payload.get("action") == "revise" and not args.get("paragraph_id")):
        keys = {row["paragraph_key"] for row in source.payload.get("paragraphs", [])}
        candidates = [row for row in entries if row.get("batch_job_id") == source.id and row.get("paragraph_key") in keys]
    else:
        raise WorkflowValidationError("这不是指定范围的正文修改任务。")
    if not candidates:
        raise WorkflowConflict("本次未生成可采用的修改，原文仍保留。")
    return candidates


def readable_preview(candidate, *, status=None):
    """Bounded display only; never use a truncated preview to save the manuscript."""
    return {"paragraph_id": candidate.get("paragraph_id", ""), "candidate_id": candidate.get("candidate_id", ""),
        "status": status or candidate.get("status", "pending"),
        **{key: str(candidate.get(key) or "")[:20000] for key in ("original_text", "candidate_text")},
        "truncated": any(len(str(candidate.get(key) or "")) > 20000 for key in ("original_text", "candidate_text"))}
