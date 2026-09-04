import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type MachineState = "live" | "idle" | "warn" | "error";

const SURFACE: Record<MachineState, string> = {
  live: "bg-live-surface text-live",
  idle: "bg-muted text-muted-foreground",
  warn: "bg-warn-surface text-warn",
  error: "bg-danger-surface text-danger",
};

const DOT: Record<MachineState, string> = {
  live: "bg-live",
  idle: "bg-muted-foreground",
  warn: "bg-warn",
  error: "bg-danger",
};

/**
 * A machine state, as a dot plus a word.
 *
 * Never the dot alone: the six-colour state vocabulary here has to survive
 * being glanced at from across a room by someone who may not separate the
 * green from the amber, so the word carries the same information the hue does.
 */
export function StatusBadge({
  state,
  children,
  className,
}: {
  state: MachineState;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Badge variant="outline" className={cn("gap-1.5 border-transparent", SURFACE[state], className)}>
      <span
        className={cn(
          "size-1.5 shrink-0 rounded-full",
          DOT[state],
          // The one glow in the application, and the one animation that never
          // stops. Restraint is what makes it read as meaningful rather than
          // as decoration - see the motion budget in the facelift plan.
          state === "live" && "shadow-[0_0_0_3px_var(--live-surface)] motion-safe:animate-heartbeat",
        )}
      />
      {children}
    </Badge>
  );
}
