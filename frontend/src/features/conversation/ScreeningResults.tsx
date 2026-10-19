import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiRequest } from "../../api/client";
import { LocalizedError } from "../../components/LocalizedError";
import { useUiText } from "../../i18n/useUiText";
import { unifyDiscoveryRows, type DiscoveryPayload } from "../discovery/model/discoveryModel";
import { buildMatrixRecommendation } from "../discovery/model/matrixRecommendation";

/** Display the exact saved stage-02 result; no new chat screening/model call. */
export function ScreeningResults({ artifactId, projectId }: {artifactId: string; projectId: string}) {
  const { text } = useUiText();
  const query = useQuery({queryKey: ["conversation-screening", projectId, artifactId],
    queryFn: () => apiRequest<DiscoveryPayload>(`/api/v1/artifacts/${encodeURIComponent(artifactId)}/content`), staleTime: Infinity});
  if (query.error) return <p role="alert"><LocalizedError error={query.error} /></p>;
  if (!query.data) return <p role="status">{text("正在读取已保存的筛选结果…", "Loading the saved screening result…")}</p>;
  const groups = query.data.results || [];
  const recommendation = buildMatrixRecommendation(groups);
  const entries = unifyDiscoveryRows(groups);
  const local = entries.filter(entry => entry.kind === "local" && !recommendation.excludedIds.has(String(entry.row.paper_id || "")));
  const recommended = local.filter(entry => recommendation.recommendedIds.has(String(entry.row.paper_id || "")));
  const review = local.filter(entry => !recommendation.recommendedIds.has(String(entry.row.paper_id || "")));
  const online = entries.filter(entry => entry.kind === "web").length;
  return <section className="conversation-screening-results"><h4>{text("检索与筛选结果", "Retrieval & screening results")}</h4>
    <p>{text(`推荐 ${recommended.length} 篇 · 待核对 ${review.length} 篇 · 联网候选 ${online} 篇`, `${recommended.length} recommended · ${review.length} to review · ${online} online candidates`)}</p>
    {[...recommended, ...review].slice(0, 6).map(({row}) => <article key={row.paper_id}><strong>{row.title || row.paper_id}</strong>
      <small>{row.paper_id} · {recommendation.recommendedIds.has(String(row.paper_id)) ? text("筛选推荐", "Screening recommendation") : text("需核对相关性", "Review relevance")}</small>
      {typeof row.reason === "string" && row.reason ? <p>{row.reason}</p> : null}</article>)}
    {!local.length ? <p>{text("本次没有可展示的本地候选。联网候选须获取全文并入库，才能用于后续写作。", "No local candidates to display. Online candidates need full text in your library before writing.")}</p> : null}
    <p className="muted">{text("以上是第二阶段的筛选建议，不代表已经采用；实际纳入名单以左侧“本项目文献”为准。可说“全部纳入推荐结果”，接续文献分析。", "These are stage-02 recommendations, not proof of adoption; Project papers shows the actual included list. Say ‘include all recommended papers’ to continue paper analysis.")}</p>
    <Link className="button button-secondary" to={`/discovery?project=${encodeURIComponent(projectId)}`}>{text("查看当前完整筛选", "View current full screening")}</Link></section>;
}
