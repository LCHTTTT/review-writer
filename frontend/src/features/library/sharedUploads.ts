import { useSyncExternalStore } from "react";
import { apiRequest, ApiError, newIdempotencyKey } from "../../api/client";
import type { Job, Principal } from "../../api/types";

export type SharedUpload = { id: string; batchId: string; name: string; status: "uploading" | "submitted" | "failed" | "uncertain"; jobId?: string; error?: string };
class UploadStore {
  rows: SharedUpload[] = [];
  listeners = new Set<() => void>();
  operations = new Map<string, Promise<Job>>();
  constructor(readonly owner: string) {
    try { this.rows = JSON.parse(localStorage.getItem(`rw-uploads:${owner}`) || "[]").map((row: SharedUpload) => row.status === "uploading" ? {...row, status: "uncertain", error: "连接已中断，请在文献库核对是否已接收，未自动重传。"} : row); } catch { /* optional browser storage */ }
  }
  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  snapshot = () => this.rows;
  update(row: SharedUpload) {
    this.rows = [...this.rows.filter(item => item.id !== row.id), row].slice(-100);
    try { localStorage.setItem(`rw-uploads:${this.owner}`, JSON.stringify(this.rows)); } catch { /* transfers do not depend on local storage */ }
    this.listeners.forEach(fn => fn());
  }
  upload(file: File, batchId = newIdempotencyKey(), key = newIdempotencyKey()): Promise<Job> {
    const existing = this.operations.get(key);
    if (existing) return existing;
    const row: SharedUpload = {id: key, batchId, name: file.webkitRelativePath || file.name, status: "uploading"};
    this.update(row);
    const operation = (async () => {
      try {
        const identity = await apiRequest<Principal>("/api/v1/me");
        if (!this.owner || identity.user_id !== this.owner) throw new Error("账号已切换，未向新账号上传文件。请在原账号重新选择文件。");
        const pdf = file.name.toLowerCase().endsWith(".pdf");
        if (!pdf && !/\.(txt|md|csv|json)$/i.test(file.name)) throw new Error("支持 PDF、UTF-8 TXT、Markdown、CSV、JSON。未上传不支持的文件。");
        if (file.size === 0 || file.size > (pdf ? 80 : 1) * 1024 * 1024) throw new Error(pdf ? "PDF 必须大于 0 且不超过 80 MB。" : "文本附件必须大于 0 且不超过 1 MB。");
        const endpoint = pdf ? `/api/v1/library/upload-jobs?filename=${encodeURIComponent(file.name)}&batch_id=${encodeURIComponent(batchId)}&display_name=${encodeURIComponent(row.name)}` : `/api/v1/conversation/attachments?filename=${encodeURIComponent(file.name)}`;
        const job = await apiRequest<Job>(endpoint, {method: "POST", headers: {"Content-Type": pdf ? "application/pdf" : "application/octet-stream", "Idempotency-Key": key}, body: file});
        this.update({...row, status: "submitted", jobId: job.id});
        return job;
      } catch (error) {
        const uncertain = !(error instanceof ApiError && error.status < 500) && !(error instanceof Error && /账号已切换|支持 PDF|必须大于/.test(error.message));
        this.update({...row, status: uncertain ? "uncertain" : "failed", error: error instanceof Error ? error.message : String(error)});
        throw error;
      }
    })();
    this.operations.set(key, operation);
    return operation;
  }
}
const stores = new Map<string, UploadStore>();
export function sharedUploads(owner: string) {
  let store = stores.get(owner); if (!store) { store = new UploadStore(owner); stores.set(owner, store); } return store;
}
const empty: SharedUpload[] = [];
const emptySubscribe = () => () => {};
export function useSharedUploads(owner: string | undefined) {
  const store = owner ? sharedUploads(owner) : null;
  return { store, rows: useSyncExternalStore(store?.subscribe || emptySubscribe, store?.snapshot || (() => empty)) };
}
