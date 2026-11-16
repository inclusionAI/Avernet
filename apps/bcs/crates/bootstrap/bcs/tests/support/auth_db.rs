//! Task 4's `DbPlugin` decorator pattern, extended for the Task 13 HTTP
//! acceptance suites (V1 API auth plan): every store operation the strict
//! auth session chain issues is classified by its NAMED statement, counted,
//! and can be paused (an explicit reached/release gate — a barrier inside
//! the decorator at a named read/write point, never a sleep) or faulted
//! (`DbError::Backend("injected")` — the ONLY fault injection point, at
//! the plugin boundary).
//!
//! The wrapper is used to build the REAL `DbUserIdentityStore` through the
//! REAL bootstrap identity wiring, so faults and gates observe the chain
//! exactly where production would hit them. Classification keys on the
//! statement text pinned by `bcs-user-identity/src/session_db.rs`:
//!
//! - `SELECT session_revision FROM bcs_user_identities`
//!   — the login batch's revision read;
//! - `SELECT session_id, session_revision, session_expires_at,` — the one
//!   strict verify SELECT (refresh reads A here);
//! - `SET token = ?, … AND session_id = ? AND session_revision = ? AND
//!   token = ?` — the rotate CAS (refresh's conditional write);
//! - `SET token = NULL, …` — the by-sid revoke;
//! - `SET token = ?, … WHERE … AND session_revision = ?` — the login
//!   install CAS.

#![allow(dead_code)] // shared support: each suite target uses a subset
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use bcs_db_api::{
    DbError, DbExecuteResult, DbHealth, DbPlugin, DbResult, DbRow, DbStatement,
    DbTransactionStep, DbTransactionStepResult,
};
use bcs_db_local::LocalSqliteDbPlugin;

/// One classified statement kind of the strict session chain.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum NamedOp {
    StrictSelect,
    RevisionRead,
    Install,
    Rotate,
    Revoke,
}

/// Whether a gate / fault applies before or after the delegation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Phase {
    Pre,
    Post,
}

/// Classify a statement by the pinned SQL fragments of the session store.
/// Everything else (identity ensure/lookups, migrations…) is `None` and
/// simply delegates.
pub fn classify(sql: &str) -> Option<NamedOp> {
    let sql = sql.trim_start();
    if sql.starts_with("SELECT session_revision FROM bcs_user_identities") {
        return Some(NamedOp::RevisionRead);
    }
    if sql.starts_with("SELECT session_id, session_revision") {
        return Some(NamedOp::StrictSelect);
    }
    if sql.contains("SET token = NULL, token_expire_at = NULL") {
        return Some(NamedOp::Revoke);
    }
    if sql.contains("SET token = ?") {
        return Some(NamedOp::Rotate);
    }
    None
}

/// One-shot reached/release gate — a deterministic barrier between the
/// decorator (which has arrived at a named point) and the test (which
/// decides when the operation may continue). No timing, no sleep.
pub struct Gate {
    reached: tokio::sync::oneshot::Sender<()>,
    release: tokio::sync::oneshot::Receiver<()>,
}

impl Gate {
    fn create() -> (Gate, HeldGate) {
        let (reached_tx, reached_rx) = tokio::sync::oneshot::channel();
        let (release_tx, release_rx) = tokio::sync::oneshot::channel();
        (
            Gate {
                reached: reached_tx,
                release: release_rx,
            },
            HeldGate {
                reached: reached_rx,
                release: release_tx,
            },
        )
    }

    /// The in-request side: announce arrival, then block until released.
    async fn arrive(mut self) {
        let _ = self.reached.send(());
        // tokio's oneshot receiver is awaited in place.
        let _ = (&mut self.release).await;
    }
}

/// The test side of a [`Gate`].
pub struct HeldGate {
    reached: tokio::sync::oneshot::Receiver<()>,
    release: tokio::sync::oneshot::Sender<()>,
}

