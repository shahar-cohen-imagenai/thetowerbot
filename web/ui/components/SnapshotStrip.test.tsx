import { render, screen } from "@testing-library/react";
import { describe as group, expect, it } from "vitest";
import { SnapshotStrip } from "./SnapshotStrip";

group("SnapshotStrip", () => {
  it("renders placeholder text when there are no unknown screens", () => {
    render(<SnapshotStrip shots={[]} />);

    expect(screen.getByText(/No unknown screens captured/)).toBeDefined();
  });
});
