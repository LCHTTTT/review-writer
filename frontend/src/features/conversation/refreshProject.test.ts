import { QueryClient } from "@tanstack/react-query";
import { expect, test } from "vitest";
import { refreshProjectResults } from "./refreshProject";

test("completion refreshes original stages only for this project", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity } } });
  const names = ["discovery", "discovery-job-state", "planning", "sections", "figure-review", "figures", "draft", "draft-dialogue", "draft-overview", "draft-publication", "final", "project-papers", "conversation-versions"];
  for (const name of names) {
    client.setQueryData([name, "current"], { revision: 1 });
    client.setQueryData([name, "other"], { revision: 1 });
  }
  client.setQueryData(["me"], { user_id: "user" });
  await refreshProjectResults(client, "current");
  for (const name of names) {
    expect(client.getQueryState([name, "current"])?.isInvalidated).toBe(true);
    expect(client.getQueryState([name, "other"])?.isInvalidated).toBe(false);
  }
  expect(client.getQueryState(["me"])?.isInvalidated).toBe(false);
  client.clear();
});
