//! Runtime-only Caller inheritance context, shared by accepted dispatch and
//! trusted coordination admission. It contains no task status/result lifecycle.
//! Delivery rows own durable parent/invocation metadata and Caller classification;
//! losing this context refuses inheritance rather than restoring credentials.
//! A bounded session/turn counter reserves at most 16 unique invocations before
//! admission; uncertain/failed admissions keep their reservation. All roots share
//! the counter. Restart requires fresh Human ingress, not a fresh old-turn budget.
use std::collections::{HashMap, HashSet};
use std::fmt;

use bcs_service_api::application::caller_identity::CallerExecutionToken;
use std::sync::{Arc, Mutex};

/// The lineage facts one accepted caller root run carries for later
/// in-group derivations (spec §8.3: delegation_only, memory-only).
///
/// The Human origin handle remains the stable pseudonymous subject. The
/// `parent_execution` marker is not an authorization: it only states that an
/// exact parent execution token was registered for `run_id`/`attempt`. Owner
/// and orchestration-middle chain entries are false and never synthesize one.
///
/// All fields stay runtime-only and never enter BCS DB, message history,
/// model context, Provider responses or logs.
#[derive(Clone, PartialEq, Eq)]
pub struct RootLineage {
    /// The original real root run, shared by accepted descendants.
    pub root_run_id: String,
    /// Identity trust-domain boundary. env-scoped pseudonyms and proofs
    /// never leak across environments through a lineage lookup.
    pub environment: String,
    /// Depth of this real execution; roots are zero.
    pub delegation_depth: u32,
    /// The exact attempt whose execution token (if any) is material-ledger
    /// backed. Old attempts never authorize a new child.
    pub attempt: u32,
    /// The canonical Human turn the root came from (§15.31: the source
    /// message id; the shared invocation allowance is keyed here).
    pub message_turn_id: String,
    /// The group boundary of the root.
    pub group_id: String,
    /// The root's session scope (the derivation's session fact).
    pub session_id: String,
    /// The root's history cutoff ceiling (§15.41): the child inherits it
    /// verbatim and never re-derives the boundary from the watermark.
    pub history_cutoff_seq: u64,
    /// The window that bounds derivability — the caller identity's own
    /// deadline captured at dispatch. Lookup past it says "no lineage".
    pub expires_at_ms: i64,
    /// The root dispatch's stable pseudonymous subject (the same ref the
    /// Gateway already knows from the root ensure) — runtime-only. Debug
    /// redacts it below.
    pub delegate_subject: String,
    /// Plugin-established identity, distinct from employee routing key. Runtime-only.
    pub identity_subject: String,
    pub identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType,
    /// True only when BCS also registered the exact execution token for
    /// `run_id`/`attempt`. False is the owner/lineage-only shape.
    pub parent_execution: bool,
}

/// Exact, non-secret key for one BCS-owned parent execution token. Identity
/// material itself is never part of the key and never appears in Debug.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct ExecutionMaterialKey {
    environment: String,
    group_id: Option<String>,
    session_id: String,
    run_id: String,
    attempt: u32,
}

impl ExecutionMaterialKey {
    fn new(environment: impl Into<String>, group_id: Option<String>, session_id: impl Into<String>,
           run_id: impl Into<String>, attempt: u32) -> Self {
        Self {
            environment: environment.into(),
            group_id,
            session_id: session_id.into(),
            run_id: run_id.into(),
            attempt,
        }
    }
}

struct ExecutionMaterialRecord {
    root_run_id: String,
    message_turn_id: String,
    target_bot_id: String,
    token: Arc<CallerExecutionToken>,
}

impl fmt::Debug for ExecutionMaterialRecord {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ExecutionMaterialRecord")
            .field("root_run_id", &self.root_run_id)
            .field("message_turn_id", &self.message_turn_id)
            .field("target_bot_id", &self.target_bot_id)
            .field("token", &"<redacted>")
            .finish()
    }
}

struct TurnBudget {
    expires_at_ms: i64,
    invocations: HashSet<(String, String)>,
}

