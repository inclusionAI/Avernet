//! Owned process lifetime is independent of its RPC transport. Desktop sockets
//! never use this guard and can never trigger owned-process teardown.
use std::path::Path;

use super::cli::CliSession;
use super::codex_app_server::CodexAppServer;
use super::codex_transport::CodexConnection;
use super::{TurnError, TurnRequest};
use tokio_util::sync::CancellationToken;

#[derive(Clone, Copy, Default)]
pub(super) enum NativeTransport {
    #[default]
    Stdio,
    #[cfg(unix)]
    UnixSocket,
}

pub(super) struct OwnedConnection {
    pub transport: CodexConnection,
    #[cfg(unix)]
    process: Option<SocketProcess>,
}

#[cfg(unix)]
struct SocketProcess {
    // Drop the process before removing its endpoint, including on cancelled
    // startup and task abort. CliSession owns process-group teardown.
    child: CliSession,
    _directory: tempfile::TempDir,
}

impl OwnedConnection {
    pub async fn spawn(mode: NativeTransport, engine: &CodexAppServer, bin: &Path,
        req: &TurnRequest, abort: &CancellationToken) -> Result<Self, TurnError> {
        if abort.is_cancelled() { return Err(TurnError::Aborted); }
        match mode {
            NativeTransport::Stdio => {
                let mut child = CliSession::spawn(bin, &engine.launch_args("stdio://"), &req.cwd, &req.env,
                    req.trace.as_ref().map(super::trace::TraceContext::shared_process)).await.map_err(TurnError::Spawn)?;
                child.disable_raw_trace();
                Ok(Self { transport: CodexConnection::Process(child), #[cfg(unix)] process: None })
            }
            #[cfg(unix)]
            NativeTransport::UnixSocket => Self::spawn_socket(engine, bin, req, abort).await,
        }
    }

    #[cfg(unix)]
    async fn spawn_socket(engine: &CodexAppServer, bin: &Path, req: &TurnRequest,
        abort: &CancellationToken) -> Result<Self, TurnError> {
        use std::io;
        use std::os::unix::ffi::OsStrExt;
        use std::os::unix::fs::PermissionsExt;
        // Unique, private endpoint per process generation, never the desktop
        // discovery path. Do not change HOME/CODEX_HOME or launcher config.
        let directory = tempfile::Builder::new().prefix("bcs-cdx-").tempdir().map_err(TurnError::Spawn)?;
        std::fs::set_permissions(directory.path(), std::fs::Permissions::from_mode(0o700)).map_err(TurnError::Spawn)?;
        let path = directory.path().join("rpc.sock");
        if path.as_os_str().as_bytes().len() >= 104 {
            return Err(TurnError::Spawn(io::Error::new(io::ErrorKind::InvalidInput,
                "Codex Unix socket path is too long; use a shorter temporary directory")));
        }
        let path_str = path.to_str().ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput,
            "Codex Unix socket path is not UTF-8"))?;
        let args = engine.launch_args(&format!("unix://{path_str}"));
        let child = CliSession::spawn_for_socket(bin, &args, &req.cwd, &req.env,
            req.trace.as_ref().map(super::trace::TraceContext::shared_process)).await.map_err(TurnError::Spawn)?;
        let process = SocketProcess { child, _directory: directory };
        // NativeServer bounds all of startup (readiness, handshake, initialize)
        // with one deadline. Only transient endpoint readiness errors retry.
        let transport = loop {
            let connected = tokio::select! {
                _ = abort.cancelled() => return Err(TurnError::Aborted),
                result = CodexConnection::connect_unix(&path, None) => result,
            };
            match connected {
                Ok(connection) => break connection,
                Err(error) if matches!(error.kind(), io::ErrorKind::NotFound | io::ErrorKind::ConnectionRefused) => {
                    tokio::select! {
                        _ = abort.cancelled() => return Err(TurnError::Aborted),
                        _ = tokio::time::sleep(std::time::Duration::from_millis(25)) => {},
                    }
                }
                Err(error) => return Err(TurnError::Io(error)),
            }
        };
        Ok(Self { transport, process: Some(process) })
    }

    pub async fn shutdown(&mut self) {
        self.transport.kill().await;
        #[cfg(unix)]
        if let Some(process) = &mut self.process { process.child.kill().await; }
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::collections::{HashSet, VecDeque};
    use std::sync::Arc;

    struct Observer;
    #[async_trait::async_trait]
    impl crate::engine::SessionObserver for Observer {
        async fn established(&self, _: &str) -> Result<(), String> { Ok(()) }
    }

    /// Opt-in local compatibility test: isolated engine home, no model turn,
    /// no changes to the real launcher's config and no external Bot connection.
    #[tokio::test]
    #[ignore = "set BCS_BRIDGE_TEST_CODEX_BIN to a local Codex binary supporting unix://"]
    async fn real_codex_socket_initialization_and_rpc_burst() {
        let bin = std::path::PathBuf::from(std::env::var_os("BCS_BRIDGE_TEST_CODEX_BIN")
            .expect("BCS_BRIDGE_TEST_CODEX_BIN must name a local Codex binary"));
        let _ = tracing_subscriber::fmt().with_env_filter("bcs_bridge_core::engine=debug").with_test_writer().try_init();
        let home = tempfile::tempdir().unwrap();
        std::fs::create_dir(home.path().join("codex")).unwrap();
        let engine = CodexAppServer::new(bin.clone(), Vec::new());
        let mut req = TurnRequest::new("transport-smoke", "unused", home.path(), Arc::new(Observer));
        req.env = vec![
            ("HOME".into(), home.path().to_string_lossy().into_owned()),
            ("CODEX_HOME".into(), home.path().join("codex").to_string_lossy().into_owned()),
            ("OPENAI_API_KEY".into(), String::new()), ("CODEX_API_KEY".into(), String::new()),
            ("CODEX_DISABLE_TELEMETRY".into(), "1".into()),
            ("OTEL_ENABLE_TELEMETRY".into(), "false".into()),
            ("CDXHOOKS_ENABLE_TELEMETRY".into(), "false".into()),
        ];
        let abort = CancellationToken::new();
        tokio::time::timeout(std::time::Duration::from_secs(20), async {
            eprintln!("smoke: waiting for owned socket");
            let mut owned = OwnedConnection::spawn(NativeTransport::UnixSocket, &engine, &bin, &req, &abort).await.unwrap();
            eprintln!("smoke: socket connected");
            let directory = owned.process.as_ref().unwrap()._directory.path().to_path_buf();
            engine.initialize(&mut owned.transport, &mut 1, &mut VecDeque::new(), &abort).await.unwrap();
            eprintln!("smoke: initialized");
            for id in 1000..1200 {
                owned.transport.write_line(&serde_json::json!({"id":id,"method":"bridge/transportSmokeUnknown"}).to_string()).await.unwrap();
            }
            tokio::time::sleep(std::time::Duration::from_millis(250)).await;
            let mut received = HashSet::new();
            while received.len() < 200 {
                let line = owned.transport.next_line().await.unwrap().expect("RPC stream must stay open");
                let response: serde_json::Value = serde_json::from_str(&line).unwrap();
                if let Some(id @ 1000..=1199) = response["id"].as_u64() {
                    assert!(response.get("error").is_some(), "unknown method must return a protocol error");
                    assert!(received.insert(id), "duplicate RPC response");
                }
            }
            owned.shutdown().await;
            drop(owned);
            assert!(!directory.exists());
        }).await.expect("real Codex transport smoke must be bounded");
    }
}
