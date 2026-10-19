"""Deterministic, read-only presentation of saved JSON; no model or scientific inference."""
from __future__ import annotations

import json
import re

_LABELS = {
    "sections": ("章节", "Chapters"), "paragraphs": ("段落", "Paragraphs"),
    "papers": ("文献", "Papers"), "items": ("条目", "Items"), "tasks": ("写作任务", "Writing tasks"),
    "title": ("标题", "Title"), "heading": ("章节标题", "Chapter title"), "topic": ("主题", "Topic"),
    "review_problem": ("研究问题", "Review question"), "core_argument": ("核心论点", "Core argument"),
    "purpose": ("目的", "Purpose"), "summary": ("概要", "Summary"), "text": ("正文", "Text"),
    "claim": ("论述", "Claim"), "proposition": ("科学命题", "Proposition"), "claims": ("论述", "Claims"),
    "scientific_claims": ("科学命题", "Scientific claims"), "evidence": ("证据", "Evidence"),
    "evidence_refs": ("证据来源", "Evidence sources"), "hits": ("检索证据", "Retrieved evidence"),
    "content": ("内容", "Content"), "status": ("状态", "Status"), "reason": ("原因", "Reason"),
    "issues": ("问题", "Issues"), "decision": ("审阅结论", "Review decision"),
    "authors": ("作者", "Authors"), "year": ("年份", "Year"), "doi": ("DOI", "DOI"),
    "journal": ("期刊", "Journal"), "abstract": ("摘要", "Abstract"), "keywords": ("关键词", "Keywords"),
    "section_id": ("章节编号", "Chapter reference"), "paragraph_id": ("段落编号", "Paragraph reference"),
    "paper_id": ("文献编号", "Paper reference"), "paper_title": ("文献标题", "Paper title"),
    "primary_papers": ("主要文献", "Primary papers"), "supporting_papers": ("支持文献", "Supporting papers"),
    "context_papers": ("背景文献", "Context papers"), "allowed_papers": ("可用文献", "Available papers"),
    "cited_paper_ids": ("引用文献", "Cited papers"), "fact_ids": ("事实编号", "Fact references"),
    "must_cover_points": ("须覆盖要点", "Required points"), "avoid_points": ("写作边界", "Writing boundaries"),
    "writing_requirements": ("写作要求", "Writing requirements"), "instruction": ("要求", "Instruction"),
    "components": ("综合要点", "Synthesis components"), "objective": ("目标", "Objective"),
    "theme": ("主题", "Theme"), "reader_takeaway": ("读者结论", "Reader takeaway"),
    "positive_synthesis": ("综合论述", "Synthesis"), "support_status": ("证据支持状态", "Evidence support"),
    "evidence_ceiling": ("证据支持范围", "Evidence ceiling"), "limitations": ("局限", "Limitations"),
    "route": ("写作路径", "Writing route"), "overview_intent": ("章节写作意图", "Chapter intent"),
    "original_section": ("原章节", "Original chapter"), "section": ("改写候选", "Revision candidate"),
    "synthesis_state": ("综合规划", "Synthesis plan"), "writing_plan": ("写作规划", "Writing plan"),
    "academic_planning": ("学术规划", "Academic planning"), "planning_status": ("规划状态", "Planning status"),
    "section_role": ("章节用途", "Chapter role"), "argument_role": ("论证用途", "Argument role"),
    "confidence": ("置信度", "Confidence"), "checked": ("已人工核对", "Manually checked"),
    "source_quote": ("来源原文", "Source quotation"), "source": ("来源", "Source"),
    "page_start": ("起始页", "Start page"), "page_end": ("结束页", "End page"),
    "figures": ("图片", "Figures"), "caption": ("图注", "Caption"), "figure_need": ("图像要求", "Figure requirements"),
    "rows": ("文献条目", "Paper entries"), "scientific_facts": ("科学事实", "Scientific facts"),
    "research_question": ("研究问题", "Research question"), "main_contribution": ("主要贡献", "Main contribution"),
    "review_relevance": ("与综述的关系", "Review relevance"), "findings": ("研究发现", "Findings"),
    "field_id": ("事实类别", "Fact category"), "value": ("内容", "Content"),
    "support_excerpt": ("来源片段", "Source excerpt"), "support_level": ("证据支持程度", "Support level"),
    "source_verification": ("来源核验", "Source verification"), "classification": ("分类", "Classification"),
}
_VALUES = {
    "complete": "已完成", "completed": "已完成", "incomplete": "未完成", "pending": "待处理",
    "supported": "有证据支持", "unsupported": "缺少证据支持", "succeeded": "成功", "failed": "失败",
    "pending_evidence": "等待补充证据", "body": "正文章节", "introduction": "引言", "conclusion": "结论",
    "human_review": "人工核对", "approved": "已批准", "unresolved": "尚未解决",
}
_BODY = ("draft_md", "section_drafts_md", "manuscript_md", "first_draft_md", "outline_md", "report_md")
_INTERNAL = {"schema_version", "version", "project_id", "user_id", "metadata", "content_records", "sha256",
             "operation_id", "operation_key", "idempotency_key", "model_snapshot", "run_output_snapshot",
             "section_sync_baselines", "section_sync_sources", "artifact_id", "candidate_artifact_id"}


