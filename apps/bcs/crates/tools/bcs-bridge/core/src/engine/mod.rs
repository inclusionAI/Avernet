//! Engine plugin interface.
//!
//! An [`Engine`] drives one downstream turn against a local coding-agent
//! process or an already-running application. Engines are created by an
//! [`EngineFactory`] registered under a string id in the [`EngineRegistry`];
//! `[[bot]] engine = "<id>"` selects one.
//! The built-in factories are [`claude_code::ClaudeCodeFactory`] (`claude-code`)
//! and [`codex_app_server::CodexAppServerFactory`] (`codex`). Distributions add
//! engines or override a built-in by registering a factory under the same id.
//!
//! Engine contract:
//! - validate an engine-native session id with [`is_valid_engine_session_id`],
//!   then await [`SessionObserver::established`] before continuing the turn, so
//!   a later failure can still resume the session;
//! - emit only engine-neutral events (`sse::chat_delta`, `agent_thinking`,
//!   `agent_tool`, `interaction_event`); the run loop derives terminal frames
//!   from the returned [`TurnOutcome`] / [`TurnError`];
//! - on `abort`, or when `events` is closed, stop the active engine turn and return
//!   [`TurnError::Aborted`];
//! - HITL is optional: register pending decisions with `TurnRequest::interactions`
//!   and await their resolution, or reject engine-side requests.

pub mod claude_code;
pub mod cli;
pub mod codex_app_server;
pub mod trace;

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;

use bcs_protocol::stream::StreamEvent;

use crate::config::BotConfig;
use crate::interaction::InteractionRegistry;

/// One BCS downstream turn request.
///
/// `run_id` is the BCS downstream body id; frames use it as `runId`.
/// `engine_session_id` carries the engine-native session id on follow-up turns.
/// `permission_mode` is passed through from bot configuration; each engine
/// defines how (and whether) it interprets the value.
/// `interactions` is the run's HITL interaction registry: an engine registers
/// pending decisions with it, the `interaction.resolve` handler delivers them.
#[non_exhaustive]
pub struct TurnRequest {
    pub run_id: String,
    pub prompt: String,
    pub engine_session_id: Option<String>,
    pub session_observer: Arc<dyn SessionObserver>,
    pub cwd: PathBuf,
    pub model: Option<String>,
    pub permission_mode: Option<String>,
    pub interactions: InteractionRegistry,
    pub trace: Option<trace::TraceContext>,
}

impl TurnRequest {
    /// Build a first-turn request; set the remaining public fields as needed.
    pub fn new(
        run_id: impl Into<String>,
        prompt: impl Into<String>,
        cwd: impl Into<PathBuf>,
        session_observer: Arc<dyn SessionObserver>,
    ) -> Self {
        Self {
            run_id: run_id.into(),
            prompt: prompt.into(),
            engine_session_id: None,
            session_observer,
            cwd: cwd.into(),
            model: None,
            permission_mode: None,
            interactions: InteractionRegistry::new(),
            trace: None,
        }
    }
}

/// Internal lifecycle notification. Await durable acknowledgement as soon as
/// the engine creates/resumes a session, even if the turn subsequently fails.
#[async_trait::async_trait]
pub trait SessionObserver: Send + Sync {
    async fn established(&self, engine_session_id: &str) -> Result<(), String>;
}

/// Outcome of an engine turn: the engine-internal session id (if one was
/// established/resumed) and the final assistant text, if the turn completed.
#[derive(Debug)]
pub struct TurnOutcome {
    pub engine_session_id: Option<String>,
    pub final_text: Option<String>,
}

