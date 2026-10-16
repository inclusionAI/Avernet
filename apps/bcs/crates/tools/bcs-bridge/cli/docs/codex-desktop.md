# Codex desktop host integration

`engine = "codex-desktop"` connects to an existing Codex app-server over a Unix
WebSocket. It supports macOS and Linux and never launches a Codex executable.
Public Avernet owns this transport; deployment-specific identity and SDK logic
belongs in the distribution (for example, OCB's AgentPass implementation).

The desktop host must expose a shared app-server Unix WebSocket and use that
same server for its UI. An ordinary desktop installation is not guaranteed to
expose this interface. Check the host's actual socket before registering. This
mode does not configure or restart the desktop host, and successful RPC access
alone does not prove that a particular desktop UI displays the created thread.
Detection uses `$CODEX_HOME/app-server-control/app-server-control.sock`, or
`~/.codex/app-server-control/app-server-control.sock` when CODEX_HOME is unset.
This is the native `--listen unix://` location. Bridge verifies a user-owned
socket without group/world write access, checks the peer UID, performs
the WebSocket upgrade and validates the Codex initialization response. Checks are
bounded, create no thread/turn, and disconnect only the probing client.
Native shortened-socket aliases are accepted only when both the alias and target
are in current-user private directories; untrusted aliases are rejected.
The private desktop `ipc/ipc.sock` is never used. A raw control proxy is not
assumed compatible merely because its path matches: the wire protocol must pass.

Register using a Provider-scoped v2 token:

```sh
bcs-bridge register --engine codex-desktop \
  --token '<registration-token>' --bot-name 'Desktop Codex' \
  --cwd /absolute/project --api-url https://bcs.example.com \
  --upstream-url wss://bcs.example.com/ws/bot && bcs-bridge start
```

Do not pass `--engine-bin`. Registration and CLI startup perform detection before
registering a Bot or starting Bridge. Missing, stale, unsafe and incompatible
endpoints fail explicitly, without creating a replacement server or falling back
to CLI mode. Each turn reconnects to the selected listener so host restarts do not
leave a cached dead connection. Advanced hosts may override discovery using
`--engine-option socket=/absolute/path/to/desktop-app-server.sock`; validation and
protocol checks still apply. Saved automatic configuration needs no socket:

```toml
[[bot]]
provider_bot_ref = "desktop-agent"
engine = "codex-desktop"
cwd = "/absolute/project"
```

Each BCS session gets its own Codex thread. Its id is persisted before starting
the first turn and resumed for later turns. Only events for that thread and turn
are forwarded. Shared notifications without both ownership identifiers are
ignored; completion events may carry the turn id in `params.turn.id`. If both
flat and nested turn ids are present, both must match. The owned CLI process
retains its legacy identifier-free notification compatibility.
Cancellation sends `turn/interrupt` with those exact ids, waits
up to five seconds for acknowledgement, then disconnects this client. Failure
to acknowledge interruption is reported; the shared server is never terminated.
If cancellation arrives during `turn/start`, Bridge waits for its response to
obtain the turn id (up to 30 seconds). A timeout reports an unknown outcome;
inspect the persisted thread before retrying.

The existing Codex driver's permission contract remains read-only, with no
network access and `approvalPolicy = "never"`. Interactive engine-side approval
requests are rejected. This integration does not inherit or expand the desktop
user's permissions.

Tests use an independently owned Unix WebSocket peer to verify streaming,
resume, event ownership, cancellation and server lifetime. Native compatibility
was checked with the desktop-bundled Codex binary's `--listen unix://PATH`:
the direct WebSocket `initialize` succeeded and disconnecting the client left
the server running. These checks do not establish desktop UI visibility.

## Switch workspaces without changing the Bot

The registered directory is the default. Override it for a single run:

```sh
bcs-bridge stop &&
bcs-bridge --profile codex-desktop-pre start --cwd /absolute/project-b --daemon
```

The same option is available for the native Codex CLI engine. Omit `--cwd` to
return to the configured default. The configuration and Bot credentials remain
unchanged; stop the active Bridge before starting another workspace. Only a
configuration containing one native Codex Bot supports this override.

Codex thread mappings and queued context are isolated by canonical directory.
Switching back resumes that project's thread. Existing SQLite v1/v2 mappings,
receipts and reconnect credentials migrate transactionally to schema v3.

New registrations record their selected identity policy in
`[registration].provider_auth`. For a legacy configuration, first record the
policy that registered the existing Bot (for example `static-bearer`); this does
not require creating another Bot. Distributions validate workspace identity
through that policy before connecting.
