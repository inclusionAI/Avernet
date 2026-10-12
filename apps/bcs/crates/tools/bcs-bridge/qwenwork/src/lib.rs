//! QwenWork desktop adapter. BCN transport and run ownership remain in bridge core.
mod lifecycle;
mod session;
mod transport;
mod turn;

use bcs_bridge_core::engine::{Engine, EngineFactory};
use serde::Deserialize;
use std::{
    net::SocketAddr,
    path::PathBuf,
    sync::{atomic::Ordering, Arc},
};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

const MAX_ACTIVE: usize = 64;
const QUEUE_SIZE: usize = 128;
const MAX_BODY: usize = 8 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Options {
    listen: SocketAddr,
    bot_id: String,
    secret: String,
    hook_secret: String,
    database_path: PathBuf,
}

impl Options {
    fn parse(options: &toml::Table) -> Result<Self, String> {
        let value: Self = toml::Value::Table(options.clone())
            .try_into()
            .map_err(|e| format!("qwenwork options: {e}"))?;
        if !value.listen.ip().is_loopback() || value.listen.port() == 0 {
            return Err("qwenwork listen must be a loopback address with a nonzero port".into());
        }
        if [&value.bot_id, &value.secret, &value.hook_secret]
            .iter()
            .any(|s| s.trim().is_empty())
        {
            return Err("qwenwork bot_id, secret and hook_secret must be nonempty".into());
        }
        if value.bot_id.contains(':')
            || !value.database_path.is_absolute()
            || !value.database_path.is_file()
        {
            return Err(
                "qwenwork requires a bot_id without ':' and an existing absolute database_path"
                    .into(),
            );
        }
        Ok(value)
    }
}

pub struct QwenWorkFactory;
impl EngineFactory for QwenWorkFactory {
    fn id(&self) -> &str {
        "qwenwork"
    }
    fn default_bin(&self) -> &str {
        ""
    }
    fn requires_bin(&self) -> bool {
        false
    }
    fn build(&self, bin: PathBuf, options: &toml::Table) -> Result<Arc<dyn Engine>, String> {
        if !bin.as_os_str().is_empty() {
            return Err("qwenwork attaches to a running app; remove engine_bin".into());
        }
        let options = Options::parse(options)?;
        session::validate(&options.database_path)?;
        let listener = std::net::TcpListener::bind(options.listen)
            .map_err(|e| format!("qwenwork listener: {e}"))?;
        listener.set_nonblocking(true).map_err(|e| e.to_string())?;
        let runtime = tokio::runtime::Handle::try_current()
            .map_err(|_| "qwenwork must be built inside a Tokio runtime")?;
        let listener = tokio::net::TcpListener::from_std(listener).map_err(|e| e.to_string())?;
        let shutdown = CancellationToken::new();
        let state = Arc::new(transport::State::new(options));
        let task_state = state.clone();
        let stop = shutdown.clone();
        runtime.spawn(async move {
            if let Err(error) = axum::serve(listener, transport::router(task_state.clone()))
                .with_graceful_shutdown(stop.cancelled_owned())
                .await
            {
                task_state.faulted.store(true, Ordering::SeqCst);
                tracing::error!(%error, "qwenwork listener failed");
            }
        });
        Ok(Arc::new(QwenWork { state, shutdown }))
    }
}

struct QwenWork {
    state: Arc<transport::State>,
    shutdown: CancellationToken,
}
impl Drop for QwenWork {
    fn drop(&mut self) {
        self.shutdown.cancel();
        if let Some(peer) = self.state.lock().peer.as_ref() {
            peer.closed.cancel();
        }
    }
}

#[derive(Clone)]
struct Peer {
    id: String,
    tx: mpsc::Sender<serde_json::Value>,
    closed: CancellationToken,
}

struct Active {
    conversation: String,
    prompt: String,
    request_set: Option<(String, String)>,
    tx: mpsc::Sender<Input>,
}

enum Input {
    Snapshot { text: String, finished: bool },
    Hook(serde_json::Value),
}

struct Inner {
    peer: Option<Peer>,
    active: std::collections::HashMap<String, Active>,
    sessions: std::collections::HashMap<String, String>,
}

#[cfg(test)]
mod tests;
