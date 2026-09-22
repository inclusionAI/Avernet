# Agency Agent → BCS standalone launcher

Choose agency-agents profiles or an entire team, start isolated local Agents, and connect them to BCS.
The only supported engine right now is **OpenClaw**. Chinese translation: [README.zh-CN.md](./README.zh-CN.md).

This directory is self-contained: `launch-agency.sh`, the `agency_*.py` modules, tests, and this README
can be copied anywhere and run without importing the rest of Avernet or calling the legacy
`install-instructions/install.sh` flow. Runtime integration goes through the public OpenClaw CLI and
the BCS HTTP/WebSocket contract, not by reading the Avernet plugin implementation.

Chinese translation: [README.zh-CN.md](./README.zh-CN.md)

## Prerequisites

- macOS / Linux, with the OpenClaw CLI on `PATH`; BCN 1.0.23 declares OpenClaw 2026.3.28 as its minimum.
  `--engine claude-code` and `--engine codex` are rejected before startup.
- `uv`, or Python 3.11+ with `PyYAML==6.0.3`. `uv` can prepare an isolated environment automatically;
  otherwise set `AGENCY_PYTHON` to a compatible interpreter.
- A working model configuration; by default only model fields are read from `~/.openclaw/openclaw.json`.
- Git and GitHub access on first run if `--agency-dir` is omitted.
- A reachable BCS endpoint. Creating new Bots or re-registering them needs a Human registration token.

Isolation here is **not** an operating-system sandbox. Agents run with your user permissions, so do not
launch untrusted role content. The launcher does not run agency repository conversion/install scripts,
does not grant the permissions described by role text, and does not create friendships or groups.

## Quick start

Run from this directory (or replace the entry point with its full path):

```bash
# Start two profiles
./launch-agency.sh \
  --engine openclaw \
  --profile engineering/engineering-sre \
  --profile engineering/engineering-backend-architect \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token '<human-register-token>'

# Start the whole engineering team
./launch-agency.sh \
  --engine openclaw \
  --team engineering \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token-file /path/to/register-token

# Mix teams and individual profiles; overlaps are deduplicated
./launch-agency.sh \
  --team engineering \
  --team design \
  --profile engineering/engineering-sre \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token-file /path/to/register-token
```

If you want the script itself without checking out Avernet, you can curl the entry point directly.
When the bundled Python modules are missing, the launcher downloads the fixed `third-party/agency-agent`
bundle to `~/.avernet/bcs/agency-agent/.bundle/` before starting:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/third-party/agency-agent/launch-agency.sh || echo exit\ 1)" --launch-agency.sh --engine openclaw \
  --profile engineering/engineering-sre \
  --profile engineering/engineering-backend-architect \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token '<human-register-token>'
```

The `bash -c` form only executes the entry script; the script itself fetches the rest of the files it needs.

## Parameters

| Option | Default / meaning |
| --- | --- |
| `--engine` | `openclaw`; only supported value |
| `--lang` | `en` (default) or `zh`; selects the profile repository and isolates checkouts/instances per language |
| `--profile TEAM/PROFILE` | repeatable; one role, optional `.md` suffix |
| `--team TEAM` | repeatable; every agent in the team; overlaps with profiles are deduplicated |
| `--agency-dir` | local checkout; defaults to cloning/reusing `<state-dir>/agency-agents` (`agency-agents-zh` for `--lang zh`) |
| `--state-dir` | `~/.avernet/bcs/agency-agent`, shared root; instances live under `<state-dir>/<engine>` (`<engine>-zh` for `--lang zh`) |
| `--model-config` | `~/.openclaw/openclaw.json`, read-only extraction of model settings |
| `--bcs-endpoint` | required, HTTP(S) BCS base URL; deployment prefixes are allowed |
| `--token` / `--token-file` | mutually exclusive; otherwise `BCS_REGISTER_TOKEN` |
| `--overwrite-profile` | accept all changed profile overwrites without prompting; does not re-register BCS |
| `--overwrite-endpoint` | approve overwriting saved BCS endpoints and re-registering those instances without prompting |
| `--reregister` | re-register every selected instance that already has a BCS session |
| `--parallel` | concurrent install/startup tasks, default `4`; `1` restores serial behavior |
| `--base-port` | `19000`, used only for new instances |
| `--port-step` | `20`, minimum 20; 20 ports per instance are reserved |
| `--startup-timeout` | seconds to wait for each authenticated connection, default 90 |
| `--bcn-plugin` | `@avernet-plugin/openclaw-channel-bcn@1.0.23`; can be replaced with a compatible build |

The old names `--yes` and `--reregister-bcs` are intentionally rejected. Use `--overwrite-profile`
and `--reregister` instead.

Human tokens have the same semantics as the legacy installer: they are Human registration tokens, not
arbitrary user access tokens, and not Bot tokens. A command-line token can show up in shell history or
process lists; use a `0600` file or environment variable if that matters. The launcher does not pass the
registration token to OpenClaw or Git child processes, and it never prints it to the console.

## Parallel startup and colored logs

By default up to **4** agents are prepared and installed concurrently. After all plugin preparations
finish, BCS registration and any replacement-session commits still happen serially. Then up to 4 agents
start Gateways, wait for authenticated connections, and publish capabilities in parallel. `ALL CONNECTED`
is printed only after everything completes.

```bash
# Lower parallelism on constrained machines
./launch-agency.sh --team engineering --parallel 2 \
  --bcs-endpoint http://127.0.0.1:21000 --token-file /path/to/register-token

