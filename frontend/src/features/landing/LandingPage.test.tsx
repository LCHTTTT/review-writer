import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it } from "vitest";
import { usePreferences } from "../../state/preferences";
import { LandingPage } from "./LandingPage";

afterEach(cleanup);

it("presents the chemistry workflow in both languages without treating the example as live data", () => {
  usePreferences.getState().setLanguage("zh-CN");
  render(<MemoryRouter><LandingPage identity={null} authConfig={{ enabled: true, registration_enabled: true,
    password_reset_enabled: false, password_reset_expiry_minutes: 30, password_min_length: 10 }} /></MemoryRouter>);
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("从化学文献到综述，读得清楚，写得有据。");
  expect(screen.getByText("项目示意")).toBeVisible();
  expect(screen.getByText("以化学综述为重点，也支持通用学术写作。")).toBeVisible();
  expect(document.querySelector(".hero-chemistry-pattern")).toHaveAttribute("aria-hidden", "true");
  expect(screen.getByRole("link", { name: "登录后开始" })).toHaveAttribute("href", "/login");
  expect(screen.getByText(/在初稿中生成、比较并保存总览图版本/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "EN" }));
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("From chemistry papers to a clear, evidence-grounded review.");
  expect(screen.getByText("Example project")).toBeVisible();
  expect(screen.getByText(/Submitted background tasks continue/)).toBeVisible();
});