#[derive(Default)]
struct RootLineageLedgerInner {
    lineages: HashMap<String, RootLineage>,
    budgets: HashMap<(String, String), TurnBudget>,
    executions: HashMap<ExecutionMaterialKey, ExecutionMaterialRecord>,
}

/// Bounded runtime-only origin/token directory: registered at an ACCEPTED
/// protected dispatch or a trusted application boundary, read by the
/// coordination derivation gate and protected child issuance. Expired entries
/// sweep on access; the directory is capped so idle roots cannot grow it —
/// over-capacity inserts evict OTHER expired entries first, then refuse
/// rather than dropping live lineage or live execution material.
pub struct RootLineageIndex {
    capacity: usize,
    registration_gate: Mutex<()>,
    inner: Mutex<RootLineageLedgerInner>,
}

impl fmt::Debug for RootLineage {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RootLineage")
            .field("root_run_id", &self.root_run_id)
            .field("environment", &self.environment)
            .field("delegation_depth", &self.delegation_depth)
            .field("attempt", &self.attempt)
            .field("message_turn_id", &self.message_turn_id)
            .field("group_id", &self.group_id)
            .field("session_id", &self.session_id)
            .field("history_cutoff_seq", &self.history_cutoff_seq)
            .field("expires_at_ms", &self.expires_at_ms)
            .field("delegate_subject", &"<redacted>")
            .field("parent_execution", &self.parent_execution)
            .finish()
    }
}

impl RootLineageIndex {
    /// Capacity must be positive; a zero-capacity index is a misassembly.
    pub fn new(capacity: usize) -> Self {
        assert!(capacity > 0, "root lineage index capacity must be positive");
        Self {
            capacity,
            registration_gate: Mutex::new(()),
            inner: Mutex::new(RootLineageLedgerInner::default()),
        }
    }

    /// Serialize term-qualified registration with sealing. The term check is
    /// inside the same gate as the write, so a pre-seal check cannot repopulate
    /// a sealed ledger. The closure must only perform synchronous registration.
    pub fn register_in_term<T>(&self, term: Option<&std::sync::atomic::AtomicU64>,
        captured: Option<u64>, register: impl FnOnce() -> T) -> Option<T> {
        let _guard = self.registration_gate.lock().expect("caller registration gate");
        if term.map(|v| v.load(std::sync::atomic::Ordering::SeqCst)) != captured {
            return None;
        }
        Some(register())
    }

    fn valid_lineage(run_id: &str, lineage: &RootLineage, now_ms: i64) -> bool {
        !run_id.trim().is_empty()
            && !lineage.root_run_id.trim().is_empty()
            && !lineage.environment.trim().is_empty()
            && lineage.attempt > 0
            && !lineage.message_turn_id.trim().is_empty()
            && !lineage.group_id.trim().is_empty()
            && !lineage.session_id.trim().is_empty()
            && !lineage.delegate_subject.trim().is_empty()
            && lineage.expires_at_ms > now_ms
    }

    /// Register (or atomically replace) the lineage of one accepted
    /// caller-execution / owner-lineage dispatch. Caller executions must use
    /// [`Self::register_parent_execution`] so the trusted proof cannot be
    /// advertised without being present in the material ledger.
    ///
    /// Refusals (capacity) return false — the dispatch itself already
    /// succeeded; child derivability for THIS root just pauses until its
    /// next accepted dispatch.
    pub fn register(&self, run_id: &str, lineage: RootLineage, now_ms: i64) -> bool {
        if !Self::valid_lineage(run_id, &lineage, now_ms) || lineage.parent_execution {
            return false;
        }
        let mut inner = self.inner.lock().expect("caller identity material ledger");
        inner.lineages.retain(|_, entry| entry.expires_at_ms > now_ms);
        inner.executions.retain(|_, record| record.token.expires_at_ms() > now_ms);
        inner.budgets.retain(|_, budget| budget.expires_at_ms > now_ms);
        if let Some(current) = inner.lineages.get(run_id) {
            if lineage.attempt < current.attempt
                || lineage.environment != current.environment
                || lineage.group_id != current.group_id
                || lineage.session_id != current.session_id
                || lineage.root_run_id != current.root_run_id
                || lineage.message_turn_id != current.message_turn_id
                || lineage.delegate_subject != current.delegate_subject
                || lineage.identity_subject != current.identity_subject
                || lineage.identity_subject_type != current.identity_subject_type {
                return false;
            }
        }
        if !inner.lineages.contains_key(run_id) && inner.lineages.len() >= self.capacity {
            return false;
        }
        if !Self::upsert_budget(&mut inner, self.capacity, &lineage) {
            return false;
        }
        inner.executions.retain(|key, _| key.run_id != run_id);
        inner.lineages.insert(run_id.to_string(), lineage);
        true
    }

