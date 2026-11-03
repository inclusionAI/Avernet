//! Codex JSON-RPC transport: owned stdio process or shared Unix WebSocket.
use super::cli::CliSession;
use std::io;
#[cfg(unix)]
use futures::{SinkExt, StreamExt};

pub(super) enum CodexConnection {
    Process(CliSession),
    Managed(super::codex_persistent::ManagedConnection),
    #[cfg(unix)]
    Socket {
        socket: tokio_tungstenite::WebSocketStream<tokio::net::UnixStream>,
        trace: Option<super::trace::TraceContext>,
    },
}

impl CodexConnection {
    #[cfg(unix)]
    pub async fn connect(socket: &std::path::Path, trace: Option<super::trace::TraceContext>) -> Result<Self, super::TurnError> {
        let connected = tokio::time::timeout(std::time::Duration::from_secs(10),
            Self::connect_unix(socket, trace)).await;
        match connected {
            Ok(Ok(connection)) => Ok(connection),
            _ => Err(super::TurnError::EngineExited(
                "Codex desktop detection failed: no safe, running app-server Unix WebSocket at the selected endpoint. Open a supported desktop host with its shared listener enabled; this desktop version may not expose the required interface. No standalone server was started".into())),
        }
    }

    /// Connect only; process ownership and startup retries belong to the caller.
    #[cfg(unix)]
    pub(super) async fn connect_unix(socket: &std::path::Path, trace: Option<super::trace::TraceContext>) -> io::Result<Self> {
        use std::os::unix::fs::{FileTypeExt, MetadataExt};
        let (local, _other) = tokio::net::UnixStream::pair()?;
        let uid = local.peer_cred()?.uid();
        let original = std::fs::symlink_metadata(socket)?;
        let mut selected = socket.to_path_buf();
        if original.file_type().is_symlink() {
            // Native Codex may expose a private alias to a shortened Unix
            // socket. Both ends must be in current-user private directories.
            let private_parent = |path: &std::path::Path| -> io::Result<bool> {
                let parent = path.parent().ok_or_else(|| io::Error::other("Socket has no parent"))?;
                let metadata = std::fs::metadata(parent)?;
                Ok(metadata.is_dir() && metadata.uid() == uid && metadata.mode() & 0o077 == 0)
            };
            selected = socket.canonicalize()?;
            if original.uid() != uid || !private_parent(socket)? || !private_parent(&selected)? {
                return Err(io::Error::new(io::ErrorKind::PermissionDenied, "Unsafe Codex socket alias"));
            }
        }
        let metadata = std::fs::symlink_metadata(&selected)?;
        if !metadata.file_type().is_socket() || metadata.uid() != uid || metadata.mode() & 0o022 != 0 {
            return Err(io::Error::new(io::ErrorKind::PermissionDenied, "Unsafe Codex socket"));
        }
        let stream = tokio::net::UnixStream::connect(&selected).await?;
        if stream.peer_cred()?.uid() != uid {
            return Err(io::Error::new(io::ErrorKind::PermissionDenied, "Codex socket peer owner changed"));
        }
        let config = tokio_tungstenite::tungstenite::protocol::WebSocketConfig::default()
            .max_message_size(Some(crate::sse::MAX_FRAME_BYTES))
            .max_frame_size(Some(crate::sse::MAX_FRAME_BYTES));
        tokio_tungstenite::client_async_with_config("ws://localhost", stream, Some(config))
            .await.map(|(socket, _)| Self::Socket { socket, trace })
            .map_err(io::Error::other)
    }

    #[cfg(not(unix))]
    pub async fn connect(_: &std::path::Path, _: Option<super::trace::TraceContext>) -> Result<Self, super::TurnError> {
        Err(super::TurnError::Protocol("codex-desktop Unix sockets require macOS or Linux".into()))
    }

    pub async fn write_line(&mut self, line: &str) -> io::Result<()> {
        match self {
            Self::Process(cli) => cli.write_line(line).await,
            Self::Managed(cli) => cli.write_line(line),
            #[cfg(unix)]
            Self::Socket { socket, .. } => socket.send(line.to_owned().into()).await.map_err(io::Error::other),
        }
    }

    pub async fn next_line(&mut self) -> io::Result<Option<String>> {
        match self {
            Self::Process(cli) => cli.next_line().await,
            Self::Managed(cli) => cli.next_line().await,
            #[cfg(unix)]
            Self::Socket { socket, trace } => loop {
                use tokio_tungstenite::tungstenite::Message;
                match socket.next().await {
                    Some(Ok(Message::Text(text))) => {
                        if let Some(trace) = trace { trace.record_raw(text.as_ref()); }
                        return Ok(Some(text.to_string()));
                    },
                    Some(Ok(Message::Ping(payload))) => {
                        socket.send(Message::Pong(payload)).await.map_err(io::Error::other)?;
                    },
                    Some(Ok(Message::Pong(_))) => {},
                    None | Some(Ok(Message::Close(_))) => return Ok(None),
                    Some(Err(error)) => return Err(io::Error::other(error)),
                    _ => return Err(io::Error::new(io::ErrorKind::InvalidData, "Codex sent a non-text RPC frame")),
                }
            },
        }
    }

    pub async fn kill(&mut self) {
        match self {
            Self::Process(cli) => cli.kill().await,
            Self::Managed(cli) => cli.cancel().await,
            #[cfg(unix)]
            Self::Socket { socket, .. } => {
                let _ = tokio::time::timeout(std::time::Duration::from_secs(1), socket.close(None)).await;
            },
        }
    }

    pub async fn finish(&mut self) {
        match self {
            Self::Process(cli) => cli.close_stdin(),
            Self::Managed(cli) => cli.close(),
            #[cfg(unix)]
            Self::Socket { .. } => self.kill().await,
        }
    }
}
