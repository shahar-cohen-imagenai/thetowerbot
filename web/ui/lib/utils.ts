import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** One rendering for every error a page puts on screen.
 *
 * Errors out of lib/api already carry the server's own sentence as their
 * message; String(e) would print it behind an "Error: " prefix, which reads
 * as noise. Pages used String(e) for their first load and e.message for
 * everything after, so the same failure looked different depending on when
 * it happened.
 */
export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