def readable_artifact(value: str, *, language: str = "zh") -> str:
    data = json.loads(value)  # A malformed saved artifact is an error, not an empty result or raw-JSON fallback.
    english = language == "en"
    nodes = 0

    def tr(zh, en):
        return en if english else zh

    def label(key):
        pair = _LABELS.get(key)
        return pair[1 if english else 0] if pair else key.replace("_", " ").removesuffix(" md").capitalize()

    def visible(key):
        return not (key.startswith("_") or key in _INTERNAL or key.endswith(("artifact_id", "artifact_ids", "fingerprint", "sha256")))

    def scalar(item):
        if item is None or item == "":
            return tr("未填写", "Not provided")
        if isinstance(item, bool):
            return tr("是", "Yes") if item else tr("否", "No")
        return _VALUES.get(item, item) if isinstance(item, str) and not english else str(item)

    def heading(title, depth):
        return "#" * min(depth, 6) + " " + re.sub(r"[\r\n]+", " ", str(title)) + "\n\n"

    def render(item, depth=2):
        nonlocal nodes
        nodes += 1
        if depth > 12 or nodes > 10000:
            return tr("此处内容超出预览范围，请查看原阶段完整成果。", "This content exceeds the preview bounds; see the complete result in its original stage.") + "\n\n"
        if isinstance(item, dict):
            # Published prose is the document, not the accompanying machine bookkeeping.
            for key in _BODY:
                if isinstance(item.get(key), str) and item[key].strip():
                    return item[key].strip() + "\n\n"
            fields = [(key, child) for key, child in item.items() if visible(key)]
            if not fields:
                return tr("暂无可阅读内容。", "No readable content yet.") + "\n\n"
            output = []
            for key, child in fields:
                title = label(key)
                if isinstance(child, (dict, list)):
                    output.append(heading(title, depth) + render(child, depth + 1))
                else:
                    output.append(f"**{title}**\n\n{scalar(child)}\n\n")
            return "".join(output)
        if isinstance(item, list):
            if not item:
                return tr("暂无条目。", "No entries yet.") + "\n\n"
            if all(not isinstance(child, (dict, list)) for child in item):
                return "\n".join("- " + scalar(child).replace("\n", "\n  ") for child in item) + "\n\n"
            output = []
            for index, child in enumerate(item):
                if nodes > 10000:
                    output.append(tr("其余条目超出预览范围，请查看原阶段完整成果。", "Remaining entries exceed the preview bounds; see the complete result in its original stage.") + "\n\n")
                    break
                title = (child.get("heading") or child.get("title") or child.get("paper_title") or child.get("section_id")) if isinstance(child, dict) else None
                output.append(heading(title or tr(f"条目 {index + 1}", f"Item {index + 1}"), depth) + render(child, depth + 1))
            return "".join(output)
        return scalar(item) + "\n\n"

    return render(data).strip() + "\n"
