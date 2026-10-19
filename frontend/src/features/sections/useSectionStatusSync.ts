import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "../../api/client";

/** Discover work started in another window without repeatedly downloading the manuscript. */
export function useSectionStatusSync(projectId: string, enabled: boolean, resetJob: () => void) {
  const client = useQueryClient();
  const previous = useRef("");
  const status = useQuery({
    queryKey: ["section-status", projectId],
    queryFn: () => apiRequest<{ artifact_ids: Record<string, string>; job_id: string; job_status: string }>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/sections/status`),
    enabled: Boolean(projectId) && enabled,
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  });
  useEffect(() => {
    if (!status.data || !enabled) return;
    const signature = JSON.stringify([projectId, status.data]);
    if (previous.current === signature) return;
    previous.current = signature;
    resetJob();
    void client.invalidateQueries({ queryKey: ["sections", projectId] });
  }, [client, enabled, projectId, resetJob, status.data]);
}