impl HeldGate {
    /// Wait until the in-request side has arrived at the named point.
    pub async fn wait_reached(&mut self) {
        // A dropped in-request side (e.g. the request errored before the
        // point) also unblocks: the follow-up assertions then fail on
        // their own, never on a stuck test.
        let _ = (&mut self.reached).await;
    }

    /// Let the paused operation continue.
    pub fn release(self) {
        let _ = self.release.send(());
    }
}

/// A shared-barrier gate: the first `parties` arrivals at the named point
/// all wait for one another (the named-point barrier the coalescing races
/// need: e.g. TWO refreshes both read A before either rotates).
pub struct BarrierGate {
    barrier: Arc<tokio::sync::Barrier>,
}

/// The shared control plane: per-op counters, the fault plan, and the
/// installed gates — shared by every wrapper over the same harness so
/// V1/legacy entries and independent store instances coordinate through
/// one observable point.
#[derive(Default)]
pub struct DbControl {
    pub strict_selects: AtomicU64,
    pub rows_returned: AtomicU64,
    pub revision_reads: AtomicU64,
    pub installs: AtomicU64,
    pub rotates: AtomicU64,
    pub applied_rotates: AtomicU64,
    pub conflicted_rotates: AtomicU64,
    pub revokes: AtomicU64,
    pub revoked_rows: AtomicU64,
    pub transactions: AtomicU64,
    faults: Mutex<Vec<NamedOp>>,
    gates: Mutex<BTreeMap<(NamedOp, Phase), PendingGate>>,
}

enum PendingGate {
    Once(Gate),
    Barrier { barrier: Arc<tokio::sync::Barrier>, remaining: u32 },
}

impl DbControl {
    /// Fail every operation of the named kinds with `Backend("injected")`
    /// instead of delegating (task 4's fault matrix, kept at the plugin
    /// boundary).
    pub fn set_faults(&self, ops: &[NamedOp]) {
        *self.faults.lock().unwrap() = ops.to_vec();
    }

    pub fn clear_faults(&self) {
        self.faults.lock().unwrap().clear();
    }

    fn fails(&self, op: NamedOp) -> bool {
        self.faults.lock().unwrap().contains(&op)
    }

    /// Install a one-shot pause at a named point; the returned handle
    /// waits for the request's arrival and then releases it.
    pub fn install_gate(&self, op: NamedOp, phase: Phase) -> HeldGate {
        let (gate, held) = Gate::create();
        self.gates
            .lock()
            .unwrap()
            .insert((op, phase), PendingGate::Once(gate));
        held
    }

    /// Install a `parties`-wide barrier at a named point — where
    /// `parties` counts the participating REQUEST arrivals PLUS the test
    /// itself (the returned [`BarrierGate::wait`] makes the test the
    /// last party). The map entry self-removes after `parties - 1`
    /// REQUEST arrivals, so later operations at the same point pass
    /// through (a tokio barrier is cyclic; a stale entry would trap
    /// them).
    pub fn install_barrier(&self, op: NamedOp, phase: Phase, parties: usize) -> BarrierGate {
        assert!(parties >= 2, "a barrier needs at least one request party plus the test");
        let barrier = Arc::new(tokio::sync::Barrier::new(parties));
        self.gates.lock().unwrap().insert(
            (op, phase),
            PendingGate::Barrier {
                barrier: barrier.clone(),
                remaining: (parties - 1) as u32,
            },
        );
        BarrierGate { barrier }
    }