# Serial debugging
./launch-agency.sh --team engineering --parallel 1 \
  --bcs-endpoint http://127.0.0.1:21000 --token-file /path/to/register-token
```

`--parallel` caps the number of install/startup tasks, not the final number of online agents. If you
select 12 roles with parallelism 4, 12 Gateways still end up running. All interactive prompts happen
before parallel work starts.

Each progress line includes the agent name; completion order can differ from argument order. Terminals
show blue progress, green success, yellow warnings/confirmations, and red errors; redirected or piped
output stays plain text, and `NO_COLOR=1` disables ANSI colors. Colors are for humans only and are never
written into JSON or private diagnostic files.

If a required phase fails or you press Ctrl+C, unfinished work is cancelled and this launcher terminates
and reaps the child processes it created. It does not keep registering or leave installation processes
behind. Capability publication HTTP requests keep the existing 20-second timeout; cleanup waits for the
request to finish before it returns. Capability publication pending remains a warning, not a fatal
error for already authenticated connections.

## Persistent layout and reuse

### Profile languages (`--lang`)

Profiles are available in English from [agency-agents](https://github.com/msitarzewski/agency-agents)
(`--lang en`, default) and in Chinese from
[agency-agents-zh](https://github.com/jnMetaCode/agency-agents-zh) (`--lang zh`). The two languages are
fully isolated: each gets its own read-only checkout (`agency-agents` / `agency-agents-zh`), its own
instance scope (`openclaw` / `openclaw-zh`), its own BCS Bot identities, sessions, workspaces and
memory. The same relative profile launched in both languages produces two independent instances with
two independent Bots, and their Gateway ports are reserved across language scopes so both stacks can
run concurrently. `instance.json` records the language; a saved instance whose language no longer
matches the requested one is never silently reused.

Default layout:

```text
~/.avernet/
  bcs/
    agency-agent/
      agency-agents/                        # shared read-only en checkout (agency-agents)
      agency-agents-zh/                     # shared read-only zh checkout (agency-agents-zh)
      openclaw/
        .launcher.lock                      # per-engine lock only
        engineering-sre-<stable-path-hash>/ # isolated OpenClaw instance
        engineering-backend-architect-<hash>/
      openclaw-zh/                          # isolated scope for --lang zh instances
        .launcher.lock
        ...
      codex/                                # reserved for future engines; currently unsupported
```

On first use the launcher shallow-clones the repository for the selected `--lang`
(`https://github.com/msitarzewski/agency-agents.git` for `en`,
`https://github.com/jnMetaCode/agency-agents-zh.git` for `zh`) into the matching language-scoped
directory under `~/.avernet/bcs/agency-agent/`, then reuses it without auto-pulling. The clone is
written to a temporary directory first and moved into place only after success. If you pass
`--agency-dir`, no Git operation is performed and no second checkout is made.

Instance directory names are derived from the normalized repository-relative profile path, not from
timestamps, randomness, content hashes, or selection order. **The engine directory provides the
isolation**, so the same profile name in a different engine gets its own state, session, history, and
runtime configuration. Within one engine, these actions reuse the same instance directory, Bot ID,
session, port, and memory:

- running the same command again
- switching between `team/profile` and `team/profile.md`
- first launching a profile and then launching a team that contains it
- changing the order of team/profile selections

Only newly selected roles create new instance directories. Reruns do not create extra profile/session
backups unless you explicitly overwrite or re-register. The full agency repository is not copied per
instance; each instance keeps only the profile content it actually uses.

An instance contains:

```text
instance.json                         # profile source digest, engine, network, plugin, port
profile.md                            # complete source snapshot in use
profile.previous.<timestamp>.md       # previous snapshot before an explicit overwrite
openclaw.json                         # instance config
.bcs/session.json                     # Bot identity and reconnect credential
.bcs/session.previous.<timestamp>.json # old identity before explicit re-registration
agents/main/agent/                    # independent model-auth state
workspace/SOUL.md                     # full role body
workspace/AGENTS.md                   # operating instructions
workspace/IDENTITY.md                 # name, emoji, summary
workspace/MEMORY.md                   # preserved if created during runs
gateway.log                          # current Gateway log
```

Sensitive files are written with `0600`, and instance directories use `0700`; do not commit them to Git.
This does not mean “no files at all”: normal restarts still refresh runtime config/logs, and the Gateway
may update reconnect credentials.

