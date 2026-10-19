"""State-based routing of explicit completion commands to existing operations.

No scientific decisions, model calls, writes, or invented result identifiers.
"""
import re

from review_writer_api.domain_services.conversation_authorization import command_text, validate_request
from review_writer_api.errors import WorkflowStageNotReady, WorkflowValidationError
from review_writer_core.workflow import artifacts as names


def completion_requested(message):
    try:
        text = validate_request(message, message)
    except WorkflowValidationError:
        return False
    return bool(re.fullmatch(
        r"(?:按(?:照)?当前(?:流程|阶段|大纲)[，,\s]*)?(?:继续|接着|一直|请)?[，,\s]*(?:继续|一直|接着)?[，,\s]*(?:推进|进行|生成|写|做到)?[，,\s]*(?:到|直到|直至|至)[，,\s]*(?:完成综述|完成稿件|终稿)[。.!！]?"
        r"|continue (?:the current workflow )?(?:until|to) (?:the )?(?:review is complete|final manuscript)[.!]?",
        command_text(text), re.I))


def continuation_entry(message, service, principal, project_id, project, *, object_id="", attachment_ids=(), semantic=False, goal="final", auto_adopt=True):
    if object_id or attachment_ids or (not semantic and not completion_requested(message)):
        return None
    if any(job["job_type"] not in {"conversation.turn", "conversation.prepare", "conversation.control"}
           for job in project.get("jobs", [])):
        return {"reply": "当前项目已有任务正在执行。请等待实际结果，本轮没有重复提交生成任务。"}
    services = service.actions.services
    artifacts = {a["logical_name"]: a["id"] for a in project["artifacts"]}
    states = {s["id"]: s["status"] for stage in project["stages"] for s in stage["checkpoints"]}
    steps = []
    rank = {"discovery": 0, "planning": 1, "sections": 2, "draft": 3, "final": 4}[goal]

    def add(action, **arguments):
        steps.append({"action": action, "arguments": arguments})

    def result(reply):
        if not steps:
            return {"reply": reply}
        return {"reply": reply, "request": {"steps": steps, "request_quote": message,
                                             "stop_at": "planning" if goal == "discovery" else goal, "auto_adopt": auto_adopt}}

    if names.FINAL_MANUSCRIPT in artifacts and services.final_service.get(principal, project_id).get("final_current"):
        return result("当前终稿已完成，无需重复生成。需要文件时，请明确选择导出 Word 或 PDF。")
    if names.DRAFT_MANUSCRIPT in artifacts and rank >= 3:
        draft = services.drafts_service.get(principal, project_id)
        if draft["freshness"]["upstream_stale"]:
            return result("初稿的上游来源已变化，已有修改仍保留。请先在初稿核对并同步正文，再继续；未自动重新组装覆盖已有稿件。")
        if rank == 3:
            return result("当前初稿已存在，已有修改保留，无需重新组装。")
        if not draft.get("draft_approval_current") and not auto_adopt:
            return result("当前初稿已生成，是否采用当前稿件并组装终稿？")
        if not draft.get("draft_approval_current"):
            add("draft.approve")
        add("final.build")
        return result("已提交接续到终稿，保留当前初稿内容；不额外生成图像或导出文件。")
    if names.MATRIX not in artifacts:
        try:
            review = services.discovery_service.get(principal, project_id)
        except WorkflowStageNotReady:
            review = {}
        if auto_adopt and review.get("selected_paper_ids"):
            add("discovery.confirm")
            return result("已提交确认当前已选文献并启动分析，完成后继续已授权的目标；不重新检索或增加文献。")
        return result("请先确认要使用的文献（可发送「全部纳入推荐」），再继续生成综述。本轮没有替你选择论文。")
    if rank == 0:
        return result("当前已确认文献，无需重新筛选。")
    if names.PLANNING_OUTLINE not in artifacts:
        # Auto-generate and adopt a topic outline; revise later via conversation.
        add("planning.recommend_outline")
        if not auto_adopt:
            return result("已提交推荐大纲候选，生成后可查看并选择。")
        add("planning.apply_recommendation", source_job_id={"step": 0, "key": "job.id"})
        if rank == 1:
            return result("已提交生成并采用推荐大纲，不会启动正文写作。")
        add("planning.blueprint")
        if not auto_adopt:
            return result("已提交章节规划候选，尚未自动采用。")
        add("blueprint.confirm", artifact_id={"step": 2, "key": "blueprint_artifact_id"})
        section_current = False
    else:
        planning = services.planning_service.get(principal, project_id)
        if rank == 1:
            return result("当前大纲已存在，无需重复生成。")
        if not planning.get("outline_ready_for_chapter_planning"):
            return result("当前大纲尚不能用于章节规划，请在分析与大纲阶段查看并完成现有提示，再继续。")
        if not planning.get("blueprint_current"):
            add("planning.blueprint")
            if not auto_adopt:
                return result("已提交章节规划候选，尚未自动采用。")
            add("blueprint.confirm", artifact_id={"step": 0, "key": "blueprint_artifact_id"})
            section_current = False
        else:
            if not planning.get("active_blueprint_approved"):
                if not auto_adopt:
                    return result("当前章节规划尚未采用，请先确认规划后再生成正文。")
                add("blueprint.confirm", artifact_id=planning["blueprint_artifact_id"])
            try:
                sections = services.sections_service.get(principal, project_id)
            except WorkflowStageNotReady:
                section_current = False
            else:
                section_current = bool(sections["handoff"]["current"] and not sections["report"].get("pending_section_count"))
    if not section_current:
        add("sections.generate")
    if auto_adopt and (not section_current or states.get("sections") != "approved"):
        add("sections.confirm")
    if rank == 2 or (not auto_adopt and (not section_current or states.get("sections") != "approved")):
        return result("已提交章节正文操作，保留后续采用和初稿步骤。" if steps else "当前章节正文已就绪，无需重复生成。")
    # Figures: preserve source images by default; redraw stays an explicit request.
    if section_current and states.get("figures") == "approved":
        figures = services.figures_service.get(principal, project_id)
        if figures["freshness"].get("stale"):
            return result("已有配图来源已更新，请核对当前配图，已有图像成果保留。")
    if not section_current or states.get("figures") != "approved":
        if not auto_adopt:
            return result("当前配图处理尚未确认，请选择保留原图或无图处理后，再组装初稿；不自动重绘。")
        add("images.preserve_sources")
        add("images.confirm")
    add("draft.assemble")
    if rank == 3 or not auto_adopt:
        return result("已提交组装初稿，不会提前批准或进入终稿。")
    add("draft.approve")
    add("final.build")
    return result("已提交接续生成：按主题大纲生成章节、保留原图、组装初稿并接续终稿。可在对话中随时修改大纲、正文或配图。")
