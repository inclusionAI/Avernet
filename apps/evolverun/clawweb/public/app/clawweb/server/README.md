# Server rollout helpers

These delivery/bootstrap helpers are available through the existing
`@avernet/clawweb/server/*` package export; no manifest changes are needed.

## Shutdown ownership and ordering

`registerShutdown(lifecycle, hooks)` from `@avernet/clawweb/server/shutdown`
registers the CLI's single shared SIGINT/SIGTERM handler and returns a listener
disposer. `hooks` supplies `signals.on/off`, `exit(code)`, and `logError(error)`
so callers can test without touching process signals or exiting the process.
It waits for the optional `ClawWebLifecycle.stop()` before exiting 0. A thrown
or rejected stop is logged and exits 1. Repeated signals do not start another
stop or bypass a pending drain. Bootstraps return their cleanup as `stop()`;
they must not install independent process signal handlers.

`closeHttpServer(server, timeoutMs = 10_000): Promise<void>` from the same
module waits for the Node HTTP server's close callback. Callback and synchronous
errors reject. On the deadline it calls `closeAllConnections()` and rejects;
a forced close never reports success. The helper does not exit the process.
The public bootstrap clears its polling timer, awaits HTTP drain, then stops
its module and closes the database. Failures propagate to the CLI; dependent
cleanup is not advanced while a successful HTTP drain is still pending.

## Static frontend contract

`mountStaticFrontend(app: Express, staticDir: string): void` from
`@avernet/clawweb/server/static-frontend` mounts after application API routes.
It serves HTML with `Cache-Control: no-cache`, including `/`, `/index.html`,
and non-API SPA navigation. Existing `/assets` build files are immutable for
one year (HTML remains no-cache). Asset misses return a no-store 404, never the
SPA entrypoint. Missing file-like paths outside `/assets` also return no-store
404s instead of HTML. Unknown `/api` routes return JSON 404s, including when the
frontend directory is absent. Earlier API handlers retain precedence. The
helper does not configure an external asset store or start business services.
