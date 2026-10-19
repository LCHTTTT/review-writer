import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";

import type { AuthConfig, Principal } from "../../api/types";
import { PublicHeader } from "../../components/PublicHeader";
import { useUiText } from "../../i18n/useUiText";
import { workflowLinks } from "../../workflow";

type GuideStep = {
  id: string;
  title: [string, string];
  purpose: [string, string];
  actions: [string[], string[]];
  done: [string, string];
  tip: [string, string];
  href: string;
};

const steps: GuideStep[] = [
  {
    id: "library", title: ["文献库", "Library"],
    purpose: ["把论文原文上传到账号文献库，供后续检索和写作使用。上传后仍需在筛选阶段确认本项目采用的论文。", "Upload full-text papers to your account library for discovery and writing. Confirm which papers this project uses in the screening stage."],
    actions: [["选择当前项目，上传 PDF、文件夹或 ZIP。", "等待解析完成，检查论文标题、作者和解析状态。"], ["Select your project and upload PDFs, a folder, or a ZIP file.", "Wait for parsing, then check titles, authors, and parsing status."]],
    done: ["所需论文已出现在文献库，且可查看解析结果。", "The papers appear in the library and their parsed content is available."],
    tip: ["解析可能需要一段时间；加密 PDF 和嵌套压缩包不受支持。", "Parsing can take time. Encrypted PDFs and nested archives are not supported."],
    href: "/library",
  },
  {
    id: "discovery", title: ["检索与筛选", "Discovery and screening"],
    purpose: ["围绕研究主题找到候选论文，并决定哪些进入综述。", "Find candidate papers for your topic and decide which belong in the review."],
    actions: [["填写或核对检索主题，运行检索。", "查看候选文献和全文获取情况，确认采用的论文。"], ["Review the search topic and run discovery.", "Check candidate papers and full-text access, then confirm the papers to use."]],
    done: ["项目已有一组确认采用的论文。", "The project has a confirmed set of papers."],
    tip: ["重新检索会产生待确认的候选结果；请检查后再采用。", "A new search produces candidates for review; inspect them before adopting them."],
    href: "/discovery",
  },
  {
    id: "planning", title: ["文献分析与大纲", "Paper analysis and outline"],
    purpose: ["把已选论文整理为证据和章节结构。", "Turn selected papers into evidence and a chapter structure."],
    actions: [["检查文献分析和证据内容。", "生成或调整大纲，并确认适合研究主题的章节。"], ["Review the paper analysis and evidence.", "Generate or edit the outline, then confirm its sections."]],
    done: ["大纲中的章节和所依据的论文都已核对。", "The outline and its supporting papers have been reviewed."],
    tip: ["后续章节依赖这里确认的论文与大纲。", "Later writing depends on the papers and outline confirmed here."],
    href: "/planning?view=reading",
  },
  {
    id: "sections", title: ["章节规划与正文", "Section planning and writing"],
    purpose: ["按章节任务生成有论文依据的正文。", "Draft sections using paper-linked writing tasks."],
    actions: [["检查并采用各章节的规划与写作任务。", "生成章节正文，核对论点、段落和引用；也可以批量生成。"], ["Review and adopt the section plans and writing tasks.", "Generate sections, individually or in a batch, and check claims, paragraphs, and citations."]],
    done: ["需要的章节已有可审阅的正文。", "The required sections have drafts ready for review."],
    tip: ["生成内容是写作草稿，科学事实和引用仍需作者核对。", "Generated text is a draft. Verify scientific claims and citations yourself."],
    href: "/sections",
  },
  {
    id: "images", title: ["选图与重绘", "Figures and redraw"],
    purpose: ["从源论文中选择合适图片，并按需制作综述配图。", "Select source figures and prepare review figures where useful."],
    actions: [["选择与章节相关的源图；可保留原图，也可按需重绘。", "检查配图完整性；需要时在 SVG 或 Ketcher 中修改，再确认配图。"], ["Choose source figures relevant to the sections; keep originals or redraw as needed.", "Review figure integrity, edit in SVG or Ketcher if needed, then confirm the figures."]],
    done: ["需要使用的图片已经审核。", "The figures you intend to use have been reviewed."],
    tip: ["图像步骤按需要进行；不要把 AI 重绘结果当作未经核对的原始数据。", "Use this stage as needed, and verify AI redraws against the source figures."],
    href: "/images?tab=review",
  },
  {
    id: "draft", title: ["初稿编辑与优化", "Draft editing and revision"],
    purpose: ["统一修改正文、摘要、结论和可选的总览图。", "Revise the manuscript, abstract, conclusion, and optional overview figure."],
    actions: [["阅读完整初稿并检查引用。", "手动编辑或提出优化请求，确认满意的修改版本。"], ["Read the full draft and inspect citations.", "Edit directly or request improvements, then approve the version you want to keep."]],
    done: ["正文和需要的补充内容都已确认。", "The manuscript and supporting content are approved."],
    tip: ["优化候选需要确认后才会成为保存的版本。", "Proposed revisions become the saved version only after approval."],
    href: "/draft",
  },
  {
    id: "final", title: ["终稿审计与导出", "Final audit and export"],
    purpose: ["检查交付内容并下载 Word 或 PDF。", "Review the deliverable and download Word or PDF."],
    actions: [["组装已确认的初稿，处理引用、出版信息和格式问题。", "预览终稿，确认无误后导出。"], ["Assemble the approved draft and resolve reference, publication, and formatting issues.", "Preview the final manuscript, then export it."]],
    done: ["已下载并检查 Word 或 PDF 文件。", "You have downloaded and checked the Word or PDF file."],
    tip: ["导出前请再次核对科学内容、引用及图像使用权限。", "Before export, verify scientific content, citations, and figure permissions."],
    href: "/final",
  },
];