    /// Atomically register a caller execution lineage and the exact proof
    /// BCS will later exchange for a next-hop target token. A capacity or
    /// input refusal records neither the lineage nor the token marker, so a
    /// child can never infer "caller parent" without retrievable material.
    pub fn register_parent_execution(
        &self,
        run_id: &str,
        lineage: RootLineage,
        target_bot_id: &str,
        token: CallerExecutionToken,
        now_ms: i64,
    ) -> bool {
        let lineage = lineage;
        // run-budget facts only: the lineage lifetime never shrinks to a
        // token advisory, and token age is never a registration refusal.
        if !Self::valid_lineage(run_id, &lineage, now_ms)
            || target_bot_id.trim().is_empty()
            || !lineage.parent_execution
        {
            return false;
        }
        let key = ExecutionMaterialKey::new(
            lineage.environment.clone(),
            Some(lineage.group_id.clone()),
            lineage.session_id.clone(),
            run_id,
            lineage.attempt,
        );
        let record = ExecutionMaterialRecord {
            root_run_id: lineage.root_run_id.clone(),
            message_turn_id: lineage.message_turn_id.clone(),
            target_bot_id: target_bot_id.to_string(),
            token: Arc::new(token),
        };
        let mut inner = self.inner.lock().expect("caller identity material ledger");
        inner.lineages.retain(|_, entry| entry.expires_at_ms > now_ms);
        inner.executions.retain(|_, record| record.token.expires_at_ms() > now_ms);
        inner.budgets.retain(|_, budget| budget.expires_at_ms > now_ms);
        if let Some(current) = inner.lineages.get(run_id) {
            if lineage.attempt < current.attempt
                || lineage.environment != current.environment
                || lineage.group_id != current.group_id
                || lineage.session_id != current.session_id
                || lineage.root_run_id != current.root_run_id
                || lineage.message_turn_id != current.message_turn_id
                || lineage.delegate_subject != current.delegate_subject
                || lineage.identity_subject != current.identity_subject
                || lineage.identity_subject_type != current.identity_subject_type {
                return false;
            }
        }
        if !inner.lineages.contains_key(run_id) && inner.lineages.len() >= self.capacity {
            return false;
        }
        if !Self::upsert_budget(&mut inner, self.capacity, &lineage) {
            return false;
        }
        // At most one proof per live run; newer attempts retire old material atomically.
        if let Some(existing) = inner.executions.get(&key) {
            if existing.token.expose_token() != record.token.expose_token()
                || existing.target_bot_id != record.target_bot_id {
                return false;
            }
        }
        inner.executions.retain(|key, _| key.run_id != run_id);
        inner.executions.insert(key, record);
        inner.lineages.insert(run_id.to_string(), lineage);
        true
    }

    fn upsert_budget(
        inner: &mut RootLineageLedgerInner,
        capacity: usize,
        lineage: &RootLineage,
    ) -> bool {
        let key = (lineage.session_id.clone(), lineage.message_turn_id.clone());
        if !inner.budgets.contains_key(&key) && inner.budgets.len() >= capacity {
            return false;
        }
        let expiry = lineage.expires_at_ms;
        let budget = inner.budgets.entry(key).or_insert_with(|| TurnBudget {
            expires_at_ms: expiry,
            invocations: HashSet::new(),
        });
        budget.expires_at_ms = budget.expires_at_ms.max(expiry);
        true
    }

