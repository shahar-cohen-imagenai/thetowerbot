"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Live" },
  { href: "/runs/", label: "Runs" },
  { href: "/stats/", label: "Stats" },
  { href: "/errors/", label: "Errors" },
  { href: "/control/", label: "Control" },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <nav className="flex shrink-0 gap-1 overflow-x-auto border-b p-2 md:h-dvh md:w-44 md:flex-col md:overflow-visible md:border-b-0 md:border-r md:p-3">
      <div className="hidden px-2 pb-3 text-sm font-semibold md:block">The Tower</div>
      {LINKS.map((link) => {
        const active = pathname === link.href;
        return (
          <Link
            key={link.href}
            href={link.href}
            className={`rounded-md px-3 py-1.5 text-sm ${
              active ? "bg-accent text-accent-foreground font-medium" : "text-muted-foreground hover:bg-accent/50"
            }`}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
