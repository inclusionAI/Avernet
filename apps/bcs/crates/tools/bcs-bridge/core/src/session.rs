//! Durable session mappings and inject receipts; active runs remain local.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::config::BotConfig;
use crate::engine::SessionObserver;
use crate::session_db::{self, SessionDb, SessionKey};
pub use crate::session_db::SessionError;

#[derive(Clone, PartialEq, Eq)]
pub struct ConnectionIdentity {
    pub bot_id: String,
    pub token: String,
}

impl std::fmt::Debug for ConnectionIdentity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ConnectionIdentity")
            .field("bot_id", &self.bot_id)
            .field("token", &"[redacted]")
            .finish()
    }
}

#[derive(Debug, Clone)]
pub struct InjectedMessage {
    pub run_id: String,
    pub from_name: Option<String>,
    pub text: String,
}

#[derive(Debug, Clone, Default)]
pub struct SessionMapping {
    pub engine_session_id: Option<String>,
}

#[derive(Debug, thiserror::Error)]
#[error("session already has an active run")]
pub struct SessionBusy;

type Key = (String, String); // (provider_bot_ref, bcs_session_id)

#[derive(Clone)]
pub struct SessionStore {
    db: Arc<SessionDb>,
    provider: String,
    bindings: Arc<HashMap<String, (String, String)>>,
    active: Arc<RwLock<HashMap<Key, String>>>,
}

impl SessionStore {
    pub fn open(path: &Path, provider: &str, bots: &[BotConfig]) -> Result<Self, SessionError> {
        let mut bindings = HashMap::new();
        for bot in bots {
            let cwd = std::fs::canonicalize(&bot.cwd)?.to_string_lossy().into_owned();
            if bindings.insert(bot.provider_bot_ref.clone(), (bot.engine.clone(), cwd)).is_some() {
                return Err(SessionError::Bot(bot.provider_bot_ref.clone()));
            }
        }
        Ok(Self {
            db: Arc::new(SessionDb::open(path)?),
            provider: provider.into(),
            bindings: Arc::new(bindings),
            active: Arc::new(RwLock::new(HashMap::new())),
        })
    }

    fn key(&self, bot: &str, session: &str) -> Result<SessionKey, SessionError> {
        let (engine, cwd) = self.bindings.get(bot).ok_or_else(|| SessionError::Bot(bot.into()))?;
        Ok(SessionKey { provider: self.provider.clone(), bot: bot.into(), session: session.into(), engine: engine.clone(), cwd: cwd.clone() })
    }

    async fn access<T, F>(&self, work: F) -> Result<T, SessionError>
    where T: Send + 'static, F: FnOnce(&mut rusqlite::Connection) -> Result<T, SessionError> + Send + 'static {
        let db = self.db.clone();
        tokio::task::spawn_blocking(move || {
            let mut conn = db.connection.lock().map_err(|_| SessionError::Poisoned)?;
            work(&mut conn)
        }).await.map_err(|e| SessionError::Worker(e.to_string()))?
    }

    pub async fn mapping(&self, bot: &str, s: &str) -> Result<SessionMapping, SessionError> {
        let key = self.key(bot, s)?;
        let engine_session_id = self.access(move |conn| session_db::mapping(conn, &key)).await?;
        Ok(SessionMapping { engine_session_id })
    }

    /// Reconnect credentials are isolated by local namespace, server and Bot.
    pub async fn load_identity(&self, server: &str, bot_ref: &str) -> Result<Option<ConnectionIdentity>, SessionError> {
        if !self.bindings.contains_key(bot_ref) { return Err(SessionError::Bot(bot_ref.into())); }
        let provider = self.provider.clone();
        let server = server.to_owned();
        let bot = bot_ref.to_owned();
        self.access(move |conn| session_db::load_identity(conn, &provider, &server, &bot)).await
    }

    /// Returns only after SQLite commits; callers must validate the Bot identity
    /// against both configuration and the restored identity before saving it.
    pub async fn save_identity(&self, server: &str, bot_ref: &str, identity: ConnectionIdentity) -> Result<(), SessionError> {
        if !self.bindings.contains_key(bot_ref) { return Err(SessionError::Bot(bot_ref.into())); }
        let provider = self.provider.clone();
        let server = server.to_owned();
        let bot = bot_ref.to_owned();
        self.access(move |conn| session_db::save_identity(conn, &provider, &server, &bot, &identity)).await
    }

    pub async fn set_engine_session_id(&self, bot: &str, s: &str, engine_id: &str) -> Result<(), SessionError> {
        let key = self.key(bot, s)?;
        let sid = engine_id.to_owned();
        self.access(move |conn| session_db::record_session(conn, &key, &sid)).await
    }

    pub async fn enqueue_inject(&self, bot: &str, s: &str, msg: InjectedMessage, fingerprint: String) -> Result<(), SessionError> {
        let key = self.key(bot, s)?;
        self.access(move |conn| session_db::enqueue(conn, &key, &msg, &fingerprint)).await
    }

    pub async fn claim_injects(&self, bot: &str, s: &str, run_id: &str) -> Result<Vec<InjectedMessage>, SessionError> {
        let key = self.key(bot, s)?;
        let run = run_id.to_owned();
        self.access(move |conn| session_db::claim(conn, &key, &run)).await
    }

    pub async fn complete_injects(&self, bot: &str, s: &str, run_id: &str) -> Result<(), SessionError> {
        self.finish_batch(bot, s, run_id, true).await
    }

    pub async fn release_injects(&self, bot: &str, s: &str, run_id: &str) -> Result<(), SessionError> {
        self.finish_batch(bot, s, run_id, false).await
    }

    async fn finish_batch(&self, bot: &str, s: &str, run_id: &str, delivered: bool) -> Result<(), SessionError> {
        let key = self.key(bot, s)?;
        let run = run_id.to_owned();
        self.access(move |conn| session_db::finish_batch(conn, &key, &run, delivered)).await
    }

    pub fn observer(&self, bot: &str, session: &str, run: &str) -> Arc<dyn SessionObserver> {
        Arc::new(BoundSession { store: self.clone(), bot: bot.into(), session: session.into(), run: run.into() })
    }

    pub async fn try_start_run(&self, bot: &str, s: &str, run_id: &str) -> Result<(), SessionBusy> {
        let mut active = self.active.write().await;
        let key = (bot.into(), s.into());
        if active.contains_key(&key) { return Err(SessionBusy); }
        active.insert(key, run_id.into());
        Ok(())
    }

    pub async fn finish_run(&self, bot: &str, s: &str, run_id: &str) {
        let mut active = self.active.write().await;
        let key = (bot.into(), s.into());
        if active.get(&key).is_some_and(|r| r == run_id) { active.remove(&key); }
    }

    pub async fn active_run(&self, bot: &str, s: &str) -> Option<String> {
        self.active.read().await.get(&(bot.into(), s.into())).cloned()
    }
}

struct BoundSession {
    store: SessionStore,
    bot: String,
    session: String,
    run: String,
}

#[async_trait::async_trait]
impl SessionObserver for BoundSession {
    async fn established(&self, engine_id: &str) -> Result<(), String> {
        self.store.set_engine_session_id(&self.bot, &self.session, engine_id).await.map_err(|e| e.to_string())?;
        tracing::info!(provider_id = %self.store.provider, provider_bot_ref = %self.bot,
            bcs_session_id = %self.session, run_id = %self.run, engine_session_id = %engine_id,
            "engine session mapping persisted");
        Ok(())
    }
}

#[cfg(test)]
#[path = "session_tests.rs"]
mod tests;
