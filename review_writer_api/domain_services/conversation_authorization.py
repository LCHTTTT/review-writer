"""Conservative user-command boundary for model-selected project tools.

A tool must cite the current user request, never a paper/history as authority.
Ambiguous language remains a proposal. Business permission/version checks are
still owned by the original services, not by this intent classifier.
"""
import re

from review_writer_api.errors import WorkflowValidationError

RANK = {"discovery": 0, "planning": 1, "blueprint": 2, "sections": 2, "images": 3, "draft": 4, "final": 5, "tasks": 5, "library": -1}
ADOPTIONS = {"discovery.confirm", "planning.save_outline", "planning.apply_recommendation", "blueprint.confirm", "sections.confirm", "images.confirm", "draft.accept_candidate", "draft.accept_generated", "draft.sync_section", "sections.accept_rewrite", "draft.approve", "draft.adopt_overview"}


def revision_save_requested(message):
    if re.search(r"不保存|不要保存|先不保存|先别保存|do not save|don't save", message, re.I):
        return False
    return bool(re.search(r"修改|改写|重写|润色|优化|修订|revise|rewrite|polish|edit|improve", message, re.I)
                and re.search(r"保存|写入正文|替换原文|save|apply", message, re.I))


def command_text(message):
    value = message.strip()
    value = re.sub(r"^(?:(?:请|帮我|麻烦|现在|please|can you please)\s*)+", "", value, flags=re.I)
    return value


def web_search_requested(message, preference=None):
    """Online retrieval is opt-in for this turn, not inherited from history."""
    if preference is False or re.search(r"不联网|不要联网|不需要联网|不用联网|禁止联网|无需联网|不要在线.{0,8}(?:搜索|检索)|(?:只|仅).{0,20}(?:本地|文献库|上传)|no web|offline|local only|only.{0,20}(?:library|uploaded)|(?:do not|don't|without)\s+(?:(?:use|search|retrieve|find)\s+)?(?:the\s+)?(?:web|online|internet)", message, re.I):
        return False
    if preference is True:
        return True
    return bool(re.search(r"联网|在线.{0,8}(?:检索|搜索|查找)|(?:检索|搜索|查找).{0,8}在线|\b(?:web|online|internet)\b.{0,24}\b(?:search|retrieve|find)\b|\b(?:search|retrieve|find)\b.{0,40}\b(?:web|online|internet)\b", message, re.I))


def recommended_selection_requested(message):
    return bool(re.fullmatch(r"(?:全部纳入(?:推荐(?:结果|文献|论文)?)?|纳入全部(?:推荐(?:结果|文献|论文)?)?|全选(?:推荐(?:结果|文献|论文)?)?|全部选择(?:推荐(?:结果|文献|论文)?)?|include all recommended(?: papers| results)?)(?:[，,\s]*(?:并)?(?:继续|进入下一阶段|开始分析|确认并分析))?[。.!！]?", command_text(message), re.I))


def is_stop_continuation(message):
    match = re.match(r"^(?:停止接续|暂停接续|停止后续采用|停止后续执行|stop continuation|pause continuation)(?=\s|[，,。.!！]|$)", command_text(message), re.I)
    if not match:
        return False
    suffix = command_text(message)[match.end():].strip(" ，,。.!！").lower()
    return suffix in {"", "不调用模型", "保留已提交任务", "不要取消已提交任务", "no model calls", "keep submitted tasks"}


def is_steering(message):
    return bool(re.match(r"^(?:先别|先不要|停止|暂停|取消|改为|改成|调整|不要采用|stop|pause|cancel|instead|change)", command_text(message), re.I))


def validate_supported_limits(message):
    # Refuse unsupported hard limits BEFORE model admission, rather than charge
    # for interpreting a request whose cost/retry fence cannot be enforced.
    if re.search(r"(?:预算|费用|花费|cost|budget).{0,16}(?:上限|最多|不超过|不得超过|at most|limit|under|<=)|最多\s*\d+(?:\.\d+)?\s*(?:元|美元|credits)|不要收费|不产生费用|不调用模型|no charges|no model calls", message, re.I):
        raise WorkflowValidationError("当前对话不能硬约束指定费用上限／零模型调用，未提交模型或生成任务；可通过成果阅读区查看已有内容。")
    if re.search(r"最多.{0,6}重试|不重试|no retries|at most.{0,8}retr(?:y|ies)", message, re.I):
        raise WorkflowValidationError("当前执行器尚未支持本次指定的重试次数，未提交任务；没有用平台默认重试策略代替。")


