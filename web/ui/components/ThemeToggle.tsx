"use client";

import { useEffect, useState } from "react";

/** "system" is the absence of the attribute - the CSS falls back to
 *  prefers-color-scheme, which is what most viewers are on. */
type Theme = "system" | "light" | "dark";

const ORDER: Theme[] = ["system", "light", "dark"];
const LABEL: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };

/** Writing the choice can throw (private windows, blocked site data). Losing
 *  the preference across reloads is survivable; a dashboard that will not
 *  render because of it is not. */
function remember(theme: Theme) {
  try {
    if (theme === "system") localStorage.removeItem("theme");
    else localStorage.setItem("theme", theme);
  } catch {
    /* ignore */
  }
}

export function ThemeToggle() {
  // Null until mounted. The exported HTML is built once, with no idea what is
  // in this browser's localStorage, so rendering a guess would hydrate into a
  // mismatch - and the inline script in layout.tsx has already applied the
  // real choice to <html> by the time this runs.
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    const applied = document.documentElement.dataset.theme;
    setTheme(applied === "light" || applied === "dark" ? applied : "system");
  }, []);

  function cycle() {
    const next = ORDER[(ORDER.indexOf(theme ?? "system") + 1) % ORDER.length];
    setTheme(next);
    if (next === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = next;
    remember(next);
  }

  return (
    <button
      type="button"
      onClick={cycle}
      aria-label={theme ? `Theme: ${LABEL[theme]}. Click to change.` : "Theme"}
      className="rounded-md border px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
    >
      {/* Reserve the width of the longest label so the rail does not shift
          when the label arrives on mount, or when it changes on click. */}
      <span className="inline-block min-w-[3.25rem] text-left">{theme ? LABEL[theme] : " "}</span>
    </button>
  );
}