    /// Live lineage lookup for the coordination derivation gate. Expired
    /// or absent entries read as None — derivability never outlives the
    /// caller window and nothing is ever reconstructed from DB state.
    pub fn current(&self, run_id: &str, now_ms: i64) -> Option<RootLineage> {
        let mut inner = self.inner.lock().expect("caller identity material ledger");
        inner.lineages.retain(|_, entry| entry.expires_at_ms > now_ms);
        inner.executions.retain(|_, record| record.token.expires_at_ms() > now_ms);
        inner.budgets.retain(|_, budget| budget.expires_at_ms > now_ms);
        inner.lineages.get(run_id).cloned()
    }

    /// Exact parent-token lookup for a caller child. Every input is a trusted
    /// inner fact assembled from the live lineage and managed row; Provider
    /// messages cannot contribute them. Missing, foreign, stale or expired
    /// entries always read as None and never fall back to an owner handle.
    pub fn current_parent_execution(
        &self,
        environment: &str,
        group_id: Option<&str>,
        session_id: &str,
        root_run_id: &str,
        message_turn_id: &str,
        target_bot_id: &str,
        parent_run_id: &str,
        parent_attempt: u32,
        now_ms: i64,
    ) -> Option<Arc<CallerExecutionToken>> {
        let mut inner = self.inner.lock().expect("caller identity material ledger");
        inner.lineages.retain(|_, entry| entry.expires_at_ms > now_ms);
        inner.executions.retain(|_, record| record.token.expires_at_ms() > now_ms);
        lineages_budget_sweep(&mut inner, now_ms);
        let lineage = inner.lineages.get(parent_run_id)?;
        if !lineage.parent_execution
            || lineage.attempt != parent_attempt
            || lineage.environment != environment
            || lineage.group_id != group_id.unwrap_or_default()
            || lineage.session_id != session_id
            || lineage.root_run_id != root_run_id
            || lineage.message_turn_id != message_turn_id
        {
            return None;
        }
        let key = ExecutionMaterialKey::new(
            environment,
            group_id.map(str::to_string),
            session_id,
            parent_run_id,
            parent_attempt,
        );
        let record = inner.executions.get(&key)?;
        if record.root_run_id != root_run_id
            || record.message_turn_id != message_turn_id
            || record.target_bot_id != target_bot_id
        {
            return None;
        }
        let token = record.token.clone();
        Some(token)
    }

    /// Reserve before admission, under one lock, shared across all roots of a
    /// session/turn. No state transitions, result records or child run IDs.
    /// Replays fail closed here; the existing coordination claim owns dedup.
    /// Errors/unknown admissions retain the allowance, never refund it.
    pub fn reserve_invocation(
        &self,
        parent_run: &str,
        invocation: &str,
        target_bot: &str,
        now_ms: i64,
    ) -> bcs_service_api::ServiceResult<()> {
        use bcs_service_api::ServiceError;
        let refused = |message: &str| ServiceError::InvalidOperation {
            message: message.into(),
            request_id: None,
        };
        if invocation.trim().is_empty() || target_bot.trim().is_empty() {
            return Err(refused("trusted invocation and target required"));
        }

        let mut inner = self.inner.lock().expect("caller identity material ledger");
        let lineage = inner
            .lineages
            .get(parent_run)
            .filter(|value| value.expires_at_ms > now_ms)
            .ok_or_else(|| refused("caller lineage unavailable"))?;
        if lineage.delegation_depth >= 2 {
            return Err(refused("caller delegation depth exceeded"));
        }
        let key = (lineage.session_id.clone(), lineage.message_turn_id.clone());
        let budget = inner
            .budgets
            .get_mut(&key)
            .filter(|value| value.expires_at_ms > now_ms)
            .ok_or_else(|| refused("caller turn context lost"))?;
        let invocation_key = (parent_run.to_owned(), invocation.to_owned());
        if budget.invocations.contains(&invocation_key) {
            return Err(ServiceError::Conflict(
                "caller invocation already reserved".into(),
            ));
        }
        if budget.invocations.len() >= 16 {
            return Err(ServiceError::MessageLimitReached(
                "caller turn invocation budget exhausted".into(),
            ));
        }
        budget.invocations.insert(invocation_key);
        Ok(())
    }

