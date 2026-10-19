import { Link } from "react-router-dom";

import type { AuthConfig, Principal } from "../../api/types";
import { PublicHeader } from "../../components/PublicHeader";
import { useUiText } from "../../i18n/useUiText";

const workflowStages = [
  ["01–02", "准备与筛选文献", "Prepare and select papers", "上传论文到文献库，或联网寻找候选文献。围绕研究主题筛选，确认本次综述采用的论文。", "Upload papers to your library or find candidates online. Screen them against your topic and confirm the papers for this review."],
  ["03–04", "分析证据与章节写作", "Analyze evidence and draft sections", "整理论文贡献、关键结果与来源证据，选择综述大纲和章节主线，再生成可核对的正文。", "Organize contributions, key results, and source evidence. Choose an outline and section narrative, then generate drafts you can verify."],
  ["05–06", "配图与初稿修订", "Figures and draft revision", "按需保留原图或重绘；直接编辑完整正文，也可通过章节对话和批量优化修订，完善摘要、结论与可选总览图。", "Keep original figures or redraw them as needed. Edit the manuscript directly, use chapter dialogue or batch revision, and refine the abstract, conclusion, and optional overview."],
  ["07", "检查与导出终稿", "Review and export", "组装已确认的初稿，补全必要书目信息、检查引用和配图，导出 Word 与 LaTeX 排版 PDF。", "Assemble the approved draft, complete necessary bibliography details, check citations and figures, and export Word or a LaTeX-typeset PDF."],
] as const;