    /// Take whatever is installed at a named point, if any. A barrier
    /// stays until its last party takes it.
    fn take_gate(&self, point: (NamedOp, Phase)) -> Option<TakenGate> {
        let mut gates = self.gates.lock().unwrap();
        // Inspect the pending kind first (immutably), then mutate.
        let barrier_state: Option<Option<Arc<tokio::sync::Barrier>>> = match gates.get(&point) {
            Some(PendingGate::Once(_)) => Some(None),
            Some(PendingGate::Barrier { barrier, remaining }) => {
                Some(Some((Arc::clone(barrier), *remaining)).map(|(barrier, _)| barrier))
            }
            None => None,
        };
        match barrier_state {
            // A one-shot gate leaves the moment its single party takes it.
            Some(None) => match gates.remove(&point) {
                Some(PendingGate::Once(gate)) => Some(TakenGate::Once(gate)),
                _ => unreachable!("checked Once above"),
            },
            Some(Some(_)) => {
                let (barrier, remaining) = match gates.remove(&point) {
                    Some(PendingGate::Barrier { barrier, remaining }) => (barrier, remaining),
                    _ => unreachable!("checked Barrier above"),
                };
                if remaining > 1 {
                    gates.insert(
                        point,
                        PendingGate::Barrier {
                            barrier: Arc::clone(&barrier),
                            remaining: remaining - 1,
                        },
                    );
                }
                Some(TakenGate::Barrier(barrier))
            }
            None => None,
        }
    }

    /// Reset every counter (gates and faults are untouched — they are
    /// per-scenario tools, not counters).
    pub fn reset_counts(&self) {
        for counter in [
            &self.strict_selects,
            &self.rows_returned,
            &self.revision_reads,
            &self.installs,
            &self.rotates,
            &self.applied_rotates,
            &self.conflicted_rotates,
            &self.revokes,
            &self.revoked_rows,
            &self.transactions,
        ] {
            counter.store(0, Ordering::SeqCst);
        }
    }
}

/// What a request holds at a named point: a one-shot gate (test releases
/// it) or a shared barrier party (the request parties release one another).
enum TakenGate {
    Once(Gate),
    Barrier(Arc<tokio::sync::Barrier>),
}

impl TakenGate {
    async fn arrive(self) {
        match self {
            TakenGate::Once(gate) => gate.arrive().await,
            // A barrier party is the arrival+release fused into one wait:
            // the operation continues only when every other party has also
            // arrived — the named-point barrier for the coalescing races.
            TakenGate::Barrier(barrier) => {
                let _ = barrier.wait().await;
            }
        }
    }
}

impl BarrierGate {
    /// Wait with all the request parties at the named point.
    pub async fn wait(self) {
        self.barrier.wait().await;
    }
}

/// The decorator over the REAL SQLite plugin: counts classified
/// statements, returns `Backend("injected")` for faulted kinds, and pauses
/// at installed gates at the named points. Everything else delegates —
/// including migrations and the identity (display) port's statements.
pub struct FaultDbPlugin {
    inner: Arc<dyn DbPlugin>,
    control: Arc<DbControl>,
}

#[async_trait]
impl DbPlugin for FaultDbPlugin {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let kind = classify(statement.sql());
        if let Some(kind) = kind {
            // Count the statement's ARRIVAL at the plugin boundary: a
            // faulted window still evidences that the chain issued the
            // operation (the fault check below is what makes it observable).
            match kind {
                NamedOp::StrictSelect => {
                    self.control.strict_selects.fetch_add(1, Ordering::SeqCst);
                }
                NamedOp::RevisionRead => {
                    self.control.revision_reads.fetch_add(1, Ordering::SeqCst);
                }
                _ => {}
            }
            if self.control.fails(kind) {
                return Err(DbError::Backend("injected".into()));
            }
            if let Some(gate) = self.control.take_gate((kind, Phase::Pre)) {
                gate.arrive().await;
            }
        }
        let rows = self.inner.query(statement).await?;
        if let Some(NamedOp::StrictSelect) = kind {
            self.control
                .rows_returned
                .fetch_add(rows.len() as u64, Ordering::SeqCst);
            if let Some(gate) = self.control.take_gate((NamedOp::StrictSelect, Phase::Post)) {
                gate.arrive().await;
            }
        }
        Ok(rows)
    }

    async fn execute(&self, statement: DbStatement) -> DbResult<DbExecuteResult> {
        let kind = classify(statement.sql());
        if let Some(kind) = kind {
            match kind {
                NamedOp::Install => {
                    self.control.installs.fetch_add(1, Ordering::SeqCst);
                }
                NamedOp::Rotate => {
                    self.control.rotates.fetch_add(1, Ordering::SeqCst);
                }
                NamedOp::Revoke => {
                    self.control.revokes.fetch_add(1, Ordering::SeqCst);
                }
                _ => {}
            }
            if self.control.fails(kind) {
                return Err(DbError::Backend("injected".into()));
            }
            if let Some(gate) = self.control.take_gate((kind, Phase::Pre)) {
                gate.arrive().await;
            }
        }
        let result = self.inner.execute(statement).await?;
        if let Some(kind) = kind {
            match kind {
                NamedOp::Rotate => {
                    if result.affected_rows == 1 {
                        self.control.applied_rotates.fetch_add(1, Ordering::SeqCst);
                    } else {
                        self.control
                            .conflicted_rotates
                            .fetch_add(1, Ordering::SeqCst);
                    }
                }
                NamedOp::Revoke => {
                    self.control
                        .revoked_rows
                        .fetch_add(result.affected_rows, Ordering::SeqCst);
                }
                _ => {}
            }
            if let Some(gate) = self.control.take_gate((kind, Phase::Post)) {
                gate.arrive().await;
            }
        }
        Ok(result)
    }

    async fn transaction(
        &self,
        steps: Vec<DbTransactionStep>,
    ) -> DbResult<Vec<DbTransactionStepResult>> {
        self.control.transactions.fetch_add(1, Ordering::SeqCst);
        self.inner.transaction(steps).await
    }

    async fn health_check(&self) -> DbResult<DbHealth> {
        self.inner.health_check().await
    }
}