    /// Terminal release (audit P1 2026-10-25): remove EVERYTHING this root
    /// registered — the instance lineage (key == the root run id), every
    /// node lineage that roots back to it (`root_run_id`), the execution
    /// material its subtree parked, and the turn-budget invocations its
    /// nodes reserved (dropping a TurnBudget entirely once its last
    /// invocation is gone, so other live roots of the same session/turn
    /// stay untouched). Idempotent: a repeated or late terminal event finds
    /// nothing and returns false. Must be called at every run-terminal
    /// convergence (complete/fail/abort); the finite instance budget
    /// (`expires_at_ms`) is the safety net when a terminal path misses.
    pub fn release_root_run(&self, root_run_id: &str) -> bool {
        if root_run_id.trim().is_empty() {
            return false;
        }
        let mut inner = self.inner.lock().expect("caller identity material ledger");
        let before = inner.lineages.len() + inner.executions.len() + inner.budgets.len();
        // Lineages: the instance entry (key == root) plus every node entry
        // that roots back to it.
        let removed_runs: Vec<String> = inner
            .lineages
            .iter()
            .filter(|(key, entry)| {
                key.as_str() == root_run_id || entry.root_run_id == root_run_id
            })
            .map(|(key, _)| key.clone())
            .collect();
        inner
            .lineages
            .retain(|key, _| removed_runs.iter().all(|run| run != key));
        // Execution material: records always carry the root they belong to.
        inner
            .executions
            .retain(|_, record| record.root_run_id != root_run_id);
        // Turn budgets: release the invocations this subtree reserved; drop
        // TurnBudgets that thereby became empty (their expiry would reap
        // them anyway, but releasing now keeps the share with other roots
        // of the same session/turn bounded to live work).
        for budget in inner.budgets.values_mut() {
            budget
                .invocations
                .retain(|(parent_run, _)| removed_runs.iter().all(|run| run != parent_run));
        }
        inner.budgets.retain(|_, budget| !budget.invocations.is_empty());
        let after = inner.lineages.len() + inner.executions.len() + inner.budgets.len();
        after != before
    }

    /// Bounded directory size (diagnostics/tests).
    pub fn len(&self) -> usize {
        self.inner.lock().expect("caller identity material ledger").lineages.len()
    }

    /// Seal the current term and start the next one clean (spec §7.5.2):
    /// the caller master's epoch boundary wipes the whole runtime-only
    /// registry — lineage, turn budgets AND execution tokens admitted under
    /// an OLD term never derive in the new term.
    pub fn seal_term(&self) -> usize {
        let _guard = self.registration_gate.lock().expect("caller registration gate");
        let mut inner = self.inner.lock().expect("caller identity material ledger");
        let wiped = inner.lineages.len();
        inner.lineages.clear();
        inner.budgets.clear();
        inner.executions.clear();
        wiped
    }
}

