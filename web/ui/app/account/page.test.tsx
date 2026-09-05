import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AccountPage from "./page";
const { fetchAccount, fetchConcepts } = vi.hoisted(() => ({ fetchAccount: vi.fn(), fetchConcepts: vi.fn() }));
vi.mock("@/lib/api", () => ({ fetchAccount, fetchConcepts }));
const concept = { concept_id: "stats.damage", name: "Damage", domain: "stats", kind: "stat", unit: null, execution_scopes: ["workshop"], prerequisites: null, unlocks: [], rule_verified: false };
const unknown = { revision_id: null, registry_version: "1", account_id: null, game_version: null, workshop_stats: [], workshop_levels: null, lab_levels: null, inventory: null, effective_account_stats: null, unlocks: null, settings: null };
const snapshot = { persistence_available: true, error: null, errors: { account: null, run: null }, revision: null, unknown_state: unknown };
const fact = { concept_id: "stats.damage", value: 0, status: "verified", evidence: { observed_at: 1, confidence: .96, raw_name: "Damage", raw_value: "0.00", rect: [1, 2, 3, 4], frame_width: 400, frame_height: 800, frame_digest: "abc123", frame_ref: null } };
beforeEach(() => {
  fetchAccount.mockReset().mockResolvedValue(snapshot);
  fetchConcepts.mockReset().mockResolvedValue({ registry_version: "1", concepts: [concept, { ...concept, concept_id: "cards.damage", name: "Damage card", domain: "cards", kind: "card", execution_scopes: [] }] });
});
describe("Account inspector", () => {
  it("distinguishes unscanned values and unsupported readers without inventing levels or locks", async () => {
    render(<AccountPage />);
    await screen.findByText("Unknown account");
    expect(screen.getByText("stats · Not yet scanned / no verified value")).toBeDefined();
    expect(screen.getByText("cards · Unknown · reader unavailable")).toBeDefined();
    expect(screen.getAllByText("Unknown · reader unavailable in this API")).toHaveLength(6);
    expect(screen.getAllByText("Prerequisites: Unknown in catalog.")).toHaveLength(2);
  });
  it("traces zero values to stale evidence without inventing a source image", async () => {
    fetchAccount.mockResolvedValue({ ...snapshot, revision: { ...unknown, revision_id: 2, workshop_stats: [fact] } });
    render(<AccountPage />);
    await screen.findByText("0");
    fireEvent.click(screen.getAllByText("Damage")[0]);
    expect(screen.getByText("Damage → 0.00")).toBeDefined();
    expect(screen.getByText("abc123")).toBeDefined();
    expect(screen.getByText("verified · Stale saved evidence")).toBeDefined();
    expect(screen.getByText("Image not retained; digest and OCR evidence only.")).toBeDefined();
    expect(screen.queryByRole("img")).toBeNull();
  });
  it("retains observations through metadata outages and failed refreshes", async () => {
    fetchAccount.mockResolvedValue({ ...snapshot, revision: { ...unknown, revision_id: 2, workshop_stats: [fact] } });
    fetchConcepts.mockRejectedValue(new Error("metadata offline"));
    render(<AccountPage />);
    await screen.findByText("abc123");
    expect(screen.getByRole("alert").textContent).toContain("metadata");
    fetchAccount.mockRejectedValue(new Error("503"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText(/Could not load account: 503/);
    expect(screen.getByText("abc123")).toBeDefined();
  });
  it("does not infer unscanned data when the initial account request fails", async () => {
    fetchAccount.mockRejectedValue(new Error("503"));
    render(<AccountPage />);
    await screen.findByText(/Could not load account: 503/);
    expect(screen.getByText("account state unavailable")).toBeDefined();
    expect(screen.getByText("stats · Unknown · account state unavailable")).toBeDefined();
    expect(screen.queryByText("no saved revision")).toBeNull();
    expect(screen.queryByText("stats · Not yet scanned / no verified value")).toBeNull();
  });
  it("does not report zero observations when account storage could not restore a revision", async () => {
    fetchAccount.mockResolvedValue({ ...snapshot, error: "corrupt storage", errors: { account: "corrupt storage", run: null } });
    render(<AccountPage />);
    await screen.findByText("Unavailable");
    expect(screen.getByText("account state unavailable")).toBeDefined();
    expect(screen.getByText("stats · Unknown · account state unavailable")).toBeDefined();
    expect(screen.queryByText("0")).toBeNull();
    expect(screen.queryByText("no saved revision")).toBeNull();
    expect(screen.queryByText("Not yet scanned / no verified values")).toBeNull();
  });
  it("filters catalog inventory domains", async () => {
    render(<AccountPage />);
    await screen.findByText("Damage card");
    fireEvent.change(screen.getByLabelText("Domain"), { target: { value: "cards" } });
    expect(screen.queryByText("Damage")).toBeNull();
    expect(screen.getByText("Damage card")).toBeDefined();
  });
  it("renders screen observations separately and retains them after failed refresh", async () => {
    fetchAccount.mockResolvedValue({ ...snapshot, screen_readings: {
      current_screen_id: "account.settings", error: null, readings: [{
        screen_id: "account.settings", observed_at: 100, frame_width: 1080, frame_height: 2400,
        frame_digest: "settings-frame", tiers: [], fields: [{
          key: "game_version", label: "Game version", raw_value: "v29.0.1", status: "observed", confidence: .99, rect: [824, 1908, 124, 38],
        }],
      }],
    } });
    render(<AccountPage />);
    await screen.findByText("v29.0.1");
    expect(screen.getByText("no saved revision")).toBeDefined();
    expect(screen.getByText("Unknown account")).toBeDefined();
    expect(screen.getByText("Visible at last refresh")).toBeDefined();
    fetchAccount.mockRejectedValue(new Error("503"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText(/Could not load account: 503/);
    expect(screen.getByText("v29.0.1")).toBeDefined();
    expect(screen.getByText("settings-frame")).toBeDefined();
  });
});
