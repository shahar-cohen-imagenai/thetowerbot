import { fireEvent, render, screen } from "@testing-library/react";
import { describe as group, expect, it } from "vitest";
import { SnapshotStrip } from "./SnapshotStrip";
import type { Snapshot } from "@/lib/types";

const shots: Snapshot[] = [
  { name: "1788637355807491000.png", ts: 1788637355, url: "/api/unknown/1788637355807491000.png" },
  { name: "1788637324333159000.png", ts: 1788637324, url: "/api/unknown/1788637324333159000.png" },
];

const open = () => fireEvent.click(screen.getByRole("button", { name: /1788637355807491000/ }));
const opened = () => screen.queryByRole("dialog");

group("SnapshotStrip", () => {
  it("renders placeholder text when there are no unknown screens", () => {
    render(<SnapshotStrip shots={[]} />);

    expect(screen.getByText(/No unknown screens captured/)).toBeDefined();
  });

  it("shows no lightbox until a thumbnail is clicked", () => {
    render(<SnapshotStrip shots={shots} />);

    expect(opened()).toBeNull();
  });

  it("opens the clicked snapshot full size", () => {
    render(<SnapshotStrip shots={shots} />);

    open();

    const full = screen.getByRole("dialog").querySelector("img")!;
    expect(full.getAttribute("src")).toBe(shots[0].url);
  });

  // Three ways out, tested separately because they fail separately: the
  // button is the obvious one, the backdrop is the reflex, and Escape is
  // the one a keyboard reaches. A lightbox over a full-bleed screenshot
  // with only one of them is a trap.
  it("closes on the close button", () => {
    render(<SnapshotStrip shots={shots} />);
    open();

    fireEvent.click(screen.getByRole("button", { name: /close/i }));

    expect(opened()).toBeNull();
  });

  it("closes on a backdrop click", () => {
    render(<SnapshotStrip shots={shots} />);
    open();

    fireEvent.click(screen.getByRole("dialog"));

    expect(opened()).toBeNull();
  });

  it("closes on Escape", () => {
    render(<SnapshotStrip shots={shots} />);
    open();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(opened()).toBeNull();
  });

  it("keeps the lightbox open when the image itself is clicked", () => {
    render(<SnapshotStrip shots={shots} />);
    open();

    fireEvent.click(screen.getByRole("dialog").querySelector("img")!);

    expect(opened()).not.toBeNull();
  });
});
