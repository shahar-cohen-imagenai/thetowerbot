# thetowerbot dashboard

Next.js app for the bot's dashboard. It is statically exported and the build
output is committed into `../static/`, which the bot's FastAPI process serves
directly — the bot itself needs no node at runtime, only this source does.

## Building

```bash
npm run build
```

This republishes `../static/`. Commit the result along with your source
change. Forgetting to rebuild leaves `../static/` stale against `web/ui/`,
and the Python test suite (`uv run pytest -q` at the repo root) fails on
that staleness — it's checking a build manifest, not this app's code.

## Developing

```bash
npm run dev
```

Serves on `:3000` and proxies `/api/*` to the bot's own server on `:8765`
(see `next.config.ts`; the port must match `config.WEB_PORT` in the bot). The
bot needs to be running for the proxied API calls to return anything.

## Testing

```bash
npm test
```

Runs this app's own Vitest suite (component tests, `lib/format.ts`, the
event reducer, etc). It is independent of the Python suite above.
