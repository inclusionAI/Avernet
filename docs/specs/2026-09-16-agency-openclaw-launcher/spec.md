# Agency profile multi-instance OpenClaw launcher

## Approved scope

Implement option A requested by the user: one or more local agency-agents Markdown
profiles, one isolated OpenClaw Gateway and BCS identity per selected profile. This
is a local delivery/orchestration tool, not a Backend service or BCS core change.
The existing single-instance `install.sh` remains compatible and unchanged.
Since 2026-09-21 the complete launcher bundle resides in
`src/bcs/third-party/agency-agent/`, including helpers, tests and README. Copying
that directory outside Avernet must suffice; no imports or script calls into
plugin install-instructions are allowed.

## Contract

- Entry point: `src/bcs/third-party/agency-agent/launch-agency.sh`.
  `--engine` defaults to `openclaw` and rejects unsupported engines before any
  installation, cloning or registration. No placeholder engines are implemented.
- `--profile team/profile` selects a qualified repository-relative role;
  `.md` is optional and canonicalized, while bare stems are no longer accepted.
  `--team team` recursively expands its agent Markdown files in path order,
  excluding hidden paths and conventional README/AGENTS/CONTRIBUTING/LICENSE docs.
  Mixed/repeated selections preserve the first encounter and deduplicate by file.
  At least one selection is required; invalid, missing or empty selections fail
  before any Bot registration or Gateway launch.
- When a command includes `--team` and its expanded, deduplicated total exceeds
  five agents (existing and new, including additional explicit profiles), display
  the count/resource warning and ask once with default No. This precedes instance
  writes, profile overwrite/BCS identity prompts, plugin installs and registrations.
  No/empty/EOF cancels successfully without touching instances. Non-interactive
  requests above the threshold fail clearly; overwrite/reregister flags cannot
  bypass this admission step. Five agents or profile-only commands are unaffected.
- When `--agency-dir` is omitted, clone the approved public source
  `https://github.com/msitarzewski/agency-agents.git` atomically into
  `<state-dir>/agency-agent` once; verify/reuse it later without pulling. The
  default shared state root is `~/.avernet/bcs/agency-agent`. The shared source
  checkout is `<root>/agency-agent`; instance state lives at `<root>/<engine>/<id>`.
  Profile overwrite, re-registration and runtime locking are engine-scoped. Root
  locking is short-lived for shared checkout setup, never a cross-engine runtime
  lock. Sibling-engine records and sessions must not be scanned or changed.
- Preserve the existing Markdown-relative-path-based instance IDs (not timestamp,
  content, selection order or random IDs). Restarts, a switch between qualified
  suffix spellings, and profile-to-team selection reuse the same workspace,
  session and assigned port. Record the engine in instance metadata; old records
  with no engine field mean OpenClaw. Refuse old flat instance roots at startup;
  use offline `migrate-layout.sh` with preview/apply to move authorized old state.
  Migration stages verified copies, retains original-tree backups and a private
  journal, preserves BCS session bytes, rebases managed runtime paths and resolves
  port conflicts. Active locks/ports, ambiguous pending state and destination
  identity conflicts fail closed. No network requests or registrations are made.
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
  symlink escape and empty/invalid profiles. For each changed existing source,
  prompt independently before any instance mutation; declining keeps the saved
  local snapshot and continues. Non-interactive overwrite requires
  `--overwrite-profile`.
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
  Default/no reuses all old sessions. `--reregister` is the only non-interactive
  opt-in and is independent from `--overwrite-profile`. The retired flags `--yes`
  and `--reregister-bcs` are refused rather than silently aliased. Register all
  requested replacements before swapping any local session; back up old sessions, preserve old remote Bots, and
  retain private recovery records if only part of the remote batch succeeds.
- Durable initial-registration and re-registration markers prevent blind retry after
  ambiguous failure. Successful registrations are persisted before subsequent work.
- OpenClaw command failures write private per-instance diagnostic logs instead of
  discarding output. BCN's first-install setup-entry may cause OpenClaw's
  `config changed since last load` CAS conflict after the package is installed.
  Recover only for that exact error when a subsequent plugin listing proves the
  requested plugin is loaded; use normal `plugins enable`, never reinstall,
  `--force`, or unsafe-install bypasses. All other failures remain terminal.
- `--parallel` is a positive integer, default 4. After all user confirmations,
  preparation/plugin installs run in a bounded worker phase. Registration and
  replacement-session commits remain serial and happen only after that phase
  succeeds. Gateway start/readiness/capability publication use a second bounded
  phase; final success waits for all selected agents. Worker failures/interrupts
  cancel unscheduled work and join running workers before Gateway cleanup. Owned
  command processes are cancellable and reaped, not left running in the background.
- Human console output uses blue progress, green success, yellow warnings/prompts
  and red errors on TTYs, with serialized writes and agent labels. Pipes/files,
  TERM=dumb and NO_COLOR remain plain text. Never color JSON or private diagnostics.
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
and run the unchanged legacy `test_install.sh` separately from the standalone
tests. Live BCS/model validation requires user credentials and is not performed
implicitly. No Rust changes, so no Cargo suite is required.