/// Engine turn errors. `EngineExited` carries the engine's own exit reason;
/// `Aborted` is returned when the run is cancelled via the abort token.
/// `Io` is raised on stdout/stdin IO failures (e.g. broken pipe) and converts
/// from [`std::io::Error`] via `?` for the driver's read/write paths.
#[derive(Debug, thiserror::Error)]
pub enum TurnError {
    #[error("session persistence failed: {0}")]
    SessionStorage(String),
    #[error("spawn engine: {0}")]
    Spawn(std::io::Error),
    #[error("engine exited: {0}")]
    EngineExited(String),
    #[error("engine turn aborted")]
    Aborted,
    #[error("engine protocol error: {0}")]
    Protocol(String),
    #[error("engine io: {0}")]
    Io(#[from] std::io::Error),
}

#[async_trait::async_trait]
pub trait Engine: Send + Sync {
    async fn run_turn(
        &self,
        req: TurnRequest,
        events: tokio::sync::mpsc::Sender<StreamEvent>,
        abort: tokio_util::sync::CancellationToken,
    ) -> Result<TurnOutcome, TurnError>;
}

/// Creates [`Engine`]s for bots configured with `engine = "<id>"`.
pub trait EngineFactory: Send + Sync {
    /// Configuration id; also recorded with persisted engine sessions, so a
    /// bot keeps its sessions only while its engine id is unchanged.
    fn id(&self) -> &str;
    /// Executable looked up on `PATH` when the bot configures no `engine_bin`.
    /// Ignored when `requires_bin` returns false.
    fn default_bin(&self) -> &str;
    /// False for adapters to an already-running application. Registration skips
    /// executable lookup and ignores `default_bin`; `build` receives an empty path.
    /// Existing subprocess factories retain executable validation by default.
    fn requires_bin(&self) -> bool { true }
    /// Build an engine for `bin`, validating the bot's `engine_options`.
    fn build(&self, bin: PathBuf, options: &toml::Table) -> Result<Arc<dyn Engine>, String>;
}

/// Engine factories by id. Registering an id again replaces its factory.
#[derive(Clone, Default)]
pub struct EngineRegistry {
    factories: BTreeMap<String, Arc<dyn EngineFactory>>,
}

impl EngineRegistry {
    pub fn new() -> Self { Self::default() }

    /// Registry with the built-in `claude-code` and `codex` engines.
    pub fn builtin() -> Self {
        let mut registry = Self::new();
        registry.register(claude_code::ClaudeCodeFactory::default());
        registry.register(codex_app_server::CodexAppServerFactory::default());
        registry
    }

    pub fn register(&mut self, factory: impl EngineFactory + 'static) -> &mut Self {
        self.factories.insert(factory.id().to_owned(), Arc::new(factory));
        self
    }

    pub fn get(&self, id: &str) -> Option<&Arc<dyn EngineFactory>> { self.factories.get(id) }

    pub fn ids(&self) -> impl Iterator<Item = &str> { self.factories.keys().map(String::as_str) }

