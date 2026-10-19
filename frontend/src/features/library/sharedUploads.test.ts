import {afterEach, expect, it, vi} from "vitest";
import {sharedUploads} from "./sharedUploads";
afterEach(() => vi.unstubAllGlobals());

it("deduplicates the exact in-flight operation and keeps a server receipt across subscribers", async () => {
  const fetch = vi.fn(async (input: string) => input === "/api/v1/me" ? Response.json({user_id: "owner-a"}) : Response.json({id: "job-a", status: "queued"}));
  vi.stubGlobal("fetch", fetch);
  const store = sharedUploads("owner-a");
  const file = new File(["text"], "notes.md");
  const first = store.upload(file, "batch", "stable-key");
  expect(store.upload(file, "batch", "stable-key")).toBe(first);
  expect((await first).id).toBe("job-a");
  expect(fetch.mock.calls.filter(([url]) => String(url).includes("/attachments"))).toHaveLength(1);
  expect(sharedUploads("owner-a").snapshot().find(row => row.id === "stable-key")?.jobId).toBe("job-a");
  expect(sharedUploads("different-owner").snapshot()).toEqual([]);
});

it("does not transfer a queued old-account file under the new account cookie", async () => {
  const fetch = vi.fn(async () => Response.json({user_id: "new-owner"}));
  vi.stubGlobal("fetch", fetch);
  await expect(sharedUploads("old-owner").upload(new File(["%PDF"], "paper.pdf"), "b", "switch-key")).rejects.toThrow("账号已切换");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("marks lost acknowledgement uncertain and does not silently replay it", async () => {
  const fetch = vi.fn(async (input: string) => { if (input === "/api/v1/me") return Response.json({user_id: "uncertain-owner"}); throw new TypeError("connection lost"); });
  vi.stubGlobal("fetch", fetch);
  const store = sharedUploads("uncertain-owner"), file = new File(["%PDF"], "paper.pdf");
  await expect(store.upload(file, "b", "lost")).rejects.toThrow("connection lost");
  await expect(store.upload(file, "b", "lost")).rejects.toThrow("connection lost");
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(store.snapshot()[0].status).toBe("uncertain");
});
