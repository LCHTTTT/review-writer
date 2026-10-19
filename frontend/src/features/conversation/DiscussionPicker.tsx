import {useMutation, useQuery} from "@tanstack/react-query";
import {useSearchParams} from "react-router-dom";
import {apiRequest, jsonBody} from "../../api/client";
import {useUiText} from "../../i18n/useUiText";
import {LocalizedError} from "../../components/LocalizedError";

type Discussion = {id: string; title: string; archived: boolean};
/** Compatibility access to existing histories, not a second workflow manager. */
export function DiscussionPicker({endpoint, sessionId}: {endpoint: string; sessionId: string}) {
  const {text} = useUiText();
  const [params, setParams] = useSearchParams();
  const query = useQuery({queryKey:["conversation-sessions",endpoint], queryFn:()=>apiRequest<{items:Discussion[]}>(`${endpoint}/sessions`)});
  const current = query.data?.items?.find(item=>item.id===sessionId);
  const restore = useMutation({mutationFn:()=>apiRequest(`${endpoint}/sessions/${sessionId}`,{method:"PATCH",...jsonBody({archived:false})}),onSuccess:()=>query.refetch()});
  if (!sessionId && !query.error && !query.data?.items?.length) return null;
  return <div className="conversation-discussions">
    <select aria-label={text("选择对话", "Choose conversation")} value={sessionId} onChange={event=>{
      const next = new URLSearchParams(params);
      if(event.target.value)next.set("session",event.target.value);else next.delete("session");
      setParams(next);
    }}>
      <option value="">{text("当前对话", "Current conversation")}</option>
      {query.data?.items?.map(item=><option key={item.id} value={item.id}>{item.archived ? text("[已归档] ", "[Archived] ") : ""}{item.title}</option>)}
    </select>
    {current?.archived ? <><small>{text("历史对话只读", "Read-only history")}</small><button className="button button-quiet" disabled={restore.isPending} onClick={()=>restore.mutate()}>{text("恢复对话", "Restore conversation")}</button></> : null}
    {query.error || restore.error ? <LocalizedError error={query.error || restore.error}/> : null}
  </div>;
}