def validate_request(message, quote, *, semantic=False):
    if not isinstance(quote, str) or len(quote.strip()) < 2 or quote not in message:
        raise WorkflowValidationError("执行必须引用本轮用户的明确要求，论文和历史内容不能授权操作。")
    text = command_text(message)
    informational_question = semantic and (re.match(r"^(?:为什么|是什么原因|有什么用)", text) or re.search(r"(?:会怎样|有什么用|是什么原因|为什么)[^，,。.!！?？]*[?？]$", text))
    if (not semantic and re.search(r"[?？]", text)) or informational_question or re.search(r"(?:生成|继续).*(?:需要多少|需要什么|是否|会不会)", text):
        raise WorkflowValidationError("这是询问，不是执行授权；没有自动提交任务。")
    # No fixed verb-prefix gate: the intent is decided by the model and then
    # checked against real objects, versions and permissions by the callers.
    # Only explicit "discuss / don't execute" requests are refused here.
    if re.search(r"(?:先不要(?!保存|采用|确认)|先别(?!保存|采用|确认)|不要执行|不要修改(?!其他|其它|别的|其余)|仅讨论|只讨论|先思考|do not execute|do not change(?!\s+(?:other|any other))|discussion only|just discuss)", message, re.I):
        raise WorkflowValidationError("本轮要求先讨论或暂不执行，没有提交写入／生成操作。")
    return text


