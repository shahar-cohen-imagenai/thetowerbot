import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, ".") } },
  test: { environment: "jsdom", globals: true },
  // next.config.ts sets trailingSlash: true so the static export resolves
  // (out/strategy/index.html, not strategy.html); the production build gets
  // that via webpack DefinePlugin, but vitest never runs next's build, so
  // next/link would silently strip the slash from every href under test
  // without this - a real behavior mismatch, not a test-only concern.
  define: { "process.env.__NEXT_TRAILING_SLASH": JSON.stringify(true) },
});
