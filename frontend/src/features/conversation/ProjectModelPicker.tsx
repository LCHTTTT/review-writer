import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiRequest, jsonBody } from "../../api/client";
import { LocalizedError } from "../../components/LocalizedError";
import { ModelOptions } from "../../components/ModelOptions";
import { useUiText } from "../../i18n/useUiText";

export function ProjectModelPicker({ projectId, current, refresh, onPendingChange, disabled = false }: { projectId: string; current: string; refresh: () => Promise<unknown>; onPendingChange?: (value: boolean) => void; disabled?: boolean }) {
  const { text } = useUiText();
  const [choice, setChoice] = useState<string | null>(null);
  const save = useMutation({ mutationFn: (tier: string) => apiRequest(`/api/v1/projects/${encodeURIComponent(projectId)}/model-tier`,
      { method: "PATCH", ...jsonBody({ model_tier: tier }) }),
    onSuccess: async () => { setChoice(null); await refresh(); },
    onError: () => { setChoice(null); },
    onSettled: () => onPendingChange?.(false) });
  return <div className="conversation-model-picker"><label className="sr-only" htmlFor="conversation-model">{text("项目文本模型", "Project text model")}</label>
    <select id="conversation-model" value={choice ?? current} disabled={disabled || save.isPending} onChange={e => {
      const tier = e.target.value;
      if (!tier || tier === current) return;
      setChoice(tier); onPendingChange?.(true); save.mutate(tier);
    }}>
      <option value="" disabled>{text("服务器默认", "Server default")}</option><ModelOptions selected={choice ?? current} />
    </select>
    {save.isPending ? <span role="status">{text("切换中…", "Switching…")}</span> : null}
    {save.error ? <LocalizedError error={save.error} /> : null}
  </div>;
}
