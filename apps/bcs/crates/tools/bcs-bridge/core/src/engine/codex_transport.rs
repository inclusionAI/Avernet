//! Codex JSON-RPC transport: owned stdio process or shared Unix WebSocket.
use super::cli::CliSession;
use std::io;
#[cfg(unix)]
use futures::{SinkExt, StreamExt};

pub(super) enum CodexConnection {
    Process(CliSession),
    #[cfg(unix)]
    Socket {
        socket: tokio_tungstenite::WebSocketStream<tokio::net::UnixStream>,
        trace: Option<super::trace::TraceContext>,
    },
}

impl CodexConnection {
    #[cfg(unix)]
    pub async fn connect(socket: &std::path::Path, trace: Option<super::trace::TraceContext>) -> Result<Self, super::TurnError> {
        let connected = tokio::time::timeout(std::time::Duration::from_secs(10), async {
            let stream = tokio::net::UnixStream::connect(socket).await?;
            let config = tokio_tungstenite::tungstenite::protocol::WebSocketConfig::default()
                .max_message_size(Some(crate::sse::MAX_FRAME_BYTES))
                .max_frame_size(Some(crate::sse::MAX_FRAME_BYTES));
            tokio_tungstenite::client_async_with_config("ws://localhost", stream, Some(config))
                .await.map(|(socket, _)| Self::Socket { socket, trace })
                .map_err(io::Error::other)
        }).await;
        match connected {
            Ok(Ok(connection)) => Ok(connection),
            _ => Err(super::TurnError::EngineExited(
                "Cannot connect to the Codex desktop Unix WebSocket; check the socket and shared app-server listener. No standalone server was started".into())),
        }
    }

    #[cfg(not(unix))]
    pub async fn connect(_: &std::path::Path, _: Option<super::trace::TraceContext>) -> Result<Self, super::TurnError> {
        Err(super::TurnError::Protocol("codex-desktop Unix sockets require macOS or Linux".into()))
    }

    pub async fn write_line(&mut self, line: &str) -> io::Result<()> {
        match self {
            Self::Process(cli) => cli.write_line(line).await,
            #[cfg(unix)]
            Self::Socket { socket, .. } => socket.send(line.to_owned().into()).await.map_err(io::Error::other),
        }
    }

    pub async fn next_line(&mut self) -> io::Result<Option<String>> {
        match self {
            Self::Process(cli) => cli.next_line().await,
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
                    _ => return Err(io::Error::new(io::ErrorKind::InvalidData, "Codex desktop sent a non-text RPC frame")),
                }
            },
        }
    }

    pub async fn kill(&mut self) {
        match self {
            Self::Process(cli) => cli.kill().await,
            #[cfg(unix)]
            Self::Socket { socket, .. } => {
                let _ = tokio::time::timeout(std::time::Duration::from_secs(1), socket.close(None)).await;
            },
        }
    }

    pub async fn finish(&mut self) {
        match self {
            Self::Process(cli) => cli.close_stdin(),
            #[cfg(unix)]
            Self::Socket { .. } => self.kill().await,
        }
    }
}