fn lineages_budget_sweep(inner: &mut RootLineageLedgerInner, now_ms: i64) {
    inner.budgets.retain(|_, budget| budget.expires_at_ms > now_ms);
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: i64 = 1_780_000_000_000;

    fn lineage(run: &str, expires: i64) -> RootLineage {
        RootLineage {
            root_run_id: run.to_string(),
            environment: "env-1".to_string(),
            delegation_depth: 0,
            attempt: 1,
            message_turn_id: "turn-1".to_string(),
            group_id: "group-1".to_string(),
            session_id: "session-1".to_string(),
            history_cutoff_seq: 41,
            expires_at_ms: expires,
            identity_subject: "root-pseudonym".to_string(),
            identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "root-pseudonym".to_string(),
            parent_execution: false,
        }
    }

    #[test]
    fn sealed_term_cannot_be_repopulated_by_a_delayed_registration() {
        use std::sync::atomic::{AtomicU64, Ordering};
        let index = RootLineageIndex::new(8);
        let term = AtomicU64::new(1);
        let captured = Some(1);
        assert_eq!(index.register_in_term(Some(&term), captured, ||
            index.register("old", lineage("old", NOW + 10000), NOW)), Some(true));
        term.fetch_add(1, Ordering::SeqCst);
        index.seal_term();
        assert_eq!(index.register_in_term(Some(&term), captured, ||
            index.register("late", lineage("late", NOW + 10000), NOW)), None);
        assert!(index.current("late", NOW).is_none());
        assert_eq!(index.register_in_term(Some(&term), Some(2), ||
            index.register("new", lineage("new", NOW + 10000), NOW)), Some(true));
    }

    #[test]
    fn registers_replaces_and_expires_the_lineage_window() {
        let index = RootLineageIndex::new(4);
        assert!(index.register("run-1", lineage("run-1", NOW + 10_000), NOW));
        // The same run's fresh accepted dispatch refreshes the window.
        assert!(index.register("run-1", lineage("run-1", NOW + 20_000), NOW));
        assert_eq!(
            index.current("run-1", NOW).unwrap().expires_at_ms,
            NOW + 20_000
        );
        // Past the window the lineage is gone — never reconstructed.
        assert!(index.current("run-1", NOW + 20_001).is_none());
    }

    #[test]
    fn expired_or_empty_material_never_enters() {
        let index = RootLineageIndex::new(8);
        assert!(
            !index.register("run-1", lineage("run-1", NOW), NOW),
            "already dead"
        );
        assert!(
            !index.register("run-2", lineage("run-2", NOW - 1), NOW),
            "inverted window"
        );
        assert!(!index.register("", lineage("x", NOW + 10_000), NOW));
        assert!(!index.register("run-3", lineage("", NOW + 10_000), NOW));
        assert_eq!(index.len(), 0, "nothing entered");
    }

    #[test]
    fn terminal_release_scope_is_idempotent_and_frees_capacity() {
        // Audit P1 2026-10-25: run-terminal releases must scope to the
        // released root (instance + its node lineages + its turn-budget
        // invocations), never touch other roots, and be idempotent so
        // repeated/late terminal events are free no-ops.
        let index = RootLineageIndex::new(2);
        assert!(index.register("root-a", lineage("root-a", NOW + 10_000), NOW));
        assert!(index.register("root-b", lineage("root-b", NOW + 10_000), NOW));
        assert!(
            index.reserve_invocation("root-a", "node:n1:1", "worker-1", NOW).is_ok(),
            "root-a reserves on its turn budget"
        );
        // Capacity is full: a third registration is refused.
        assert!(!index.register("root-c", lineage("root-c", NOW + 10_000), NOW));

        // Terminal release for root-b: root-a keeps everything it owns.
        assert!(index.release_root_run("root-b"));
        assert!(index.current("root-b", NOW).is_none());
        assert!(index.current("root-a", NOW).is_some());
        assert_eq!(index.len(), 1);
        // The freed slot admits new work.
        assert!(index.register("root-c", lineage("root-c", NOW + 10_000), NOW));

        // Idempotent: a repeated (or late) terminal event finds nothing.
        assert!(!index.release_root_run("root-b"));
        // Empty ids never touch the ledger.
        assert!(!index.release_root_run(" "));

        // root-a's own terminal still works after all that.
        assert!(index.reserve_invocation("root-a", "node:n2:1", "worker-2", NOW).is_ok());
        assert!(index.release_root_run("root-a"));
        assert!(index.reserve_invocation("root-a", "node:n3:1", "worker-3", NOW).is_err());
    }

    #[test]
    fn terminal_release_removes_node_lineages_and_parent_material_of_the_root() {
        let index = RootLineageIndex::new(8);
        assert!(index.register("wf-1", lineage("wf-1", NOW + 10_000), NOW));
        let mut reserve = lineage("wf-1", NOW + 10_000);
        let mut delegated = reserve.clone();
        delegated.root_run_id = "wf-1".to_string();
        delegated.attempt = 1;
        delegated.parent_execution = false;
        // A node lineage rooted back at wf-1, registered under its own key.
        assert!(index.register("wf-1-node-1", delegated, NOW));
        reserve.parent_execution = false;
        // An unrelated root stays untouched.
        assert!(index.register("wf-2", lineage("wf-2", NOW + 10_000), NOW));
        assert!(index.reserve_invocation("wf-2", "inv", "bot", NOW).is_ok());

        assert!(index.release_root_run("wf-1"));
        assert!(index.current("wf-1", NOW).is_none());
        assert!(index.current("wf-1-node-1", NOW).is_none(), "node lineage roots back and dies with the root");
        assert!(index.current("wf-2", NOW).is_some());
        assert!(index.reserve_invocation("wf-2", "inv2", "bot", NOW).is_ok());
    }

    #[test]
    fn capacity_refuses_instead_of_dropping_live_lineage() {
        let index = RootLineageIndex::new(2);
        assert!(index.register("a", lineage("a", NOW + 10_000), NOW));
        assert!(index.register("b", lineage("b", NOW + 10_000), NOW));
        // All live: refuse the third instead of evicting a live root.
        assert!(!index.register("c", lineage("c", NOW + 10_000), NOW));
        assert_eq!(index.len(), 2);
        // After both expire, the sweep reclaims and the register passes.
        assert!(index.register("c", lineage("c", NOW + 30_000), NOW + 20_000));
        assert_eq!(index.current("a", NOW + 20_000), None);
    }

    #[test]
    fn parent_execution_material_is_exchange_only_for_exact_lineage_facts() {
        use bcs_service_api::application::caller_identity::{
            CallerExecutionToken, CallerProofFormat,
        };

        let index = RootLineageIndex::new(8);
        let mut execution_lineage = lineage("run-1", NOW + 10_000);
        execution_lineage.parent_execution = true;
        execution_lineage.attempt = 1;
        let token = CallerExecutionToken::new(
            CallerProofFormat::Jwt,
            "runtime-only-parent-proof".to_string(),
            NOW + 10_000,
        )
        .unwrap();
        assert!(
            index.register_parent_execution("run-1", execution_lineage, "bot-a", token, NOW)
        );

        let exact = index
            .current_parent_execution(
                "env-1",
                Some("group-1"),
                "session-1",
                "run-1",
                "turn-1",
                "bot-a",
                "run-1",
                1,
                NOW,
            )
            .expect("the exact trusted parent facts select the BCS-owned token");
        assert_eq!(exact.expose_token(), "runtime-only-parent-proof");

        for lookup in [
            ("env-2", Some("group-1"), "session-1", "run-1", "turn-1", "bot-a", "run-1", 1),
            ("env-1", Some("group-2"), "session-1", "run-1", "turn-1", "bot-a", "run-1", 1),
            ("env-1", Some("group-1"), "session-2", "run-1", "turn-1", "bot-a", "run-1", 1),
            ("env-1", Some("group-1"), "session-1", "other-root", "turn-1", "bot-a", "run-1", 1),
            ("env-1", Some("group-1"), "session-1", "run-1", "other-turn", "bot-a", "run-1", 1),
            ("env-1", Some("group-1"), "session-1", "run-1", "turn-1", "bot-b", "run-1", 1),
            ("env-1", Some("group-1"), "session-1", "run-1", "turn-1", "bot-a", "run-2", 1),
            ("env-1", Some("group-1"), "session-1", "run-1", "turn-1", "bot-a", "run-1", 2),
        ] {
            assert!(
                index.current_parent_execution(
                    lookup.0, lookup.1, lookup.2, lookup.3, lookup.4,
                    lookup.5, lookup.6, lookup.7, NOW,
                ).is_none(),
                "foreign, stale or mismatched parent facts never reach identity material"
            );
        }

        assert!(
            index
                .current_parent_execution(
                    "env-1", Some("group-1"), "session-1", "run-1", "turn-1",
                    "bot-a", "run-1", 1, NOW + 10_001,
                )
                .is_none(),
            "expired parent material refuses instead of rebuilding"
        );
    }

    #[test]
    fn seal_term_wipes_lineage_and_turn_budgets_for_the_new_master_epoch() {
        // Spec §7.5.2: lineage admitted under an OLD term never derives in
        // a new term — even a live window reads as absent (the root's own
        // retry re-registers through a fresh accepted dispatch), and the
        // turn budgets die with the directory so invocations never carry
        // across terms.
        let index = RootLineageIndex::new(4);
        assert!(index.register("run-1", lineage("run-1", NOW + 10_000), NOW));
        assert!(
            index.reserve_invocation("run-1", "invocation-1", "bot-a", NOW).is_ok(),
            "the turn budget is consumed under the alive window"
        );

        let wiped = index.seal_term();
        assert_eq!(wiped, 1, "the live lineage entry count");
        assert!(index.current("run-1", NOW).is_none(), "old-term lineage never derives");
        assert!(
            index.reserve_invocation("run-1", "invocation-2", "bot-a", NOW).is_err(),
            "the old term's resident root cannot derive even by id"
        );
        // The new term re-registers cleanly: fresh window, fresh budget.
        assert!(index.register("run-1", lineage("run-1", NOW + 10_000), NOW));
        assert!(
            index.reserve_invocation("run-1", "invocation-1", "bot-a", NOW).is_ok(),
            "the invocation allowance was wiped with the term — same id re-admits"
        );
    }
}
#[cfg(test)]
mod budget_tests {
    use super::*;