export function LandingPage({ authConfig, identity }: { authConfig: AuthConfig; identity: Principal | null }) {
  const { text } = useUiText();
  const workspaceHref = identity ? "/workspace" : "/login";

  return (
    <div className="public-page chemistry-landing">
      <PublicHeader authConfig={authConfig} identity={identity} />
      <main>
        <section className="product-hero">
          <svg className="hero-chemistry-pattern" viewBox="0 0 1400 760" fill="none" aria-hidden="true" focusable="false">
            <g stroke="currentColor" strokeWidth="2">
              <path d="M40 180l52-30 52 30v60l-52 30-52-30zM51 187l41-24M133 187v45M51 233l41 24M144 180l52-30 52 30M248 180l52-30 52 30v60l-52 30-52-30z" />
              <path d="M1120 560l52-30 52 30v60l-52 30-52-30zM1131 567l41-24M1213 567v45M1131 613l41 24M1224 560l52-30 52 30M1276 530v-60M1284 530v-60" />
              <path d="M790 80h150m-18-10 18 10-18 10M380 650h120m-18-10 18 10-18 10" />
              <circle cx="1050" cy="150" r="10" /><circle cx="1090" cy="120" r="7" /><circle cx="1120" cy="165" r="9" />
              <path d="M1058 144l27-19m10 1 20 31m-55-5 51 11" />
            </g>
          </svg>
          <div className="product-hero-copy">
            <p className="eyebrow">{text("面向化学研究的综述写作工作台", "A review-writing workspace for chemistry research")}</p>
            <h1>{text("从化学文献到综述，读得清楚，写得有据。", "From chemistry papers to a clear, evidence-grounded review.")}</h1>
            <p className="lead">{text("围绕研究问题整理文献证据，规划章节、比较研究与修订正文。Review Writer 将文献筛选、化学配图、初稿讨论和 Word / PDF 导出连成一条清晰的工作流，关键判断始终由你掌握。", "Organize evidence around research questions, plan sections, compare studies, and revise your manuscript. Review Writer connects paper selection, chemistry figures, draft dialogue, and Word / PDF export in one clear workflow—with you in control of the key decisions.")}</p>
            <p className="product-domain-note">{text("以化学综述为重点，也支持通用学术写作。", "Built with chemistry reviews in mind, with support for general academic writing.")}</p>
            <div className="product-hero-actions">
              <Link className="button button-primary product-cta" to={workspaceHref}>{identity ? text("进入工作台", "Open workspace") : text("登录后开始", "Sign in to start")}</Link>
              <a className="button button-quiet product-cta" href="#workflow">{text("查看完整流程", "Explore the workflow")}</a>
            </div>
            <div className="product-trust-row" aria-label={text("产品特性", "Product qualities")}>
              <span>{text("证据驱动", "Evidence-grounded")}</span>
              <span>{text("可编辑、可讨论", "Editable and conversational")}</span>
              <span>{text("版本可追溯", "Version-traceable")}</span>
            </div>
          </div>
          <div className="product-hero-visual" aria-label={text("综述工作流预览", "Review workflow preview")}>
            <div className="hero-status-line"><span>{text("项目示意", "Example project")}</span><strong>{text("不对称催化与合成策略综述", "Asymmetric catalysis and synthetic strategies")}</strong><em>{text("阶段 06", "Stage 06")}</em></div>
            <div className="hero-flow-map">
              <article className="complete"><b>01</b><span>{text("文献库", "Library")}</span><small>{text("30 篇已解析", "30 parsed")}</small></article>
              <article className="complete"><b>03</b><span>{text("大纲", "Outline")}</span><small>{text("11 节已确认", "11 confirmed")}</small></article>
              <article className="complete"><b>05</b><span>{text("图像", "Figures")}</span><small>{text("20 张已审核", "20 reviewed")}</small></article>
              <article className="active"><b>06</b><span>{text("初稿", "Draft")}</span><small>{text("正在优化", "Optimizing")}</small></article>
            </div>
            <div className="hero-document-preview">
              <div><span /><span /><span /></div>
              <p /><p /><p className="short" />
              <section className="hero-chemistry-scheme" aria-hidden="true"><svg viewBox="0 0 360 100" fill="none" focusable="false"><g stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M35 30l26-15 26 15v30L61 75 35 60zM42 34l19-11m19 11v22M42 56l19 11M87 30l27-15 27 15M218 30l26-15 26 15v30l-26 15-26-15zM225 34l19-11m19 11v22m-38 0 19 11M270 30l27-15 27 15M155 45h48m-10-7 10 7-10 7" /></g></svg></section>
              <p /><p className="short" />
            </div>
          </div>
        </section>

        <section className="product-section" id="capabilities">
          <header className="product-section-heading">
            <p className="eyebrow">{text("核心能力", "Core capabilities")}</p>
            <h2>{text("让文献、论证与配图，成为一篇完整综述。", "Bring papers, arguments, and figures into one review.")}</h2>
          </header>
          <div className="capability-grid">
            <article><span>01</span><h3>{text("找到相关文献", "Find relevant papers")}</h3><p>{text("批量解析 PDF，结合本地全文与联网检索筛选文献。可用的语义索引按需参与召回，不必重复整理原文。", "Parse PDFs in batches and select papers through local full-text and online discovery. Available semantic indexes can assist retrieval without reorganizing the source papers.")}</p></article>
            <article><span>02</span><h3>{text("围绕问题组织证据", "Organize evidence around questions")}</h3><p>{text("从论文贡献与关键结果出发，调整综述范围、大纲和章节主线；写作时按需回读来源，便于核对论点与引用。", "Start with contributions and key results, then refine the scope, outline, and section narrative. Revisit source passages during writing to check arguments and citations.")}</p></article>
            <article><span>03</span><h3>{text("化学配图与结构编辑", "Chemistry figures and structure editing")}</h3><p>{text("保留原图或按需重绘，通过 SVG 与 Ketcher 编辑配图；在初稿中生成、比较并保存总览图版本。", "Keep source figures or redraw them, refine figures with SVG and Ketcher, and generate, compare, and save overview versions in Draft.")}</p></article>
            <article><span>04</span><h3>{text("边讨论，边完成稿件", "Discuss and refine your manuscript")}</h3><p>{text("直接修改完整正文，用章节对话或批量优化获得建议，再完善摘要、结论和书目，导出 Word / PDF。", "Edit the manuscript directly, use chapter dialogue or batch revision for suggestions, then refine the abstract, conclusion, and bibliography before Word / PDF export.")}</p></article>
          </div>
        </section>

        <section className="product-section workflow-showcase" id="workflow">
          <header className="product-section-heading split">
            <div><p className="eyebrow">{text("七阶段核心工作流", "Seven-stage core workflow")}</p><h2>{text("从选好论文，到交付完整稿件。", "From selecting papers to delivering your manuscript.")}</h2></div>
            <p>{text("可以在任意阶段暂停、审核或返回修订；系统根据当前产物版本判断后续内容是否仍然有效。", "Pause, review, or revise at any stage while artifact versions determine whether downstream content remains current.")}</p>
          </header>
          <div className="workflow-stage-grid">
            {workflowStages.map(([stage, zhTitle, enTitle, zhBody, enBody]) => (
              <article key={stage}><span>{stage}</span><h3>{text(zhTitle, enTitle)}</h3><p>{text(zhBody, enBody)}</p></article>
            ))}
          </div>
        </section>

        <section className="product-section governance-section" id="governance">
          <div>
            <p className="eyebrow">{text("可信与隔离", "Trust and isolation")}</p>
            <h2>{text("面向真实项目，而不是一次性演示。", "Designed for real projects, not one-off demos.")}</h2>
            <p>{text("文献、项目、任务与用量按账号隔离。已有成果保留版本，修改上游内容时提示哪些结果需要更新；服务配置由后台统一管理，你只需选择可用模型，专注研究与写作。", "Papers, projects, tasks, and usage are isolated by account. Artifacts keep their versions, and upstream changes flag results that need updating. Service configuration stays in the administration area; select an available model and focus on research and writing.")}</p>
          </div>
          <ul>
            <li><strong>{text("持久任务状态", "Persistent job state")}</strong><span>{text("已提交的后台任务持续执行，返回后可查看进度和成果", "Submitted background tasks continue; return to view progress and results")}</span></li>
            <li><strong>{text("服务端模型网关", "Server-side model gateway")}</strong><span>{text("按项目锁定模型、记录Token和实际结算成本", "Lock models per project and track tokens and settled usage costs")}</span></li>
            <li><strong>{text("修改由你决定", "You decide what to keep")}</strong><span>{text("比较修改候选、核对引用与配图，再采用满意的版本", "Compare revisions, check citations and figures, and keep the version you prefer")}</span></li>
          </ul>
        </section>

        <section className="product-final-cta">
          <div><p className="eyebrow">Review Writer</p><h2>{text("把时间留给化学问题，让综述写作更有条理。", "Spend more time on chemistry, with a clearer writing workflow.")}</h2></div>
          <Link className="button button-primary product-cta" to={workspaceHref}>{identity ? text("继续当前工作", "Continue your work") : text("登录工作台", "Sign in to workspace")}</Link>
        </section>
      </main>
      <footer className="product-footer"><span>Review Writer</span><p>{text("证据驱动的化学综述工作台", "Evidence-grounded chemistry review workspace")}</p></footer>
    </div>
  );
}
