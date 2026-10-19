"""Structured conversation intent -> executable stage plan.

The model states a goal and a high-level operation; this module reads the
project overview and builds the actual stage sequence. The model never has to
guess the next step or enumerate low-level tool calls.

Intents resolve against owned, versioned stage results. Missing outlines use
the original recommendation worker, never outline text fabricated in chat.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from review_writer_api.errors import WorkflowStageNotReady, WorkflowValidationError


class ConversationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    goal: Literal["discovery", "planning", "sections", "draft", "final"] = "final"
    operation: Literal["generate_review", "select_recommended"] = "generate_review"
    request_quote: str = Field(min_length=2, max_length=12000)
    target_artifact_id: str = Field(default="", max_length=36)
    section_id: str = Field(default="", max_length=96)
    auto_adopt: bool = False


def intent_context(service, principal, project_id):
    """Bounded selection context, not raw screening JSON or another ranker."""
    from .project_papers import recommended_paper_ids
    try:
        review = service.actions.services.discovery_service.get(principal, project_id)
    except WorkflowStageNotReady:
        return {"recommendations": {"available": False, "count": 0}}
    ids = recommended_paper_ids(review)
    titles = {str(row.get("paper_id")): str(row.get("title") or row.get("paper_id"))[:240]
              for group in review.get("results", []) for row in group.get("local_results", [])}
    return {"recommendations": {"available": bool(ids), "count": len(ids),
            "artifact_id": review.get("artifact_id", ""),
            "items": [{"paper_id": key, "title": titles.get(key, key)} for key in ids[:40]],
            "scope": "current_saved_local_recommendations", "complete": len(ids) <= 40},
            "selected_paper_count": len(review.get("selected_paper_ids", [])),
            "rules": "Interpret current message with recent dialogue. 'All/these' must have one clear object. select_recommended never means the entire account library or web candidates. Ask if the referent or authorization is unclear. Earlier messages inform the object, not new permission."}


def resolve_intent(intent, service, principal, project_id, overview):
    """One state-aware router for both shortcuts and semantic model tools."""
    from .conversation_authorization import validate_request
    import re
    validate_request(intent.request_quote, intent.request_quote, semantic=True)
    if intent.auto_adopt and re.search(r"不采用|不要采用|不确认|不要确认|不批准|不保存|do not (?:adopt|accept|confirm|approve|save)|don't (?:adopt|accept|confirm|approve|save)", intent.request_quote, re.I):
        raise WorkflowValidationError("本轮保留了采用决定，没有自动批准后续成果。")
    if intent.section_id:
        raise WorkflowValidationError("整篇推进不能代替指定章节的修改，请使用章节修改工具。")
    if intent.target_artifact_id and intent.target_artifact_id not in {row["id"] for row in overview.get("artifacts", [])}:
        raise WorkflowValidationError("讨论对象不是当前项目的有效成果，未覆盖历史版本或其他项目。")
    from .conversation_continuation import continuation_entry
    if intent.operation == "generate_review":
        return continuation_entry(intent.request_quote, service, principal, project_id,
                                  overview, semantic=True, goal=intent.goal, auto_adopt=intent.auto_adopt)
    from .project_papers import recommended_paper_ids
    from .conversation_entry import recommendation_steps
    review = service.actions.services.discovery_service.get(principal, project_id)
    ids = recommended_paper_ids(review)
    steps = recommendation_steps(ids)
    if intent.goal == "discovery":
        steps = steps[:-1]  # selection-only means no confirmation or analysis
    if intent.goal in {"sections", "draft", "final"} and not intent.auto_adopt:
        raise WorkflowValidationError("是否按推荐方案继续到指定阶段？未自动采用后续成果。")
    label = {"planning": "大纲", "sections": "正文", "draft": "初稿", "final": "终稿"}.get(intent.goal, "筛选")
    return {"request": {"steps": steps, "request_quote": intent.request_quote,
            "stop_at": "planning" if intent.goal == "discovery" else intent.goal, "auto_adopt": intent.auto_adopt},
            "reply": "已提交纳入当前推荐文献" + (f"；分析完成后将按推荐方案继续到{label}。" if intent.auto_adopt and intent.goal != "discovery" else "，任务卡将显示实际处理进度。")}


def goal_reached(intent, service, principal, project_id, overview):
    """Existence is not freshness; finish only on a real usable stage result."""
    from review_writer_core.workflow import artifacts as names
    artifacts = {row["logical_name"] for row in overview["artifacts"]}
    services = service.actions.services
    if intent.goal == "final":
        return names.FINAL_MANUSCRIPT in artifacts and services.final_service.get(principal, project_id).get("final_current", False)
    if intent.goal == "draft":
        return names.DRAFT_MANUSCRIPT in artifacts and not services.drafts_service.get(principal, project_id)["freshness"]["upstream_stale"]
    if intent.goal == "sections":
        if names.SECTION_DRAFTS not in artifacts:
            return False
        sections = services.sections_service.get(principal, project_id)
        return bool(sections["handoff"]["current"] and not sections["report"].get("pending_section_count"))
    if intent.goal == "planning":
        return names.PLANNING_OUTLINE in artifacts and services.planning_service.get(principal, project_id).get("outline_ready_for_chapter_planning", False)
    return False