def authorize_action(message, quote, name, args, *, automatic=False, stop_at=None, web_search=None):
    text = validate_request(message, quote)
    if name == "sections.generate" and args.get("restart"):
        if (automatic or re.search(r"不要重新|不重新|不要从头|do not restart|don't restart", text, re.I)
                or not re.search(r"重新生成|从头(?:生成|写)|restart|regenerate|from scratch", text, re.I)):
            raise WorkflowValidationError("本轮未授权从头重新生成章节；继续请求应保留有效章节并续跑。")
    if name == "discovery.search" and args.get("web_search") and not web_search_requested(message, web_search):
        raise WorkflowValidationError("只有本轮明确要求联网检索时才会联网；未授权联网，未提交该操作。")
    if name != "tasks.cancel" and re.match(r"取消|停止|暂停|cancel|stop|pause", text, re.I):
        raise WorkflowValidationError("这是停止／取消要求，不是生成或写入授权；混合要求请拆成明确操作。")
    if name in ADOPTIONS and re.search(r"不采用|不要采用|不确认|不要确认|不批准|不保存|do not (?:adopt|accept|confirm|approve|save)|don't (?:adopt|accept|confirm|approve|save)", text, re.I):
        raise WorkflowValidationError("本轮明确保留采用节点，没有自动采用或批准。")
    if stop_at and RANK[name.split(".")[0]] > RANK[stop_at]:
        raise WorkflowValidationError("操作超出本次截止阶段。")
    if automatic:
        # A clear directive may run the recommended plan end-to-end, but never
        # expands redraw, prose-rewrite or field-edit scope on its own, and
        # approval still requires the final stop target. Online retrieval
        # requires explicit current-turn authorization, checked above.
        if name in {"images.redraw", "draft.overview", "draft.adopt_overview", "discovery.acquire", "draft.revise_section", "draft.revise_paragraph", "draft.replace_text", "sections.rewrite", "sections.accept_rewrite"}:
            raise WorkflowValidationError("自动推进不授权扩大重绘或改写范围。")
        if name.startswith(("library.", "tasks.")) or name in {"discovery.selection", "discovery.select_library", "draft.manuscript_fields", "draft.publication", "draft.accept_generated", "draft.sync_section", "blueprint.fields"}:
            raise WorkflowValidationError("自动推进不授权修改用户字段或任意采用既有修改。")
        if name == "draft.approve" and stop_at != "final":
            raise WorkflowValidationError("推进到初稿不等于批准出版；没有提前批准。")
    else:
        targets = {"discovery.search": r"检索|搜索|search", "discovery.confirm": r"筛选|纳入|论文|discovery|selection|papers",
                   "planning.analyze": r"文献分析|分析.*(?:论文|文献)|analy[sz]e.*(?:papers|matrix)",
                   "planning.save_outline": r"大纲|outline", "planning.recommend_outline": r"大纲|outline", "planning.apply_recommendation": r"大纲|outline", "planning.blueprint": r"规划|blueprint|chapter plan",
                   "discovery.acquire": r"下载|获取全文|download|acquire", "draft.overview": r"总览图|综述图|overview", "draft.adopt_overview": r"总览图|综述图|overview",
                   "blueprint.confirm": r"规划|blueprint|chapter plan", "sections.generate": r"正文|章节|sections|chapters",
                   "sections.confirm": r"正文|章节|sections|chapters", "images.confirm": r"图像|图片|原图|figures|images",
                   "draft.assemble": r"初稿|draft", "draft.revise_section": r"初稿|draft",
                   "draft.revise_paragraph": r"初稿|段|paragraph|draft", "draft.replace_text": r"替换|改成|改为|删除|去掉|replace|change|remove|delete",
                   "draft.accept_candidate": r"初稿|摘要|结论|候选|修改|draft|abstract|conclusion|candidate",
                   "draft.approve": r"初稿|draft", "final.build": r"终稿|final"}
        if name in targets and not re.search(targets[name], text, re.I):
            raise WorkflowValidationError("操作目标不匹配本轮要求，未跨阶段执行。")
        if name == "draft.approve" and not re.search(r"批准|确认|approve|confirm", text, re.I):
            raise WorkflowValidationError("保存修改不等于批准初稿。")
        if name == "draft.synthesis" and not re.search(r"摘要|abstract" if args.get("role", "abstract") == "abstract" else r"结论|conclusion", text, re.I):
            raise WorkflowValidationError("摘要／结论目标不匹配本轮要求。")
        if name == "discovery.acquire" and (not args.get("candidate_id") or args["candidate_id"] not in message):
            raise WorkflowValidationError("请明确指定要下载的候选 ID；不会自动下载全部检索结果。")
        if name == "tasks.cancel" and (not re.search(r"取消|cancel", text, re.I) or not args.get("job_id") or args["job_id"] not in message):
            raise WorkflowValidationError("取消须明确指定本项目的父任务 ID。")
    if not automatic and name in ADOPTIONS and not re.search(r"(?:采用|确认|批准|保存|adopt|accept|confirm|approve|save)", text, re.I) and not (name == "discovery.confirm" and recommended_selection_requested(message)):
        raise WorkflowValidationError("生成候选和普通继续不等于允许采用。")
    if name in {"library.metadata", "draft.manuscript_fields", "draft.publication", "blueprint.fields"}:
        labels = {"title": r"标题|题名|title", "keywords": r"关键词|keywords", "authors": r"作者|authors",
                  "affiliations": r"单位|机构|affiliations", "journal": r"期刊|journal", "year": r"年份|年|year",
                  "volume": r"卷|volume", "issue": r"期|issue", "pages": r"页|pages", "doi": r"doi", "review_problem": r"研究问题|综述问题|review.problem"}
        for field, value in args.get("fields", {}).items():
            values = value if isinstance(value, list) else [str(value)]
            if not re.search(labels.get(field, r"(?!)"), message, re.I) or any(str(item) not in message for item in values):
                raise WorkflowValidationError("直接字段保存只接受用户明确指定的字段和值；模型建议请先展示候选。")
            if (not values or any(not str(item).strip() for item in values)) and not re.search(r"清空|删除|去掉|clear|remove", message, re.I):
                raise WorkflowValidationError("没有授权清空这个字段。")
        if name == "library.metadata" and args.get("paper_id", "") not in message:
            raise WorkflowValidationError("请指定文献 ID，不能从模糊的‘这篇’猜测账号文献库对象。")
    if name == "draft.replace_text":
        if (not args.get("old_text") or args["old_text"] not in message
                or (args.get("new_text") and args["new_text"] not in message)):
            raise WorkflowValidationError("精确替换只接受本轮明确给出的原文和新文字，不能用模型自拟正文替代。")
        if not args.get("new_text") and not re.search(r"删除|去掉|移除|空文字|空字符串|remove|delete|empty", message, re.I):
            raise WorkflowValidationError("删除文字须明确提出删除要求。")
        if re.search(r"不保存|不要保存|先不保存|仅预览|只预览|preview only|do not save|don't save", message, re.I):
            raise WorkflowValidationError("本轮只要求预览，请展示替换建议，不执行保存。")
    if name == "draft.revise_section" and re.search(r"(?:只|仅).{0,12}(?:段|句)|only.{0,24}(?:paragraph|sentence)", message, re.I):
        raise WorkflowValidationError("这是指定段落修改，请定位该段并使用单段修改，不扩大为整章。")
    if name == "draft.revise_paragraph" and (not args.get("message") or args["message"] != message):
        raise WorkflowValidationError("段落修改必须保留本轮完整要求，不能遗漏范围或否定条件。")
    if name == "discovery.selection":
        remove = re.search(r"移出|排除|不纳入|remove|exclude", message, re.I)
        include = re.search(r"纳入|加入|include|add", message, re.I)
        if (args.get("selected") and (remove or not include)) or (not args.get("selected") and not remove):
            raise WorkflowValidationError("论文纳入／移出选择须匹配本轮明确要求。")
    if name == "sections.generate" and not automatic:
        # This existing executor has no upstream rewrite contract.
        if re.search(r"修改|改写|重写|revise|rewrite", text, re.I):
            raise WorkflowValidationError("上游正文改写尚未接入，没有用生成／续跑替代改写。")
        if re.search(r"第[一二三四五六七八九十\d]+[章节]|(?:chapter|section)\s+\d", text, re.I) and not re.search(r"所有|全部|all", text, re.I):
            raise WorkflowValidationError("当前正文工具不是单章改写入口，未扩大成全量正文任务。")
    if name == "sections.rewrite":
        if not args.get("section_id") or args["section_id"] not in message or not args.get("message") or args["message"] not in message or not re.search(r"上游|原章节|sections", text, re.I) or not re.search(r"整章|整个章节|whole.chapter", text, re.I):
            raise WorkflowValidationError("上游整章改写须明确指定章节 ID、整章范围及本轮原文要求；不能代替段落局部编辑。")
    if name == "sections.accept_rewrite" and (not args.get("artifact_id") or args["artifact_id"] not in message or not re.search(r"上游|原章节|sections", text, re.I)):
        raise WorkflowValidationError("采用须明确指定上游章节改写候选 ID；普通继续不授权采用。")
    if name == "draft.sync_section" and (not re.search(r"初稿|draft", text, re.I) or not re.search(r"同步.*保存|sync.*save", text, re.I)):
        raise WorkflowValidationError("须明确同步到初稿并保存；普通讨论只展示同步候选。")
    if name == "images.redraw" and (not re.search(r"重绘|重画|redraw", text, re.I) or re.search(r"保留原图|不重绘|keep.*source|no redraw", text, re.I)):
        raise WorkflowValidationError("本轮没有授权重绘这些图片。")
    if name == "images.redraw":
        instructions = args.get("edit_instructions", "")
        if instructions and instructions not in text:
            raise WorkflowValidationError("图像编辑要求必须来自本轮用户原文，不能自行添加。")
        if re.search(r"配色|布局|颜色|字体|标注|color|layout|font|annotation", text, re.I) and not instructions:
            raise WorkflowValidationError("请传递本轮图像编辑要求，不能静默忽略参数后提交重绘。")
        if re.search(r"尺寸|分辨率|resolution|\d+\s*[x×]\s*\d+", text, re.I):
            raise WorkflowValidationError("尚不能保证指定图像尺寸／分辨率，没有提交不符合要求的任务。")
        if re.search(r"配色|颜色|color|红色|蓝色|绿色", text, re.I) and args.get("palette") != "source":
            raise WorkflowValidationError("明确配色要求不能由默认灰度策略覆盖；须采用 source 调色模式。")
    if name == "images.preserve_sources" and not automatic and not re.search(r"保留原图|不重绘|keep.*source|preserve.*source|no redraw", text, re.I):
        raise WorkflowValidationError("请明确图像处理选择；没有默认为重绘或保留。")
    if name == "draft.accept_generated" and not revision_save_requested(text):
        raise WorkflowValidationError("只有本轮明确修改并保存的章节可以自动采用生成结果。")
    if name in {"final.export", "final.pdf"}:
        formats = r"Word|docx" if name == "final.export" else r"PDF"
        if not re.search(formats, text, re.I):
            raise WorkflowValidationError("没有授权本次导出格式。")
