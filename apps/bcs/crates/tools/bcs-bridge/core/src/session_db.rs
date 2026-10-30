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
        if !matches!(version, 0 | 1 | 2 | 3 | 4) {
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
        tx.execute("UPDATE injects SET state = 'pending', run_id = NULL WHERE state = 'inflight'", [])?;
        if version < 3 {
            // Rebuild parent and child together: retain mappings, receipts and
            // sequence numbers while making the working directory a durable key.
            tx.execute_batch("
                CREATE TABLE sessions_v3 (
                    provider TEXT NOT NULL, bot TEXT NOT NULL, session TEXT NOT NULL,
                    engine TEXT NOT NULL, cwd TEXT NOT NULL, engine_session_id TEXT,
                    PRIMARY KEY (provider, bot, session, engine, cwd)
                );
                INSERT INTO sessions_v3 SELECT * FROM sessions;
                CREATE TABLE injects_v3 (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL, id TEXT NOT NULL,
                    bot TEXT NOT NULL, session TEXT NOT NULL, engine TEXT NOT NULL, cwd TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, from_name TEXT, text TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending','inflight','delivered')), run_id TEXT,
                    UNIQUE (provider, id),
                    FOREIGN KEY (provider,bot,session,engine,cwd)
                        REFERENCES sessions_v3(provider,bot,session,engine,cwd)
                );
                INSERT INTO injects_v3
                    SELECT i.seq,i.provider,i.id,i.bot,i.session,s.engine,s.cwd,
                           i.fingerprint,i.from_name,i.text,i.state,i.run_id
                    FROM injects i JOIN sessions s USING (provider,bot,session);
                DROP TABLE injects;
                DROP TABLE sessions;
                ALTER TABLE sessions_v3 RENAME TO sessions;
                ALTER TABLE injects_v3 RENAME TO injects;
                CREATE INDEX injects_pending ON injects(provider,bot,session,engine,cwd,state,seq);
                PRAGMA user_version = 3;
            ")?;
        }
        if version < 4 {
            // A2A task snapshots: queryable after the in-memory run is gone and
            // across Bridge restarts. `snapshot` is the A2A `Task` JSON.
            tx.execute_batch("
                CREATE TABLE a2a_tasks (
                    provider TEXT NOT NULL, task_id TEXT NOT NULL,
                    owner TEXT NOT NULL, bot TEXT NOT NULL,
                    state TEXT NOT NULL, snapshot TEXT NOT NULL, updated_at INTEGER NOT NULL,
                    PRIMARY KEY (provider, task_id)
                );
                CREATE INDEX a2a_tasks_updated ON a2a_tasks(provider, updated_at);
                PRAGMA user_version = 4;
            ")?;
        }
        // No other Bridge owns this database. Interrupted attempts are retried
        // on the next send, never by starting an engine during recovery.
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
    // The first indexed row fixes the engine; native Codex may have many
    // directories. LIMIT 1 keeps validation bounded as projects accumulate.
    let binding: Option<(String, String)> = tx.query_row(
        "SELECT engine,cwd FROM sessions WHERE provider=?1 AND bot=?2 AND session=?3 LIMIT 1",
        params![key.provider, key.bot, key.session], |row| Ok((row.get(0)?, row.get(1)?)),
    ).optional()?;
    if binding.is_some_and(|(engine, cwd)| engine != key.engine
        || (cwd != key.cwd && !matches!(key.engine.as_str(), "codex" | "codex-desktop"))) {
        return Err(SessionError::BindingChanged);
    }
    tx.execute(
        "INSERT INTO sessions(provider, bot, session, engine, cwd) VALUES (?1, ?2, ?3, ?4, ?5)
         ON CONFLICT(provider, bot, session, engine, cwd) DO NOTHING",
        params![key.provider, key.bot, key.session, key.engine, key.cwd],
    )?;
    let (engine, cwd, sid): (String, String, Option<String>) = tx.query_row(
        "SELECT engine, cwd, engine_session_id FROM sessions WHERE provider=?1 AND bot=?2 AND session=?3 AND engine=?4 AND cwd=?5",
        params![key.provider, key.bot, key.session, key.engine, key.cwd],
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
        "UPDATE sessions SET engine_session_id=?4 WHERE provider=?1 AND bot=?2 AND session=?3 AND engine=?5 AND cwd=?6",
        params![key.provider, key.bot, key.session, sid, key.engine, key.cwd],
    )?;
    tx.commit()?;
    Ok(())
}

pub(crate) fn enqueue(
    conn: &mut Connection, key: &SessionKey, msg: &InjectedMessage, fingerprint: &str,
) -> Result<(), SessionError> {
    let tx = conn.transaction()?;
    ensure_session(&tx, key)?;
    let previous: Option<(String, String, String, String, String)> = tx.query_row(
        "SELECT bot, session, fingerprint, engine, cwd FROM injects WHERE provider=?1 AND id=?2",
        params![key.provider, msg.run_id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
    ).optional()?;
    if let Some((bot, session, fp, engine, cwd)) = previous {
        if bot != key.bot || session != key.session || fp != fingerprint || engine != key.engine || cwd != key.cwd {
            return Err(SessionError::Conflict);
        }
    } else {
        tx.execute(
            "INSERT INTO injects(provider, id, bot, session, fingerprint, from_name, text, state, engine, cwd)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'pending', ?8, ?9)",
            params![key.provider, msg.run_id, key.bot, key.session, fingerprint, msg.from_name, msg.text, key.engine, key.cwd],
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
             WHERE provider=?1 AND bot=?2 AND session=?3 AND engine=?4 AND cwd=?5 AND state!='delivered' ORDER BY seq",
        )?;
        stmt.query_map(params![key.provider, key.bot, key.session, key.engine, key.cwd], |r| {
            Ok(InjectedMessage { run_id: r.get(0)?, from_name: r.get(1)?, text: r.get(2)? })
        })?.collect::<Result<Vec<_>, _>>()?
    };
    tx.execute(
        "UPDATE injects SET state='inflight', run_id=?4
         WHERE provider=?1 AND bot=?2 AND session=?3 AND engine=?5 AND cwd=?6 AND state!='delivered'",
        params![key.provider, key.bot, key.session, run, key.engine, key.cwd],
    )?;
    tx.commit()?;
    Ok(messages)
}

pub(crate) fn finish_batch(conn: &mut Connection, key: &SessionKey, run: &str, delivered: bool) -> Result<(), SessionError> {
    let tx = conn.transaction()?;
    tx.execute(
        "UPDATE injects SET state=?5, run_id=NULL
         WHERE provider=?1 AND bot=?2 AND session=?3 AND state='inflight' AND run_id=?4 AND engine=?6 AND cwd=?7",
        params![key.provider, key.bot, key.session, run, if delivered { "delivered" } else { "pending" }, key.engine, key.cwd],
    )?;
    tx.commit()?;
    Ok(())
}

/// One persisted A2A task snapshot row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct A2aTaskRow {
    pub task_id: String,
    pub owner: String,
    pub bot: String,
    pub state: String,
    pub snapshot: String,
}

pub(crate) fn save_a2a_task(conn: &mut Connection, provider: &str, row: &A2aTaskRow, now_ms: u64) -> Result<(), SessionError> {
    conn.execute(
        "INSERT INTO a2a_tasks(provider,task_id,owner,bot,state,snapshot,updated_at) VALUES (?1,?2,?3,?4,?5,?6,?7)
         ON CONFLICT(provider,task_id) DO UPDATE SET state=excluded.state, snapshot=excluded.snapshot, updated_at=excluded.updated_at",
        params![provider, row.task_id, row.owner, row.bot, row.state, row.snapshot, now_ms as i64],
    )?;
    Ok(())
}

pub(crate) fn load_a2a_task(conn: &Connection, provider: &str, task_id: &str) -> Result<Option<A2aTaskRow>, SessionError> {
    Ok(conn.query_row(
        "SELECT task_id,owner,bot,state,snapshot FROM a2a_tasks WHERE provider=?1 AND task_id=?2",
        params![provider, task_id],
        |row| Ok(A2aTaskRow { task_id: row.get(0)?, owner: row.get(1)?, bot: row.get(2)?, state: row.get(3)?, snapshot: row.get(4)? }),
    ).optional()?)
}

/// Rows whose state is not one of `terminal`, e.g. tasks interrupted by a restart.
pub(crate) fn unfinished_a2a_tasks(conn: &Connection, provider: &str, terminal: &[&str]) -> Result<Vec<A2aTaskRow>, SessionError> {
    let mut statement = conn.prepare("SELECT task_id,owner,bot,state,snapshot FROM a2a_tasks WHERE provider=?1")?;
    let rows = statement.query_map(params![provider], |row| Ok(A2aTaskRow {
        task_id: row.get(0)?, owner: row.get(1)?, bot: row.get(2)?, state: row.get(3)?, snapshot: row.get(4)? }))?;
    let mut unfinished = Vec::new();
    for row in rows {
        let row = row?;
        if !terminal.contains(&row.state.as_str()) { unfinished.push(row); }
    }
    Ok(unfinished)
}

pub(crate) fn prune_a2a_tasks(conn: &mut Connection, provider: &str, before_ms: u64) -> Result<usize, SessionError> {
    Ok(conn.execute("DELETE FROM a2a_tasks WHERE provider=?1 AND updated_at < ?2", params![provider, before_ms as i64])?)
}
