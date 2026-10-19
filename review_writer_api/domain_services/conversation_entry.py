"""Fast handoff for unambiguous commands, not scientific screening rules.

The existing Discovery worker owns query planning, retrieval and relevance.
Ambiguous, attachment-based and existing-manuscript requests stay in dialogue.
"""
import re

from review_writer_api.domain_services.conversation_authorization import command_text, validate_request, web_search_requested, recommended_selection_requested
from review_writer_api.errors import WorkflowStageNotReady, WorkflowValidationError


def discovery_entry(message, project, *, object_id="", attachment_ids=(), web_search=None):
    if object_id or attachment_ids:
        return None
    try:
        text = validate_request(message, message)
    except WorkflowValidationError:
        return None
    # Explicit search commands are a UI shortcut, not a relevance classifier.
    search = re.match(r"^(?:重新\s*)?(?:(?:联网|在线)\s*)?(?:检索|搜索|search\b|find\s+(?:papers|articles|literature)\b)", command_text(text), re.I)
    review = re.match(r"^(?:写|撰写|生成|write\b|generate\b)", text, re.I) and re.search(r"综述|\breview\b", text, re.I)
    if not search and not review:
        return None
    if re.search(r"先思考|不要执行|仅讨论|只讨论|不要检索|不检索|(?:do not|don't) search(?!\s+(?:online|(?:the\s+)?web|internet))|discussion only", message, re.I):
        return None
    if search and re.search(r"按钮|日志|对话记录|正文|章节|稿件|manuscript|chapter|chat history|logs?\b|\b(?:how|why|where|should)\b", text, re.I):
        return None
    if review:
        # Never reinterpret an existing project as a new review or rerun search
        # when the user has already selected a corpus/started the workflow.
        if project["artifacts"]:
            return None
        if re.search(r"摘要|结论|改写|修改|重写|abstract|conclusion|rewrite|revise", text, re.I):
            return None
    if any(job.get("job_type") in {"discovery.search", "conversation.plan", "conversation.action"}
           for job in project.get("jobs", [])):
        return None
    topic = message.strip()
    if re.fullmatch(r"(?:生成|写|撰写)(?:一篇|这个|当前)?综述[。.!！]?|(?:write|generate)(?:\s+a)?\s+review[.!]?", text, re.I):
        topic = project["topic"]
    elif search:
        topic = text[search.end():].strip(" ：:，,") or project["topic"]
    if not 3 <= len(topic) <= 10000:
        return None
    return {"steps": [{"action": "discovery.search", "arguments": {"topic": topic, "keywords": "", "web_search": web_search_requested(message, web_search)}}],
            "request_quote": message, "auto_adopt": True, "stop_at": "planning"}


def recommendation_entry(message, discovery, principal, project_id, *, object_id="", attachment_ids=()):
    if object_id or attachment_ids:
        return None
    if not recommended_selection_requested(message):
        return None
    validate_request(message, message)
    from .project_papers import recommended_paper_ids
    try:
        review = discovery.get(principal, project_id)
        ids = recommended_paper_ids(review)
    except WorkflowStageNotReady:
        review = None
        ids = []
    if not ids:
        # No search-recommended papers: select the whole Library directly.
        return {"steps": [{"action": "discovery.select_library", "arguments": {}},
                          {"action": "discovery.confirm", "arguments": {}}],
                "request_quote": message, "auto_adopt": False, "stop_at": "planning"}
    steps = recommendation_steps(ids)
    return {"steps": steps, "request_quote": message, "auto_adopt": False, "stop_at": "planning"}


def recommendation_steps(ids):
    if not ids:
        raise WorkflowValidationError("当前没有可采用的本地推荐文献，请先检索或选择已有文献。")
    # Original selection endpoint saves up to 200 changes. Preserve other
    # choices and its limits; each step gets the previous step's true version.
    steps = [{"action": "discovery.selection", "arguments": {"candidate_ids": ids[start:start + 200], "selected": True}}
             for start in range(0, len(ids), 200)]
    steps.append({"action": "discovery.confirm", "arguments": {}})
    return steps
