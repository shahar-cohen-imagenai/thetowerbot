import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GuidePage from "./page";

describe("Guide", () => {
  it("gives the workshop order the bot actually ships with", () => {
    render(<GuidePage />);
    expect(screen.getByText(/Unlock Cash Bonuses/)).toBeInTheDocument();
    expect(screen.getByText(/Coins\/Kill/)).toBeInTheDocument();
  });

  it("says where this account is, not just where the guides assume", () => {
    render(<GuidePage />);
    expect(screen.getByRole("heading", { name: /where this account is/i })).toBeInTheDocument();
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
