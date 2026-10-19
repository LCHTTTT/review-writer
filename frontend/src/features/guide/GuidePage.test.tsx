import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { apiRequest } from "../../api/client";
import { AppShell } from "../../components/AppShell";
import { usePreferences } from "../../state/preferences";

vi.mock("../../api/client", async original => ({ ...await original<object>(), apiRequest: vi.fn() }));

beforeEach(() => {
  usePreferences.getState().setLanguage("en");
  vi.mocked(apiRequest).mockResolvedValue({ enabled: true });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); localStorage.clear(); });

it.each(["/draft?project=p", "/conversation?project=p"])("keeps guide, home and mode switching available at %s", async path => {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter initialEntries={[path]}>
      <AppShell identity={{ user_id: "u", email: "reader@example.com", display_name: "Reader", roles: ["member"], permissions: [] }}
        authConfig={{ enabled: true, registration_enabled: true, password_reset_enabled: false, password_reset_expiry_minutes: 30, password_min_length: 10 }}>
        <main>Existing content</main>
      </AppShell>
    </MemoryRouter>
  </QueryClientProvider>);
  expect(screen.getByRole("link", { name: "Guide" })).toHaveAttribute("href", "/guide?project=p");
  expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
  await waitFor(() => expect(screen.getByRole("link", { name: path.startsWith("/conversation") ? "Stage mode" : "Conversation mode" })).toBeInTheDocument());
  expect(screen.queryByRole("navigation", { name: "Review workflow" }) !== null).toBe(!path.startsWith("/conversation"));
  expect(screen.getByText("Existing content")).toBeVisible();
});
