import type { QueryClient } from "@tanstack/react-query";

const projectQueries = new Set([
  "discovery", "discovery-job-state", "planning", "planning-matrix", "sections", "figures", "images",
  "draft", "draft-dialogue", "draft-overview", "draft-publication", "final", "final-figure-review",
  "project-papers", "conversation-draft-edit", "conversation-versions", "figure-review",
]);

export async function refreshProjectResults(client: QueryClient, projectId: string) {
  await Promise.all([
    client.invalidateQueries({ predicate: query => projectQueries.has(String(query.queryKey[0])) && query.queryKey[1] === projectId }),
    client.invalidateQueries({ queryKey: ["projects"] }),
    client.invalidateQueries({ queryKey: ["library"] }),
  ]);
}