    fn root(session: &str, turn: &str, depth: u32) -> RootLineage {
        RootLineage {
            root_run_id: "root".into(),
            environment: "env-budget".into(),
            delegation_depth: depth,
            attempt: 1,
            message_turn_id: turn.into(),
            group_id: "group".into(),
            session_id: session.into(),
            history_cutoff_seq: 0,
            expires_at_ms: 1000,
            identity_subject: "subject".into(),
            identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "subject".into(),
            parent_execution: false,
        }
    }

    #[test]
    fn all_roots_share_turn_budget_but_sessions_and_turns_are_isolated() {
        let index = RootLineageIndex::new(8);
        assert!(index.register("a", root("session", "turn", 0), 1));
        assert!(index.register("b", root("session", "turn", 0), 1));
        for i in 0..16 {
            index
                .reserve_invocation(
                    if i % 2 == 0 { "a" } else { "b" },
                    &i.to_string(),
                    "worker",
                    2,
                )
                .unwrap();
        }
        assert!(
            index
                .reserve_invocation("a", "overflow", "worker", 2)
                .is_err()
        );
        // A re-register/retry never resets the turn counter.
        assert!(index.register("a", root("session", "turn", 0), 2));
        assert!(
            index
                .reserve_invocation("a", "overflow", "worker", 2)
                .is_err()
        );
        assert!(index.register("c", root("other-session", "turn", 0), 2));
        assert!(index.register("d", root("session", "other-turn", 0), 2));
        index.reserve_invocation("c", "fresh", "worker", 2).unwrap();
        index.reserve_invocation("d", "fresh", "worker", 2).unwrap();
    }

