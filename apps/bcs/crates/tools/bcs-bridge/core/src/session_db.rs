//! Bridge-owned durable state. This module never opens engine transcripts.

use std::fs::{File, OpenOptions};
use std::path::Path;
use std::sync::Mutex;

use rusqlite::{Connection, OptionalExtension, Transaction, params};

use crate::session::{ConnectionIdentity, InjectedMessage};

#[derive(Debug, thiserror::Error)]
pub enum SessionError {
    #[error("session storage: {0}")]
    Database(#[from] rusqlite::Error),
    #[error("session storage io: {0}")]
    Io(#[from] std::io::Error),
    #[error("session database already in use: {0}")]
    Locked(String),
    #[error("unsupported session schema version: {0}")]
    Schema(i64),
    #[error("session storage worker failed: {0}")]
    Worker(String),
    #[error("session storage lock poisoned")]
    Poisoned,
    #[error("unknown or duplicate bot binding: {0}")]
    Bot(String),
    #[error("engine or working directory changed for this session")]
    BindingChanged,
    #[error("engine changed the resumed session id")]
    SessionChanged,
    #[error("invalid engine session id")]
    InvalidSessionId,
    #[error("same inject id with different payload")]
    Conflict,
}

#[derive(Clone)]
pub(crate) struct SessionKey {
    pub provider: String,
    pub bot: String,
    pub session: String,
    pub engine: String,
    pub cwd: String,
}

pub(crate) struct SessionDb {
    pub connection: Mutex<Connection>,
    // Protect startup recovery and process-local run ownership across processes.
    // Fields drop in declaration order: close SQLite before releasing ownership.
    _owner: OwnerLock,
}

struct OwnerLock(File);

impl Drop for OwnerLock {
    fn drop(&mut self) {
        // An unrelated concurrently spawned child can inherit this descriptor
        // until exec, even with CLOEXEC. Closing only our copy would leave the
        // shared open description locked until that child closes its copy.
        if let Err(error) = self.0.unlock() {
            tracing::warn!(%error, "failed to explicitly release session database owner lock");
        }
    }
}

impl SessionDb {
    pub fn open(path: &Path) -> Result<Self, SessionError> {
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            std::fs::create_dir_all(parent)?;
        }
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        // Create private database files before SQLite opens them (sidecars
        // inherit database permissions). Do not truncate existing databases.
        drop(options.open(path)?);
        // Different config paths/symlinks to the same database must share its
        // owner lock; otherwise startup could reclaim another live run's batch.
        let path = std::fs::canonicalize(path)?;
        let mut lock_path = path.as_os_str().to_os_string();
        lock_path.push(".lock");
        let owner = options.open(lock_path)?;
        owner.try_lock().map_err(|e| SessionError::Locked(e.to_string()))?;
        let owner = OwnerLock(owner);
        let mut connection = Connection::open(&path)?;
        connection.busy_timeout(std::time::Duration::from_secs(5))?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "synchronous", "FULL")?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        let version: i64 = connection.pragma_query_value(None, "user_version", |r| r.get(0))?;
        if !matches!(version, 0 | 1 | 2) {
            return Err(SessionError::Schema(version));
        }
        let tx = connection.transaction()?;
        if version == 0 {
            tx.execute_batch("
                CREATE TABLE sessions (
                    provider TEXT NOT NULL, bot TEXT NOT NULL, session TEXT NOT NULL,
                    engine TEXT NOT NULL, cwd TEXT NOT NULL, engine_session_id TEXT,
                    PRIMARY KEY (provider, bot, session)
                );
                CREATE TABLE injects (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL, id TEXT NOT NULL,
                    bot TEXT NOT NULL, session TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, from_name TEXT, text TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending','inflight','delivered')),
                    run_id TEXT,
                    UNIQUE (provider, id),
                    FOREIGN KEY (provider, bot, session) REFERENCES sessions(provider, bot, session)
                );
                CREATE INDEX injects_pending ON injects(provider, bot, session, state, seq);
            ")?;
        }
        if version < 2 {
            tx.execute_batch("
                CREATE TABLE connection_identities (
                    provider TEXT NOT NULL, server TEXT NOT NULL, bot TEXT NOT NULL,
                    bot_id TEXT NOT NULL, token TEXT NOT NULL,
                    PRIMARY KEY (provider, server, bot)
                );
                PRAGMA user_version = 2;
            ")?;
        }
        // No other Bridge owns this database. Interrupted attempts are retried
        // on the next send, never by starting an engine during recovery.
        tx.execute("UPDATE injects SET state = 'pending', run_id = NULL WHERE state = 'inflight'", [])?;
        tx.commit()?;
        Ok(Self { connection: Mutex::new(connection), _owner: owner })
    }
}

pub(crate) fn load_identity(conn: &Connection, provider: &str, server: &str, bot: &str) -> Result<Option<ConnectionIdentity>, SessionError> {
    Ok(conn.query_row(
        "SELECT bot_id, token FROM connection_identities WHERE provider=?1 AND server=?2 AND bot=?3",
        params![provider, server, bot],
        |row| Ok(ConnectionIdentity { bot_id: row.get(0)?, token: row.get(1)? }),
    ).optional()?)
}

pub(crate) fn save_identity(conn: &mut Connection, provider: &str, server: &str, bot: &str, identity: &ConnectionIdentity) -> Result<(), SessionError> {
    let tx = conn.transaction()?;
    tx.execute(
        "INSERT INTO connection_identities(provider,server,bot,bot_id,token) VALUES (?1,?2,?3,?4,?5)
         ON CONFLICT(provider,server,bot) DO UPDATE SET bot_id=excluded.bot_id, token=excluded.token",
        params![provider, server, bot, identity.bot_id, identity.token],
    )?;
    tx.commit()?;
    Ok(())
}

fn ensure_session(tx: &Transaction<'_>, key: &SessionKey) -> Result<Option<String>, SessionError> {
    tx.execute(
        "INSERT INTO sessions(provider, bot, session, engine, cwd) VALUES (?1, ?2, ?3, ?4, ?5)
         ON CONFLICT(provider, bot, session) DO NOTHING",
        params![key.provider, key.bot, key.session, key.engine, key.cwd],
    )?;
    let (engine, cwd, sid): (String, String, Option<String>) = tx.query_row(
        "SELECT engine, cwd, engine_session_id FROM sessions WHERE provider=?1 AND bot=?2 AND session=?3",
        params![key.provider, key.bot, key.session],
        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
    )?;
    if engine != key.engine || cwd != key.cwd {
        return Err(SessionError::BindingChanged);
    }
    if sid.as_deref().is_some_and(|s| !crate::engine::is_valid_engine_session_id(s)) {
        return Err(SessionError::InvalidSessionId);
    }
    Ok(sid)
}

pub(crate) fn mapping(conn: &mut Connection, key: &SessionKey) -> Result<Option<String>, SessionError> {
    let tx = conn.transaction()?;
    let sid = ensure_session(&tx, key)?;
    tx.commit()?;
    Ok(sid)
}

pub(crate) fn record_session(conn: &mut Connection, key: &SessionKey, sid: &str) -> Result<(), SessionError> {
    if !crate::engine::is_valid_engine_session_id(sid) {
        return Err(SessionError::InvalidSessionId);
    }
    let tx = conn.transaction()?;
    if ensure_session(&tx, key)?.as_deref().is_some_and(|old| old != sid) {
        return Err(SessionError::SessionChanged);
    }
    tx.execute(
        "UPDATE sessions SET engine_session_id=?4 WHERE provider=?1 AND bot=?2 AND session=?3",
        params![key.provider, key.bot, key.session, sid],
    )?;
    tx.commit()?;
    Ok(())
}

pub(crate) fn enqueue(
    conn: &mut Connection, key: &SessionKey, msg: &InjectedMessage, fingerprint: &str,
) -> Result<(), SessionError> {
    let tx = conn.transaction()?;
    ensure_session(&tx, key)?;
    let previous: Option<(String, String, String)> = tx.query_row(
        "SELECT bot, session, fingerprint FROM injects WHERE provider=?1 AND id=?2",
        params![key.provider, msg.run_id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
    ).optional()?;
    if let Some((bot, session, fp)) = previous {
        if bot != key.bot || session != key.session || fp != fingerprint {
            return Err(SessionError::Conflict);
        }
    } else {
        tx.execute(
            "INSERT INTO injects(provider, id, bot, session, fingerprint, from_name, text, state)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'pending')",
            params![key.provider, msg.run_id, key.bot, key.session, fingerprint, msg.from_name, msg.text],
        )?;
    }
    tx.commit()?;
    Ok(())
}

pub(crate) fn claim(conn: &mut Connection, key: &SessionKey, run: &str) -> Result<Vec<InjectedMessage>, SessionError> {
    let tx = conn.transaction()?;
    ensure_session(&tx, key)?;
    // The caller holds this session's run slot. Recover a previous failed
    // release as well as normal pending messages without losing receipts.
    let messages = {
        let mut stmt = tx.prepare(
            "SELECT id, from_name, text FROM injects
             WHERE provider=?1 AND bot=?2 AND session=?3 AND state!='delivered' ORDER BY seq",
        )?;
        stmt.query_map(params![key.provider, key.bot, key.session], |r| {
            Ok(InjectedMessage { run_id: r.get(0)?, from_name: r.get(1)?, text: r.get(2)? })
        })?.collect::<Result<Vec<_>, _>>()?
    };
    tx.execute(
        "UPDATE injects SET state='inflight', run_id=?4
         WHERE provider=?1 AND bot=?2 AND session=?3 AND state!='delivered'",
        params![key.provider, key.bot, key.session, run],
    )?;
    tx.commit()?;
    Ok(messages)
}

pub(crate) fn finish_batch(conn: &mut Connection, key: &SessionKey, run: &str, delivered: bool) -> Result<(), SessionError> {
    let tx = conn.transaction()?;
    tx.execute(
        "UPDATE injects SET state=?5, run_id=NULL
         WHERE provider=?1 AND bot=?2 AND session=?3 AND state='inflight' AND run_id=?4",
        params![key.provider, key.bot, key.session, run, if delivered { "delivered" } else { "pending" }],
    )?;
    tx.commit()?;
    Ok(())
}