## Overwrite and BCS identity confirmation

All profile overwrite checks and `--reregister` decisions apply only to the selected instances under the
current `--engine`; sibling engines are never scanned or modified.

In an interactive terminal, each changed profile is asked about first. `No` keeps the saved snapshot and
role files, and continues. `Yes` backs up the old snapshot before overwriting SOUL/AGENTS/IDENTITY.
Overwriting a role does not clear conversations, memory, or model-auth state.

After all local profile decisions, the launcher asks once:

```text
Existing BCS sessions detected:
  SRE: Bot ID bot_xxx
  Backend Architect: Bot ID bot_yyy
Re-register all existing BCS sessions as new Bots? [y/N]
```

`No` reuses the old identity for every selected instance. `Yes` needs a Human token and creates new
BCS Bots for all selected instances that already had sessions. Profiles without sessions are still
registered normally. Old remote Bots are not automatically deleted. `--overwrite-profile` and
`--reregister` are independent.

In non-interactive mode, the default is to reuse sessions; if profile content changed without
`--overwrite-profile`, the launcher fails instead of waiting for input.

For batch re-registration, the launcher collects all replacement credentials first and then swaps local
sessions. If one remote request fails, no old session is replaced, no Gateway starts, and the new
identity is preserved in private `bcs-reregistration.pending.json` for manual recovery. This prevents
blind repeated registrations.

## Model and runtime state

Model configuration must be JSON. By default the launcher reads only `models`,
`agents.defaults.model`, and optionally `agents.defaults.models` from `~/.openclaw/openclaw.json`.
Other channels, plugins, env, agent lists, workspaces, and OAuth auth stores are not copied, and the
default OpenClaw config is not modified.

If a provider uses `${MODEL_API_KEY}` or similar references, provide them in the launch environment.
The default OpenClaw `.env` or OAuth login is not inherited automatically. `OPENCLAW_*`, `BCS_*`,
`BOT_*`, and `MOLTIS_*` prefixes are isolated; do not use them to carry model keys.

The launcher supervises all selected instances in the foreground. Ctrl+C/SIGTERM stops only the
Gateways it started and keeps state. Do not use SIGKILL: cleanup is not guaranteed then. A second
launcher on the same engine directory is refused rather than trying to kill the running one; locks in
other engines do not block this engine. The shared checkout lock is short-lived and does not span
across engines. Browser capability is disabled by default to avoid CDP port conflicts.

`ALL CONNECTED` means each BCN channel reached an authenticated session token, **not** that the model
has already replied. Capability publication failure is retried a bounded number of times and then shown
as `capability metadata pending` with a private diagnostic file; it does not stop already authenticated
connections. This launcher does not make a paid model test call; verify the role by sending a real BCS
message yourself.

## Old directory and command migration

Old commands need `--overwrite-profile`, `--reregister`, and `team/profile`.
`--state-dir` now points to the shared root, not the engine directory itself; even when passed
explicitly, instances live under `<state-dir>/<engine>/`.

This bundle does not ship an automatic migration script. If a legacy flat layout is detected, startup
stops before registering anything. Move the old instance directory into the engine-scoped layout
yourself (for example `~/.bcs/agency/<agent>` → `~/.avernet/bcs/agency-agent/openclaw/<agent>`),
verify `session.json`, ports, and configuration first, and keep a backup path. Do not run old and new
copies of the same identity at the same time. The launcher does not delete old data.

An old `instance.json` without an `engine` field is treated as OpenClaw. Do not overwrite an existing
new directory with the same name. Changing the BCS network asks once whether the saved endpoint may be overwritten: `Yes` archives the
old `.bcs/session.json` as `session.previous.<timestamp>.json` and re-registers those instances on the
new endpoint (a Human token is required); `No` fails with `BCS endpoint has changed and cannot
continue without overwriting`. Non-interactive runs need `--overwrite-endpoint`. Plugin spec or engine
changes still fail closed to avoid identity confusion.

## Failure recovery and verification

- OpenClaw command failures: check the instance’s `openclaw-plugins-*.log` (may contain internal
  information; do not share raw).
- BCN first install can hit `config changed since last load`: the launcher recovers only when the
  plugin is already loaded, using plain `plugins enable`; it does not use `--force` or bypass safety.
- Registration timeouts / 5xx: keep `registration.pending.json` (or `bcs-reregistration.pending.json`
  for re-registration) and resolve the remote result before retrying to avoid duplicate Bots.
- Port conflicts: stop the actual process holding the port; new instances can use `--base-port`. The
  launcher never kills unknown processes.

Tests for this bundle (no real BCS / model / GitHub needed):

```bash
uv run --no-project --with PyYAML==6.0.3 python -m unittest discover -s tests -v
```

The tests use a temporary HOME, loopback HTTP, and executable stubs for OpenClaw/Git, including a
copy-outside-Avernet check. They do not replace real model/network deployment validation.