function projectHref(href: string, project: string | null): string {
  if (!project) return href;
  const [path, query = ""] = href.split("?");
  const params = new URLSearchParams(query);
  params.set("project", project);
  return `${path}?${params.toString()}`;
}

export function GuidePage({ authConfig, identity }: { authConfig: AuthConfig; identity: Principal | null }) {
  const { language, text } = useUiText();
  const location = useLocation();
  const project = new URLSearchParams(location.search).get("project");
  const startHref = identity ? "/workspace#create-project" : "/login?next=%2Fworkspace%23create-project";

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  useEffect(() => {
    if (!location.hash) return;
    const target = document.getElementById(location.hash.slice(1));
    target?.scrollIntoView?.();
  }, [location.hash]);

  return <div className="public-page guide-page">
    <PublicHeader authConfig={authConfig} identity={identity} compact />
    <main className="guide-container">
      <header className="guide-hero">
        <p className="eyebrow">Review Writer · {text("使用指南", "User guide")}</p>
        <h1>{text("从第一个项目到综述终稿", "From your first project to the final review")}</h1>
        <p>{text("按照下面的步骤完成一次综述写作。每个阶段都可以返回检查和修改。", "Follow these steps to write a review. You can return to inspect and revise each stage.")}</p>
      </header>

      <section className="guide-quick-start" id="quick-start" aria-labelledby="guide-quick-title">
        <div className="guide-quick-heading"><span className="step-label">{text("5 分钟了解流程", "The workflow in 5 minutes")}</span><h2 id="guide-quick-title">{text("第一次使用，先完成这四步", "Start with these four steps")}</h2></div>
        <ol className="guide-quick-steps">
          <li><strong>{text("创建项目", "Create a project")}</strong><span>{text("填写项目 ID 和研究主题，选择分类与文本模型。", "Enter a project ID and topic, then choose a taxonomy and text model.")}</span></li>
          <li><strong>{text("准备论文", "Add papers")}</strong><span>{text("上传 PDF，等待解析并核对文献信息。", "Upload PDFs, wait for parsing, and check paper details.")}</span></li>
          <li><strong>{text("确认文献", "Confirm papers")}</strong><span>{text("检索候选论文，检查后确认采用的集合。", "Search for candidates and confirm the papers to use.")}</span></li>
          <li><strong>{text("规划写作", "Plan the review")}</strong><span>{text("检查证据与大纲，再逐章生成正文。", "Review evidence and the outline, then draft each section.")}</span></li>
        </ol>
        <Link className="button button-primary" to={startHref}>{text("开始创建项目", "Create your first project")}</Link>
      </section>

      <div className="guide-layout">
        <nav className="guide-toc" aria-label={text("指南目录", "Guide contents")}>
          <strong>{text("目录", "Contents")}</strong>
          <a href="#quick-start">{text("快速开始", "Quick start")}</a>
          <a href="#before-you-start">{text("开始前准备", "Before you start")}</a>
          {steps.map((step, index) => <a key={step.id} href={`#${step.id}`}>{String(index + 1).padStart(2, "0")} · {text(...step.title)}</a>)}
          <a href="#common-questions">{text("常见问题", "Common questions")}</a>
        </nav>
        <details className="guide-toc-mobile">
          <summary>{text("指南目录", "Guide contents")}</summary>
          <nav aria-label={text("指南目录", "Guide contents")}>
            <a href="#quick-start">{text("快速开始", "Quick start")}</a>
            <a href="#before-you-start">{text("开始前准备", "Before you start")}</a>
            {steps.map((step, index) => <a key={step.id} href={`#${step.id}`}>{String(index + 1).padStart(2, "0")} · {text(...step.title)}</a>)}
            <a href="#common-questions">{text("常见问题", "Common questions")}</a>
          </nav>
        </details>

        <div className="guide-content">
          <section className="guide-section" id="before-you-start">
            <span className="step-label">{text("开始前准备", "Before you start")}</span>
            <h2>{text("准备研究主题和论文", "Prepare your topic and papers")}</h2>
            <p>{text("建议先确定综述范围，并准备相关论文 PDF，也可以通过联网检索寻找候选论文。项目 ID 只接受小写字母、数字和连字符；研究主题可以之后继续调整。生成任务需要可用的文本模型与账户额度，PDF 解析需要解析服务就绪。服务不可用请联系管理员；额度不足请按页面提示补充。图像与语义索引属于按需能力，不必先配置全部服务。", "Define your review scope and prepare relevant PDFs, or use online discovery to find candidate papers. Project IDs use lowercase letters, numbers, and hyphens. You can refine the topic later. Generation requires an available text model and balance; PDF parsing requires the parsing service. Contact the administrator for unavailable services and follow the app's balance instructions. Image generation and semantic indexing are optional; you do not need to configure every service first.")}</p>
          </section>

          {steps.map((step, index) => {
            const stage = workflowLinks.find(item => item.id === step.id);
            return <section className="guide-section" id={step.id} key={step.id}>
              <span className="step-label">{stage?.stage || String(index + 1).padStart(2, "0")} · {text("工作阶段", "Workflow stage")}</span>
              <h2>{text(...step.title)}</h2>
              <p className="guide-purpose">{text(...step.purpose)}</p>
              <h3>{text("怎么操作", "What to do")}</h3>
              <ol>{step.actions[language === "en" ? 1 : 0].map(action => <li key={action}>{action}</li>)}</ol>
              <div className="guide-stage-notes">
                <p><strong>{text("完成标志", "You're done when")}</strong>{text(...step.done)}</p>
                <p><strong>{text("注意", "Tip")}</strong>{text(...step.tip)}</p>
              </div>
              <Link className="button button-secondary" to={projectHref(step.href, project)}>{text(`前往${step.title[0]}`, `Go to ${step.title[1]}`)} <span aria-hidden="true">↗</span></Link>
            </section>;
          })}

          <section className="guide-section" id="common-questions">
            <span className="step-label">FAQ</span>
            <h2>{text("常见问题", "Common questions")}</h2>
            <details><summary>{text("任务运行时可以离开页面吗？", "Can I leave while a task is running?")}</summary><p>{text("已提交到后台的解析和生成任务会继续执行，返回相应阶段可查看。文件仍在浏览器上传时，请先等待上传完成；不要关闭页面或假设切换后仍会上传。", "Submitted background parsing and generation tasks continue; return to the stage to check them. While files are still uploading in your browser, wait for upload completion before leaving or closing the page.")}</p></details>
            <details><summary>{text("修改前面阶段后，后面的内容怎么办？", "What happens if I change an earlier stage?")}</summary><p>{text("先确认修改。系统会根据产物版本提示哪些后续内容需要更新；检查提示后再继续。", "Confirm the change first. The app marks downstream artifacts that need updating based on their versions. Review those notices before continuing.")}</p></details>
            <details><summary>{text("AI 生成的内容可以直接提交吗？", "Can I submit AI-generated content as is?")}</summary><p>{text("请在导出或投稿前逐项核对科学事实、论文引用、图像内容和使用权限。", "Before export or submission, verify scientific claims, references, figure content, and permissions.")}</p></details>
          </section>
        </div>
      </div>
    </main>
  </div>;
}
