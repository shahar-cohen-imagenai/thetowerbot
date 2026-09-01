import { fireEvent, render, screen } from "@testing-library/react";
import { describe as group, expect, it } from "vitest";
import { EventFeed } from "./EventFeed";
import type { BotEvent } from "@/lib/types";

const events: BotEvent[] = [
  { type: "RunStarted", seq: 1, ts: 0, run_id: 1 },
  { type: "Tapped", seq: 2, ts: 1, action: "Damage", x: 1, y: 2, score: 0.9, price: null, wallet: null },
  { type: "Navigated", seq: 3, ts: 2, target: "SHOP" },
];

/** Stub the scroll geometry jsdom never lays out, with a real backing store
 * so both the test and the component's own `node.scrollTop = ...` land on
 * the same value. */
function stubScrollGeometry(
  node: HTMLElement,
  { scrollHeight, clientHeight, scrollTop }: { scrollHeight: number; clientHeight: number; scrollTop: number },
) {
  Object.defineProperty(node, "scrollHeight", { value: scrollHeight, configurable: true });
  Object.defineProperty(node, "clientHeight", { value: clientHeight, configurable: true });
  let top = scrollTop;
  Object.defineProperty(node, "scrollTop", {
    get: () => top,
    set: (v: number) => { top = v; },
    configurable: true,
  });
}

group("EventFeed", () => {
  it("narrows the rendered rows to the filter substring", () => {
    render(<EventFeed events={events} />);

    expect(screen.getByText(/RUN/)).toBeDefined();
    expect(screen.getByText(/TAP/)).toBeDefined();
    expect(screen.getByText(/NAV/)).toBeDefined();

    fireEvent.change(screen.getByPlaceholderText("filter…"), { target: { value: "damage" } });

    expect(screen.getByText(/TAP/)).toBeDefined();
    expect(screen.queryByText(/RUN/)).toBeNull();
    expect(screen.queryByText(/NAV/)).toBeNull();
  });

  it("auto-scrolls to the bottom only when the reader was already pinned there", () => {
    const { container, rerender } = render(<EventFeed events={events} />);
    const box = container.querySelector(".overflow-y-auto") as HTMLDivElement;
    expect(box).toBeTruthy();

    // Case: pinned at the bottom -> new events should pull the view down.
    stubScrollGeometry(box, { scrollHeight: 1000, clientHeight: 100, scrollTop: 900 });
    fireEvent.scroll(box);
    rerender(<EventFeed events={[...events, { type: "Navigated", seq: 4, ts: 3, target: "MENU" }]} />);
    expect(box.scrollTop).toBe(1000);

    // Case: scrolled up to read history -> new events must not yank the view.
    stubScrollGeometry(box, { scrollHeight: 1200, clientHeight: 100, scrollTop: 200 });
    fireEvent.scroll(box);
    rerender(<EventFeed events={[...events, { type: "Navigated", seq: 5, ts: 4, target: "SHOP" }]} />);
    expect(box.scrollTop).toBe(200);
  });
});
