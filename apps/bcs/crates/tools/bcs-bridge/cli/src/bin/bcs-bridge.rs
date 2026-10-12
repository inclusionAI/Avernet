//! Open-source `bcs-bridge` binary: the application with its default plugins
//! (`claude-code`, `codex` and native `qwenwork` engines, explicit endpoints,
//! `BCS_BRIDGE_PROVIDER_TOKENS` webhook credentials, static-bearer identity).
//!
//! Loads config from `--config` / `BRIDGE_CONFIG` (default `~/.bcn-bridge/bridge.toml`), initializes tracing
//! with an `EnvFilter` from `RUST_LOG` (falls back to the `info` level when the
//! variable is unset), then selects the gateway HTTP or plugin Bot WS adapter.
//!
//! # Graceful shutdown
//!
//! On SIGINT (ctrl_c) or — on Unix — SIGTERM, axum stops accepting new
//! connections, then `RunRegistry::abort_all` cancels every in-flight run
//! (`aborted` terminal state); each run loop finalizes, emits the terminal SSE
//! frame, reaps its engine subprocess, and the in-flight HTTP connections drain.
//! The process then exits 0. No `unwrap`/`expect`/`panic` lives in this file.
//!
//! # HTTP/2 (h2c) — manual verification, NOT in CI
//!
//! Production BCN Provider 2.0 speaks HTTP/2 cleartext (h2c). `axum::serve`
//! drives hyper-util's auto connection builder, which detects the h2 connection
//! preface and upgrades to h2c — so a `--http2-prior-knowledge` client is served
//! as HTTP/2 without TLS. Verify locally against a running binary:
//!
//! ```text
//! # write a throwaway config bound to 127.0.0.1:21999
//! cat > /tmp/bridge-h2c.toml <<'EOF'
//! provider_id        = "bridge-1"
//! listen             = "127.0.0.1:21999"
//! [[bot]]
//! provider_bot_ref = "worker-1"
//! engine = "claude-code"
//! cwd = "/tmp"
//! EOF
//! BCS_BRIDGE_PROVIDER_TOKENS='{"bridge-1":"tok-b2p"}' \
//!   BRIDGE_CONFIG=/tmp/bridge-h2c.toml cargo run -p bcs-bridge -- start &
//! # send a bot.ping over h2c prior-knowledge (no TLS):
//! curl --http2-prior-knowledge -sS -v \
//!   -H 'Authorization: Bearer tok-b2p' \
//!   -H 'Content-Type: application/json' \
//!   --data '{"type":"req","id":"p1","method":"bot.ping","to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"}}' \
//!   http://127.0.0.1:21999/webhook
//! # expect:  * Using HTTP2 prior knowledge
//! #          < HTTP/2 200
//! #          {"ok":true}
//! # SIGTERM the binary; it should exit 0.
//! kill -TERM %1
//! ```

use std::process::ExitCode;

use bcs_bridge_app::BridgeApp;

fn main() -> ExitCode {
    BridgeApp::builder().engine(bcs_bridge_qwenwork::QwenWorkFactory).build().main()
}