    #[test]
    fn replay_target_change_and_lost_or_too_deep_context_refuse() {
        let index = RootLineageIndex::new(8);
        assert!(index.register("a", root("session", "turn", 0), 1));
        index
            .reserve_invocation("a", "invocation", "worker", 2)
            .unwrap();
        assert!(
            index
                .reserve_invocation("a", "invocation", "other-worker", 2)
                .is_err()
        );
        assert!(index.reserve_invocation("a", "", "worker", 2).is_err());
        assert!(
            index
                .reserve_invocation("a", "next", "worker", 1000)
                .is_err()
        );
        assert!(
            RootLineageIndex::new(8)
                .reserve_invocation("a", "next", "worker", 2)
                .is_err()
        );
        assert!(index.register("deep", root("session", "turn", 2), 1));
        assert!(
            index
                .reserve_invocation("deep", "next", "worker", 2)
                .is_err()
        );
    }

    #[test]
    fn concurrent_reservations_cannot_exceed_the_shared_ceiling() {
        let index = Arc::new(RootLineageIndex::new(8));
        assert!(index.register("a", root("session", "turn", 0), 1));
        let handles: Vec<_> = (0..64)
            .map(|n| {
                let index = index.clone();
                std::thread::spawn(move || {
                    index
                        .reserve_invocation("a", &n.to_string(), "worker", 2)
                        .is_ok()
                })
            })
            .collect();
        assert_eq!(
            handles
                .into_iter()
                .filter_map(|h| h.join().ok())
                .filter(|ok| *ok)
                .count(),
            16
        );
    }
}
