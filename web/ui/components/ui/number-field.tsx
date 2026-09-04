import { Input } from "@/components/ui/input";

/**
 * A labelled number, with optional help text and a badge.
 *
 * Extracted from StrategyEditor's local NumberField, which ShoppingEditor had
 * separately hand-written four times over. The `key={value}` remount is the
 * load-bearing part and the reason this is worth having in one place: without
 * it a Revert (or a change arriving from another tab) updates the draft but
 * leaves the previously-typed number sitting in the field, looking live.
 */
export function NumberField({
  label,
  ariaLabel,
  value,
  onCommit,
  min,
  max,
  step,
  disabled,
  note,
  badge,
  width = "w-24",
}: {
  label: string;
  /** When the visible label reads differently from what the field is called -
   *  "Visit frequency" on screen, "Visit every N runs" to a screen reader. */
  ariaLabel?: string;
  value: number;
  onCommit: (n: number) => void;
  min: number;
  max?: number;
  step: number;
  disabled?: boolean;
  /** Help text under the label. */
  note?: string;
  badge?: React.ReactNode;
  width?: string;
}) {
  return (
    <label className="flex items-center justify-between gap-3 text-sm">
      <span>
        {label}
        {badge}
        {note ? <span className="block max-w-xs text-xs text-muted-foreground">{note}</span> : null}
      </span>
      <Input
        type="number" min={min} max={max} step={step} aria-label={ariaLabel ?? label}
        key={value}
        defaultValue={value}
        disabled={disabled}
        onBlur={(e) => onCommit(Number(e.target.value))}
        className={`${width} text-right font-mono`}
      />
    </label>
  );
}