    /// Build the engine configured for `bot`.
    pub fn build(&self, bot: &BotConfig) -> Result<Arc<dyn Engine>, String> {
        let factory = self.get(&bot.engine).ok_or_else(|| format!(
            "unknown engine '{}' for bot '{}'; available engines: {}",
            bot.engine, bot.provider_bot_ref, self.ids().collect::<Vec<_>>().join(", ")))?;
        let bin = if factory.requires_bin() {
            bot.engine_bin.clone().unwrap_or_else(|| PathBuf::from(factory.default_bin()))
        } else {
            if bot.engine_bin.is_some() { return Err(format!("engine '{}' does not accept engine_bin", bot.engine)); }
            PathBuf::new()
        };
        factory.build(bin, &bot.engine_options)
            .map_err(|error| format!("engine '{}' for bot '{}': {error}", bot.engine, bot.provider_bot_ref))
    }
}

/// Reject `engine_options` for engines that define none.
pub fn reject_engine_options(options: &toml::Table) -> Result<(), String> {
    match options.keys().next() {
        None => Ok(()),
        Some(key) => Err(format!("unsupported engine_options key '{key}'")),
    }
}

/// Validate an engine-native session id before it is persisted or used as a
/// `--resume` argv argument or a thread id. An engine must never be a trusted
/// source for these — a buggy or hostile engine could supply `../../evil` (path traversal) or `--evil`
/// (argv option injection). Rules: non-empty; no leading dash (argv option
/// guard); no path separators or parent refs; only ascii alphanumeric plus
/// `-`/`_`/`.`. Engines call this at their capture sites (claude-code
/// `system/init`, codex `thread/start`); invalid ids must not be persisted or resumed.
pub fn is_valid_engine_session_id(id: &str) -> bool {
    !id.is_empty()
        && !id.starts_with('-')
        && !id.contains(['/', '\\'])
        && !id.contains("..")
        && id.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_protocol::stream::StreamEvent;

    struct FakeEngine;
    struct TestObserver;
    #[async_trait::async_trait]
    impl SessionObserver for TestObserver {
        async fn established(&self, _: &str) -> Result<(), String> { Ok(()) }
    }
    #[async_trait::async_trait]
    impl Engine for FakeEngine {
        async fn run_turn(&self, req: TurnRequest,
                          events: tokio::sync::mpsc::Sender<StreamEvent>,
                          _abort: tokio_util::sync::CancellationToken)
            -> Result<TurnOutcome, TurnError> {
            let _ = events.send(crate::sse::chat_delta(&req.run_id, "fake")).await;
            Ok(TurnOutcome { engine_session_id: Some("e-1".into()), final_text: Some("done".into()) })
        }
    }

    struct FakeFactory { id: &'static str }
    impl EngineFactory for FakeFactory {
        fn id(&self) -> &str { self.id }
        fn default_bin(&self) -> &str { "fake-engine" }
        fn build(&self, _bin: PathBuf, options: &toml::Table) -> Result<Arc<dyn Engine>, String> {
            reject_engine_options(options)?;
            Ok(Arc::new(FakeEngine))
        }
    }

    fn bot(engine: &str) -> BotConfig {
        BotConfig {
            bot_id: None,
            token: None,
            provider_bot_ref: "worker".into(),
            engine: engine.into(),
            model: None,
            cwd: "/tmp".into(),
            permission_mode: None,
            engine_bin: None,
            engine_options: toml::Table::new(),
        }
    }

    #[tokio::test]
    async fn fake_engine_emits_delta() {
        let (tx, mut rx) = tokio::sync::mpsc::channel(8);
        let engine = FakeEngine;
        let req = TurnRequest::new("r-1", "hi", ".", Arc::new(TestObserver));
        let outcome = engine.run_turn(req, tx, tokio_util::sync::CancellationToken::new()).await.unwrap();
        assert_eq!(outcome.engine_session_id.as_deref(), Some("e-1"));
        assert!(rx.recv().await.is_some());
    }

    #[test]
    fn builtin_registry_builds_claude_code_and_codex() {
        let registry = EngineRegistry::builtin();
        assert_eq!(registry.ids().collect::<Vec<_>>(), ["claude-code", "codex"]);
        assert_eq!(registry.get("claude-code").unwrap().default_bin(), "claude");
        assert_eq!(registry.get("codex").unwrap().default_bin(), "codex");
        assert!(registry.build(&bot("claude-code")).is_ok());
        assert!(registry.build(&bot("codex")).is_ok());
    }

    #[test]
    fn registry_rejects_unknown_engines_and_unsupported_options() {
        let registry = EngineRegistry::builtin();
        let error = registry.build(&bot("bogus")).err().unwrap();
        assert!(error.contains("unknown engine 'bogus'") && error.contains("claude-code, codex"), "{error}");
        let mut configured = bot("codex");
        configured.engine_options.insert("sandbox".into(), "workspace-write".into());
        let error = registry.build(&configured).err().unwrap();
        assert!(error.contains("unsupported engine_options key 'sandbox'"), "{error}");
    }

    #[test]
    fn registering_an_existing_id_replaces_its_factory_and_new_ids_are_added() {
        let mut registry = EngineRegistry::builtin();
        registry.register(FakeFactory { id: "codex" }).register(FakeFactory { id: "custom" });
        assert_eq!(registry.get("codex").unwrap().default_bin(), "fake-engine");
        assert_eq!(registry.ids().collect::<Vec<_>>(), ["claude-code", "codex", "custom"]);
        assert!(registry.build(&bot("custom")).is_ok());
    }

    #[test]
    fn is_valid_engine_session_id_accepts_safe_ids() {
        assert!(is_valid_engine_session_id("cc-sess-1"));
        assert!(is_valid_engine_session_id("01a058a5-5bb3-7702-bc4c-7d26b3bfa32d"));
        // underscores and dots are in the allowed set; a single dot is fine.
        assert!(is_valid_engine_session_id("thread_42"));
        assert!(is_valid_engine_session_id("sess.1"));
    }

    #[test]
    fn is_valid_engine_session_id_rejects_unsafe_ids() {
        // empty
        assert!(!is_valid_engine_session_id(""), "empty rejected");
        // path separators (path traversal)
        assert!(!is_valid_engine_session_id("a/b"), "forward slash rejected");
        assert!(!is_valid_engine_session_id("a\\b"), "backslash rejected");
        assert!(!is_valid_engine_session_id("../x"), "parent ref rejected");
        assert!(!is_valid_engine_session_id("a..b"), "embedded parent ref rejected");
        // leading dash (argv option injection)
        assert!(!is_valid_engine_session_id("--evil"), "leading dash rejected");
        // whitespace / other disallowed chars
        assert!(!is_valid_engine_session_id("a b"), "space rejected");
        assert!(!is_valid_engine_session_id("a:b"), "colon rejected");
        assert!(!is_valid_engine_session_id("café"), "non-ascii rejected");
    }
}