/// The shared test harness: one REAL SQLite plugin (in-memory, or
/// file-backed when suites need independent plugin instances over one
/// database), the real migration chain applied through `bcs`'s own
/// migrator, and one shared control plane.
pub struct DbHarness {
    /// The real underlying plugin. Statements issued through this handle
    /// bypass the decorator (fixture seeding / planting only).
    pub raw: Arc<dyn DbPlugin>,
    pub control: Arc<DbControl>,
}

impl DbHarness {
    /// In-memory SQLite with the REAL full migration chain applied
    /// through `bcs::migrations::run_sqlite_migrations` (the published
    /// baseline + every versioned migration, incl. 042).
    pub async fn migrated() -> Self {
        let raw: Arc<dyn DbPlugin> =
            Arc::new(LocalSqliteDbPlugin::new().expect("test sqlite db"));
        Self::with_plugin(raw).await
    }

    /// File-backed SQLite with the same real migration chain, for suites
    /// that drive independent plugin instances over one shared database.
    pub async fn migrated_shared_file(path: &std::path::Path) -> Self {
        let raw: Arc<dyn DbPlugin> = Arc::new(
            LocalSqliteDbPlugin::new_file(path).expect("shared test sqlite db"),
        );
        Self::with_plugin(raw).await
    }

    async fn with_plugin(raw: Arc<dyn DbPlugin>) -> Self {
        let harness = Self {
            raw,
            control: Arc::new(DbControl::default()),
        };
        bcs::migrations::run_sqlite_migrations(harness.wrapper().as_ref())
            .await
            .expect("apply the real sqlite migration chain");
        harness
    }

    /// A new decorator wrapper over the shared real plugin. Independent
    /// wrappers (and stores built over them) still observe the same rows
    /// and the same control plane.
    pub fn wrapper(&self) -> Arc<dyn DbPlugin> {
        Arc::new(FaultDbPlugin {
            inner: self.raw.clone(),
            control: self.control.clone(),
        })
    }

    /// The REAL bootstrap identity wiring over one decorator wrapper:
    /// `DbPluginKind::LocalSqlite` → `DbUserIdentityStore::sqlite` →
    /// both bridges (`user_identity` display port + the strict
    /// `auth_session_identity` port).
    pub fn identity_ports(&self) -> bcs::identity_wiring::IdentityPorts {
        bcs::identity_wiring::db_identity_ports(
            bcs::plugins::DbPluginKind::LocalSqlite,
            self.wrapper(),
        )
    }

    /// Reset the shared counters before a measured request window.
    pub fn reset_counts(&self) {
        self.control.reset_counts();
    }
}
