import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GuidePage from "./page";

describe("Guide", () => {
  it("gives the workshop order the bot actually ships with", () => {
    render(<GuidePage />);
    expect(screen.getByRole("heading", { name: "Economy first" })).toBeInTheDocument();
    // Two paragraphs legitimately name Coins/Kill (the "what's already
    // unlocked" section and this workshop-order bullet), so a single-match
    // getByText throws here - assert at least one match instead.
    expect(screen.getAllByText(/Coins\/Kill/).length).toBeGreaterThanOrEqual(1);
  });

  it("directs account-specific questions to observed evidence", () => {
    render(<GuidePage />);
    expect(screen.getByRole("link", { name: /account inspector/i })).toHaveAttribute("href", "/account/");
    expect(screen.queryByText("2026-09-03")).toBeNull();
  });

  it("states what the bot will not do and why", () => {
    render(<GuidePage />);
    const limits = screen.getByRole("region", { name: /what the bot will not do/i });
    expect(limits).toHaveTextContent(/lab slots/i);
    expect(limits).toHaveTextContent(/ultimate weapon/i);
  });

  it("cites a source for every section", () => {
    render(<GuidePage />);
    const links = screen.getAllByRole("link", { name: /tower-hub/i });
    expect(links.length).toBeGreaterThanOrEqual(4);
  });
});
