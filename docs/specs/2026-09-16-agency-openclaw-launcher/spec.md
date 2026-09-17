# Agency profile multi-instance OpenClaw launcher

## Approved scope

Implement option A requested by the user: one or more local agency-agents Markdown
profiles, one isolated OpenClaw Gateway and BCS identity per selected profile. This
is a local delivery/orchestration tool, not a Backend service or BCS core change.
The existing single-instance `install.sh` remains compatible and unchanged.

## Contract

- Entry point beside `install.sh`: `launch-agency.sh`. Repeated `--profile` paths
  are relative to `--agency-dir`; a unique file stem is also accepted. When `--agency-dir` is omitted,
  clone `https://github.com/msitarzewski/agency-agents.git` atomically into
  `<state-dir>/agency-agents` on first use; reuse and verify its origin thereafter,
  never automatically pull. The default state directory is `~/.bcs/agency`.
- `--model-config` defaults to `~/.openclaw/openclaw.json` and supplies JSON
  `models` and `agents.defaults.model`
  (optionally `agents.defaults.models`). No other channel, plugin, environment,
  session, workspace, auth-store, or agent configuration is inherited.
- `--bcs-endpoint` is explicit. Registration credentials come from `--token` or `--token-file` (mutually
  exclusive), falling back to `BCS_REGISTER_TOKEN`, never logs or child environments.
  The command-line token has the same Human registration semantics as install.sh;
  document the shell-history/process-list exposure of that convenience option. Reuse credentials
  only for the same source profile and BCS endpoint.
- YAML frontmatter is parsed with PyYAML safe loading. Preserve the entire role
  body in SOUL.md, generate minimal AGENTS.md and IDENTITY.md, and keep a hashed
  source snapshot. Never execute agency repository scripts. Reject traversal,
  symlink escape, empty/invalid profiles and duplicate selection. For each changed
  existing source, prompt independently before any mutation; declining keeps the
  saved local snapshot and continues. Non-interactive overwrite requires `--yes`.
- State/config/workspace/session/agent paths are private per instance. Pin BCS
  channel routing to the only agent, `main`; disable inherited credentials and
  isolate OpenClaw environment. Bind Gateways to loopback. Reserve port spacing
  of at least 20. Do not install or restart OS services or the default Gateway.
- Reuse the existing `/register` protocol (legacy 200 bare response and 201 data
  envelope), `.bcs/session.json`, and BCN plugin. Configure the channel only after
  installing the plugin. Probe `channels status --probe --json` for connected BCS plus a nonempty
  authenticated session token,
  then use the authenticated `/bots/onboard` API for name/description/domain.
  Capability metadata convergence is retried a bounded number of times but is not
  a readiness requirement after authenticated BCN connection; persist a private
  diagnostic and retry next launch instead of stopping all Gateways.
- After all per-profile overwrite decisions, ask once whether every selected
  instance with an existing `.bcs/session.json` should receive a new BCS identity.
  Default/no reuses all old sessions. `--reregister-bcs` is the only non-interactive
  opt-in and is independent from `--yes`. Register all requested replacements before
  swapping any local session; back up old sessions, preserve old remote Bots, and
  retain private recovery records if only part of the remote batch succeeds.
- Durable initial-registration and re-registration markers prevent blind retry after
  ambiguous failure. Successful registrations are persisted before subsequent work.
- OpenClaw command failures write private per-instance diagnostic logs instead of
  discarding output. BCN's first-install setup-entry may cause OpenClaw's
  `config changed since last load` CAS conflict after the package is installed.
  Recover only for that exact error when a subsequent plugin listing proves the
  requested plugin is loaded; use normal `plugins enable`, never reinstall,
  `--force`, or unsafe-install bypasses. All other failures remain terminal.
- Run in the foreground, supervise all child process groups, and stop only owned
  processes on interrupt, startup failure, or child exit. Preserve runtime state
  and credentials for reruns. Concurrent launchers sharing a state root fail.
- Readiness means an authenticated BCN connection, not a verified model reply.
  Logs and summary explicitly distinguish that boundary. No automatic group,
  friendship, tool installation, permissions, or paid model calls.

## Validation

Use stdlib unittest, temp directories, a loopback HTTP BCS stub and an executable
OpenClaw stub. Exercise real launcher processes, file permissions, config isolation,
registration reuse/failure, connected probes, cleanup and source validation. Retain
and run `test_install.sh`. Live BCS/model validation requires user credentials and
is not performed implicitly. No Rust changes, so no Cargo suite is required.
