/**
 * The page's own name.
 *
 * Before this, the only <h1> in the application was the run-detail heading -
 * so glancing at a second monitor, the only way to tell which page was open
 * was to read which item in the rail was highlighted.
 */
export function PageHeader({
  title,
  meta,
  action,
}: {
  title: string;
  /** A count or a timestamp, set in mono beside the title. */
  meta?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-3">
      <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
      {meta ? <span className="font-mono text-xs text-faint-foreground">{meta}</span> : null}
      {action ? <div className="ml-auto">{action}</div> : null}
    </div>
  );
}
