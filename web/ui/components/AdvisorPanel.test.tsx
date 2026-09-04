import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdvisorPanel } from "./AdvisorPanel";
import type {
  AdvisorDraftResult,
  AdvisorSnapshot,
  Strategy,
} from "@/lib/types";

const api = vi.hoisted(() => ({
  fetchAdvisor: vi.fn(),
  importAdvisor: vi.fn(),
  stageAdvisor: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
const draft = {
  name: "default",
  shopping: { workshop: [], armed: false, enabled: false, coin_budget: 0 },
} as unknown as Strategy;
const empty = (profile = "default"): AdvisorSnapshot => ({
  profile,
  import_id: null,
  imported_at: null,
  source: null,
  missing_inputs: [],
  stale: false,
  recommendations: [],
});
const snapshot = (): AdvisorSnapshot => ({
  profile: "default",
  import_id: "revision-1",
  imported_at: Date.now() / 1000,
  source: {
    name: "Effective Paths",
    version: "v1",
    account_name: "My tower",
    exported_at: Date.now() / 1000,
    account_snapshot_at: Date.now() / 1000,
  },
  missing_inputs: [],
  stale: false,
  recommendations: [
    {
      id: "health",
      path: "health",
      system: "workshop",
      upgrade: "Health",
      upgrade_id: "health",
      current_value: 20,
      target_value: 40,
      value_kind: "stat",
      cost: 50,
      currency: "coins",
      benefit: 12.5,
      can_stage: true,
      blocked_reason: null,
    },
    {
      id: "lab",
      path: "damage",
      system: "lab",
      upgrade: "Damage lab",
      current_value: 2,
      target_value: 3,
      value_kind: "level",
      cost: null,
      currency: "time",
      benefit: null,
      can_stage: false,
      blocked_reason: "Labs cannot be staged",
    },
  ],
});
beforeEach(() => {
  vi.resetAllMocks();
  api.fetchAdvisor.mockResolvedValue(snapshot());
  api.importAdvisor.mockResolvedValue(snapshot());
  api.stageAdvisor.mockResolvedValue({
    added: true,
    message: "Added Health to the draft",
    draft: {
      ...draft,
      shopping: {
        ...draft.shopping,
        workshop: [
          { name: "Health", category: "DEFENSE", enabled: true, target: 40 },
        ],
      },
    },
  });
});

describe("AdvisorPanel", () => {
  it("does not apply a draft while the parent is saving or switching strategy", async () => {
    let resolve!: (value: AdvisorDraftResult) => void;
    api.stageAdvisor.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const onChange = vi.fn();
    const view = render(
      <AdvisorPanel profile="default" value={draft} onChange={onChange} />,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Add Health to Workshop draft",
      }),
    );
    view.rerender(
      <AdvisorPanel
        profile="default"
        value={draft}
        onChange={onChange}
        disabled
      />,
    );
    await act(async () =>
      resolve({ added: true, message: "Old response", draft }),
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(
      await screen.findByText(/strategy operation is in progress/),
    ).toBeTruthy();
  });
  it("loads a CSV file for review before importing its exact content", async () => {
    render(<AdvisorPanel profile="default" value={draft} onChange={vi.fn()} />);
    await screen.findByText("My tower");
    const csv = "id,path\nhealth,health";
    const file = new File([csv], "recommendations.csv", { type: "text/csv" });
    Object.defineProperty(file, "text", { value: () => Promise.resolve(csv) });
    fireEvent.change(screen.getByLabelText("Normalized export file"), {
      target: { files: [file] },
    });
    await waitFor(() =>
      expect(screen.getByLabelText("Normalized export content")).toHaveValue(
        csv,
      ),
    );
    expect(screen.getByLabelText("Import format")).toHaveValue("csv");
    expect(api.importAdvisor).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Import recommendations" }),
    );
    await waitFor(() =>
      expect(api.importAdvisor).toHaveBeenCalledWith({
        profile: "default",
        filename: "recommendations.csv",
        content: csv,
      }),
    );
  });
  it("keeps existing duplicate rules unchanged when the server declines to add one", async () => {
    const onChange = vi.fn();
    api.stageAdvisor.mockResolvedValue({
      draft,
      added: false,
      message: "Health is already in the plan; existing target retained",
    });
    render(
      <AdvisorPanel profile="default" value={draft} onChange={onChange} />,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Add Health to Workshop draft",
      }),
    );
    await screen.findByText(/existing target retained/);
    expect(onChange).not.toHaveBeenCalled();
  });
  it("ignores a late snapshot from a previously selected profile", async () => {
    let resolve!: (value: AdvisorSnapshot) => void;
    api.fetchAdvisor.mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const view = render(
      <AdvisorPanel profile="default" value={draft} onChange={vi.fn()} />,
    );
    api.fetchAdvisor.mockResolvedValueOnce(empty("other"));
    view.rerender(
      <AdvisorPanel
        profile="other"
        value={{ ...draft, name: "other" }}
        onChange={vi.fn()}
      />,
    );
    await screen.findByText(/No recommendations imported/);
    await act(async () => resolve(snapshot()));
    expect(screen.queryByText("My tower")).toBeNull();
  });
  it("shows an empty normalized-import workflow without inferring example recommendations", async () => {
    api.fetchAdvisor.mockResolvedValue(empty());
    render(<AdvisorPanel profile="default" value={draft} onChange={vi.fn()} />);
    expect(await screen.findByText(/No recommendations imported/)).toBeTruthy();
    expect(screen.getByText(/Native Effective Paths worksheets/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Example JSON/ })).toHaveAttribute(
      "href",
      "/api/advisor/template.json",
    );
    expect(screen.queryByRole("button", { name: /Add Health/ })).toBeNull();
  });
  it("shows provenance, filters paths and leaves unsupported recommendations visible with reasons", async () => {
    render(<AdvisorPanel profile="default" value={draft} onChange={vi.fn()} />);
    expect(await screen.findByText("My tower")).toBeTruthy();
    expect(screen.getByText("Labs cannot be staged")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Damage" }));
    expect(screen.getByText("Damage lab")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Add Health to Workshop draft" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Add Damage lab to Workshop draft" }),
    ).toBeDisabled();
  });
  it("imports pasted normalized data only on explicit import and preserves existing data on failure", async () => {
    const onChange = vi.fn();
    api.importAdvisor.mockRejectedValue(
      new Error("Unsupported schema version"),
    );
    render(
      <AdvisorPanel profile="default" value={draft} onChange={onChange} />,
    );
    await screen.findByText("My tower");
    fireEvent.change(screen.getByLabelText("Normalized export content"), {
      target: { value: '{"schema_version":2}' },
    });
    expect(api.importAdvisor).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Import recommendations" }),
    );
    await screen.findByText("Unsupported schema version");
    expect(api.importAdvisor).toHaveBeenCalledWith({
      profile: "default",
      filename: "advisor.json",
      content: '{"schema_version":2}',
    });
    expect(screen.getByText("My tower")).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();
  });
  it("stages the returned draft without saving or arming", async () => {
    const onChange = vi.fn();
    render(
      <AdvisorPanel profile="default" value={draft} onChange={onChange} />,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Add Health to Workshop draft",
      }),
    );
    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({
          shopping: expect.objectContaining({
            armed: false,
            enabled: false,
            coin_budget: 0,
          }),
        }),
      ),
    );
    expect(api.stageAdvisor).toHaveBeenCalledWith({
      profile: "default",
      import_id: "revision-1",
      recommendation_id: "health",
      draft,
    });
  });
  it("blocks stale and incomplete account snapshots even if an old row says it can stage", async () => {
    api.fetchAdvisor.mockResolvedValue({
      ...snapshot(),
      stale: true,
      missing_inputs: ["Modules"],
    });
    render(<AdvisorPanel profile="default" value={draft} onChange={vi.fn()} />);
    expect(await screen.findByText(/Modules/)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Add Health to Workshop draft" }),
    ).toBeDisabled();
  });
  it("discards a late draft response after a profile switch", async () => {
    let resolve!: (value: AdvisorDraftResult) => void;
    api.stageAdvisor.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const onChange = vi.fn();
    const view = render(
      <AdvisorPanel profile="default" value={draft} onChange={onChange} />,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Add Health to Workshop draft",
      }),
    );
    api.fetchAdvisor.mockResolvedValue(empty("other"));
    view.rerender(
      <AdvisorPanel
        profile="other"
        value={{ ...draft, name: "other" }}
        onChange={onChange}
      />,
    );
    await act(async () =>
      resolve({ added: true, message: "Old response", draft }),
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByText("Old response")).toBeNull();
  });
  it("does not overwrite a draft edited while staging was pending", async () => {
    let resolve!: (value: AdvisorDraftResult) => void;
    api.stageAdvisor.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const onChange = vi.fn();
    const view = render(
      <AdvisorPanel profile="default" value={draft} onChange={onChange} />,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Add Health to Workshop draft",
      }),
    );
    view.rerender(
      <AdvisorPanel
        profile="default"
        value={{ ...draft, interval: 9 }}
        onChange={onChange}
      />,
    );
    await act(async () =>
      resolve({ added: true, message: "Old response", draft }),
    );
    expect(onChange).not.toHaveBeenCalled();
    expect(await screen.findByText(/draft changed/)).toBeTruthy();
  });
  it("ignores late imports after unmount", async () => {
    let resolve!: (value: AdvisorSnapshot) => void;
    api.importAdvisor.mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const onChange = vi.fn();
    const view = render(
      <AdvisorPanel profile="default" value={draft} onChange={onChange} />,
    );
    await screen.findByText("My tower");
    fireEvent.change(screen.getByLabelText("Normalized export content"), {
      target: { value: "{}" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Import recommendations" }),
    );
    view.unmount();
    await act(async () => resolve(snapshot()));
    expect(onChange).not.toHaveBeenCalled();
  });
});
