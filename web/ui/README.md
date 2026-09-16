# thetowerbot dashboard

Next.js app for the bot's dashboard. It is statically exported into the ignored
`../static/` directory, which the bot's FastAPI process serves directly.

## Building

```bash
npm run build
```

This republishes `../static/`. Do not commit the result. Prefer `./run.sh`
from the repository root for normal use: it installs the locked UI dependencies
when needed, rebuilds only when the local bundle is missing or stale, and
verifies the build manifest before starting the bot.

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
