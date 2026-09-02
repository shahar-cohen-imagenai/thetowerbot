import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProfileBar } from "./ProfileBar";
import type { StrategyList } from "@/lib/types";

const handlers = {
  onSelect: vi.fn(),
  onActivate: vi.fn(),
  onDuplicate: vi.fn(),
  onDelete: vi.fn(),
  onSave: vi.fn(),
  onRevert: vi.fn(),
};

describe("ProfileBar", () => {
  it("disables Delete for the active profile", () => {
    const list: StrategyList = { active: "default", names: ["default", "crit"] };
    render(<ProfileBar list={list} current="default" dirty={false} {...handlers} />);
    expect(screen.getByText("Delete").hasAttribute("disabled")).toBe(true);
  });

  it("disables Delete when it is the only profile, even if not active", () => {
    // Contrived (the active one is always in the list), but the rule is
    // "either condition alone is enough" and this is the only way to prove
    // the `||` isn't secretly a `&&`.
    const list: StrategyList = { active: "default", names: ["default"] };
    render(<ProfileBar list={list} current="default" dirty={false} {...handlers} />);
    expect(screen.getByText("Delete").hasAttribute("disabled")).toBe(true);
  });

  it("enables Delete for an inactive profile when another one exists", () => {
    const list: StrategyList = { active: "default", names: ["default", "crit"] };
    render(<ProfileBar list={list} current="crit" dirty={false} {...handlers} />);
    expect(screen.getByText("Delete").hasAttribute("disabled")).toBe(false);
  });

  it("disables Activate when the selected profile is already active", () => {
    const list: StrategyList = { active: "default", names: ["default", "crit"] };
    render(<ProfileBar list={list} current="default" dirty={false} {...handlers} />);
    expect(screen.getByText("Active").hasAttribute("disabled")).toBe(true);
  });

  it("enables Activate for a selected profile that is not the active one", () => {
    const list: StrategyList = { active: "default", names: ["default", "crit"] };
    render(<ProfileBar list={list} current="crit" dirty={false} {...handlers} />);
    expect(screen.getByText("Activate").hasAttribute("disabled")).toBe(false);
  });
});
