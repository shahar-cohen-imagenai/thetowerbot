import { render, screen } from "@testing-library/react";
import { describe as group, expect, it } from "vitest";
import { WaveSparkline } from "./WaveSparkline";

group("WaveSparkline", () => {
  it("renders placeholder text with fewer than two waves", () => {
    render(<WaveSparkline waves={[]} />);
    expect(screen.getByText(/Not enough runs yet/)).toBeDefined();

    render(<WaveSparkline waves={[7]} />);
    expect(screen.getAllByText(/Not enough runs yet/).length).toBeGreaterThan(0);
  });
});
