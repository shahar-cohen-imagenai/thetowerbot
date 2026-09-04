"use client";

import { cn } from "@/lib/utils";

/**
 * A two-state control.
 *
 * Generalised from the arm switch in ShoppingEditor, which was the only
 * properly-built toggle in the app - everything else was a bare
 * `<input type="checkbox">`, which ignores the theme, is about 13px, and has
 * no focus ring that matches anything else on the page.
 *
 * Stays a `<button role="switch">` rather than a styled checkbox so the arm
 * switch's existing behaviour and its test (`getByRole("switch")`) carry over
 * unchanged.
 */
export function Switch({
  checked,
  onCheckedChange,
  disabled = false,
  label,
  size = "default",
  tone = "default",
  className,
}: {
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  disabled?: boolean;
  /** Rendered as aria-label; these switches sit beside their own text. */
  label: string;
  size?: "default" | "lg";
  /** "danger" is reserved for arming real spending. */
  tone?: "default" | "danger";
  className?: string;
}) {
  const large = size === "lg";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative shrink-0 rounded-full border-2 transition-colors disabled:opacity-50",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        large ? "h-7 w-14" : "h-5 w-9",
        checked
          ? tone === "danger"
            ? "border-danger bg-danger"
            : "border-primary bg-primary"
          : "border-border-strong bg-muted",
        className,
      )}
    >
      <span
        className={cn(
          "absolute top-0.5 rounded-full bg-background shadow transition-transform",
          large ? "h-5 w-5" : "h-3 w-3",
          checked ? (large ? "translate-x-7" : "translate-x-4") : "translate-x-0.5",
        )}
      />
    </button>
  );
}
