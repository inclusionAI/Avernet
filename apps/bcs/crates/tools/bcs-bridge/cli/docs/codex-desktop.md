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
The daemon's raw `codex app-server proxy` control channel is a different interface.

Register using a Provider-scoped v2 token:

```sh
bcs-bridge register --engine codex-desktop \
  --engine-option socket=/absolute/path/to/desktop-app-server.sock \
  --token '<registration-token>' --bot-name 'Desktop Codex' \
  --cwd /absolute/project --api-url https://bcs.example.com \
  --upstream-url wss://bcs.example.com/ws/bot && bcs-bridge start
```

Do not pass `--engine-bin`. Registration validates the socket option's shape;
the socket connection is attempted when a task runs. Saved configuration uses:

```toml
[[bot]]
provider_bot_ref = "desktop-agent"
engine = "codex-desktop"
cwd = "/absolute/project"
engine_options = { socket = "/absolute/path/to/desktop-app-server.sock" }
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
