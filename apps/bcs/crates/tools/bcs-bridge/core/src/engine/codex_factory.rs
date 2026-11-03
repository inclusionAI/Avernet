//! Composition-time selection of an owned Codex transport; desktop is separate.
use std::path::PathBuf;
use std::sync::Arc;

use super::codex_app_server::{CodexAppServer, CodexOptions};
use super::codex_owned::NativeTransport;
use super::{Engine, EngineFactory};

/// Factory for an owned app-server. Plain `codex` retains stdio by default.
/// Distributions can select Unix WebSocket without changing engine identity,
/// caller environment, permissions or the user's launcher configuration.
pub struct CodexAppServerFactory {
    id: String,
    default_bin: String,
    prefix_args: Vec<String>,
    transport: NativeTransport,
}

impl CodexAppServerFactory {
    pub fn custom(id: impl Into<String>, default_bin: impl Into<String>, prefix_args: Vec<String>) -> Self {
        Self { id: id.into(), default_bin: default_bin.into(), prefix_args, transport: NativeTransport::Stdio }
    }

    /// Launch a private app-server with `--listen unix://PATH`. This is not
    /// desktop attachment: Bridge owns, reaps and restarts the process tree.
    /// Requires a launcher/engine supporting Unix WebSocket; never falls back
    /// to stdio or to a user's shared desktop listener on failure.
    #[cfg(unix)]
    pub fn with_owned_unix_socket(mut self) -> Self {
        self.transport = NativeTransport::UnixSocket;
        self
    }
}

impl Default for CodexAppServerFactory {
    fn default() -> Self { Self::custom("codex", "codex", Vec::new()) }
}

impl EngineFactory for CodexAppServerFactory {
    fn supports_caller_env(&self) -> bool { true }
    fn id(&self) -> &str { &self.id }
    fn default_bin(&self) -> &str { &self.default_bin }
    fn build(&self, bin: PathBuf, options: &toml::Table) -> Result<Arc<dyn Engine>, String> {
        Ok(Arc::new(CodexAppServer::with_options(bin, self.prefix_args.clone(),
            CodexOptions::parse(options)?, self.transport)))
    }
}
