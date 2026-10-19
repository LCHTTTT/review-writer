const stagePaths = new Set(["/workspace", "/library", "/discovery", "/planning", "/sections", "/images", "/draft", "/final"]);
export function isStagePath(path: string) { return stagePaths.has(path); }
function key(userId: string, projectId: string) { return `rw-conversation-return:${userId}:${projectId}`; }
export function rememberStage(userId: string, projectId: string, path: string) {
  try {
    const url = new URL(path, window.location.origin);
    if (!isStagePath(url.pathname) || url.origin !== window.location.origin || (url.searchParams.get("project") || "") !== projectId) return;
    sessionStorage.setItem(key(userId, projectId), `${url.pathname}${url.search}${url.hash}`);
  } catch { /* A storage failure must not block navigation. */ }
}
export function stageReturnPath(userId: string, projectId: string) {
  try {
    const stored = sessionStorage.getItem(key(userId, projectId));
    if (stored) {
      const url = new URL(stored, window.location.origin);
      if (url.origin === window.location.origin && isStagePath(url.pathname) && (url.searchParams.get("project") || "") === projectId)
        return `${url.pathname}${url.search}${url.hash}`;
    }
  } catch { /* Fall back to a deterministic, project-scoped entry. */ }
  return projectId ? `/library?project=${encodeURIComponent(projectId)}` : "/workspace";
}
