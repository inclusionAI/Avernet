# BCS Direct Chat Run Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Govern two process-local run stores (`ChatRunStore`, `MemoryBotRunContextStore`) so they survive BCS restart and stay consistent across replicas, behind a config switch that defaults to the old in-memory behavior.

**Architecture:** Two independent governed stores keyed by a shared `run_id`. `ChatRunStore` → `ChatRunRepoPort` with a MySQL-authoritative + Redis-hot-cache SQL impl and a behavior-equivalent in-memory impl; the `ChatRunStore` struct becomes a state-machine engine over the port (terminal-guard/transition rules live here once). `BotRunContextPort` keeps its trait and gains a Redis impl. `A2aChat` aligns terminal lifecycle across both. SSE live reader stays node-local; a TTL + timeout sweep covers reader death. A config switch (`run_store`, `run_context_store`) selects impl per environment, default `memory`.

**Tech Stack:** Rust workspace; `bcs-service-api` (port traits), `bcs-db-api`/`bcs-cache-api` (DbPlugin/CachePlugin), `bcs-db-local`(SQLite)/`bcs-db-mysql`/`bcs-cache-redis`/`bcs-cache-local`, `async-trait`, `serde`, `thiserror`, `tokio`. SQLite + MySQL migration runner in `bootstrap/bcs/src/migrations.rs`.

**Spec:** `docs/superpowers/specs/2026-08-27-bcs-run-governance-design.md`

## Global Constraints

- **No `cargo fmt`.** Keep whitespace edits to lines that must change (project rule, `crates/.../CLAUDE.md`).
- **UTF-8 truncation:** Never slice by byte index. Use `is_char_boundary`/`char_indices` (see `ChatRunStore::append_delta`). Preserve existing 1 MiB cap (`MAX_CONTENT_BYTES`).
- **Write-failure propagation:** `ChatRunRepoError::Backend` / `DbError::Backend` must surface as errors, never become a 202/success (issue #1546 hard constraint).
- **Node-local only:** `Notify`, `ChatRunEventPort`/`ChatRunCleanupPort` + `RunChannelManager` (mpsc/alias/session/trace), and the SSE reader task must NOT be persisted.
- **Crate layering (CLAUDE.md):** repo traits in `bcs_service_api::port::repo`; store crates implement them; `application`/`core` may hold `Arc<dyn *Repo>`. Delivery adapters call only application services.
- **Memory-mode default:** default config selects `memory` (old behavior). `memory` = new engine + `MemoryChatRunRepo`/`MemoryBotRunContextStore`, behavior-equivalent by contract tests.
- **DB/cache reuse:** use existing `[database]` + `[cache.redis]`; no top-level `[redis]` (rejected by config validation). Redis keys: `{prefix}chat_run:{run_id}` (hot cache), `{prefix}botrun:{run_id}` etc.
- **Commit cadence:** one logical task = one commit. Conventional Commits messages; end commit messages with `Co-Authored-By: Claude <noreply@anthropic.com>`.
- **Existing tests must stay green** at every task boundary: `cargo test -p bcs-message-flow` (contract_a2a_chat / conformance) and `cargo test -p bcs-service-api`.

---

## File Structure

**New files:**
- `crates/service-api/bcs-service-api/src/port/repo/chat_run.rs` — `ChatRunRecord`, `ChatRunState`, `ChatRunCompletionPolicy`, `CasOutcome`, `ChatRunRepoError`, `ChatRunRepoPort`, `MAX_CONTENT_BYTES`. *(Partially scaffolded already during plan prep — reconcile against Task 1.)*
- `crates/services/bcs-chat-run-store/Cargo.toml` — new store crate.
- `crates/services/bcs-chat-run-store/src/lib.rs` — `SqlChatRunRepo` (MySQL + Redis hot cache), type aliases, crate docs.
- `crates/services/bcs-chat-run-store/src/memory.rs` — `MemoryChatRunRepo`.
- `crates/services/bcs-chat-run-store/src/sql.rs` — SQL text, row↔record mapping, schema upsert.
- `crates/services/bcs-chat-run-store/tests/memory_repo.rs` — memory repo unit tests.
- `crates/services/bcs-chat-run-store/tests/sql_repo.rs` — SQLite-backed SqlChatRunRepo tests.
- `migrations/mysql/011_chat_runs.sql` — MySQL DDL for `bcs_chat_runs`.
- `crates/services/bcs-message-flow/src/run_context_redis.rs` — `RedisBotRunContextStore` impl of `BotRunContextPort` (Redis).
- `crates/services/bcs-message-flow/tests/run_context_redis.rs` — Redis-bot-run tests against `bcs-cache-redis` (or local fake).
- `crates/bootstrap/bcs/tests/run_governance_restart.rs` — restart/replica/idempotency/audit integration tests.

**Modified files:**
- `crates/service-api/bcs-service-api/src/port/repo/mod.rs` (+`pub mod chat_run;` + re-exports) *(done in prep)*
- `crates/service-api/bcs-service-api/src/lib.rs` (+crate re-exports) *(done in prep)*
- `crates/services/bcs-message-flow/src/a2a_chat/run_store.rs` — replace type defs with re-exports; refactor `ChatRunStore` to engine-over-port.
- `crates/services/bcs-message-flow/src/a2a_chat/mod.rs` — `A2aChat` construction over repo; terminal co-op with BotRun; write-failure propagation.
- `crates/services/bcs-message-flow/src/run_context.rs` — keep `MemoryBotRunContextStore`; extract shared enum/structs if needed for Redis impl.
- `crates/services/bcs-message-flow/src/lib.rs` — `pub mod run_context_redis;` + re-export.
- `crates/services/bcs-message-flow/Cargo.toml` — add `bcs-chat-run-store` dep (not needed if engine lives in message-flow; see Task 3 decision).
- `Cargo.toml` (workspace) — register `bcs-chat-run-store` member + dep.
- `crates/bootstrap/bcs/src/migrations.rs` — add `bcs_chat_runs` to `SQLITE_DDL_STATEMENTS`.
- `crates/bootstrap/bcs/src/config.rs` — add `async_chat_run_store`, `bot_run_context_store` fields + defaults.
- `crates/bootstrap/bcs/src/server.rs` — wire repos at the production site (`new_with_infrastructure`) + in-memory sites + config selection.
- `crates/bootstrap/bcs/Cargo.toml` — add `bcs-chat-run-store`.

---

## Task 1: Port + moved types (`ChatRunRepoPort`)

**Files:**
- Create: `crates/service-api/bcs-service-api/src/port/repo/chat_run.rs`
- Modify: `crates/service-api/bcs-service-api/src/port/repo/mod.rs`, `crates/service-api/bcs-service-api/src/lib.rs`

**Interfaces:**
- Produces: `bcs_service_api::port::repo::chat_run::{ChatRunRecord, ChatRunState, ChatRunCompletionPolicy, CasOutcome, ChatRunRepoError, ChatRunRepoPort, MAX_CONTENT_BYTES}` and crate-root re-exports `bcs_service_api::{ChatRunRecord, ChatRunState, ChatRunCompletionPolicy, CasOutcome, ChatRunRepoError, ChatRunRepoPort}`. Also `bcs_service_api::CHAT_RUN_MAX_CONTENT_BYTES` (= `MAX_CONTENT_BYTES`).

> Note: a first version of `chat_run.rs`, the `mod.rs` registration, and the `lib.rs` re-exports were written during plan prep. This task RECONCILES them to the exact API below and verifies the build. Do not skip the verify step.

**Exact port API (must match later tasks):**

```rust
pub const MAX_CONTENT_BYTES: usize = 1_024 * 1_024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ChatRunState { Pending, Submitted, Running, Completed, Failed, Cancelled }
impl ChatRunState { pub fn as_str(self) -> &'static str { ... }  pub fn is_terminal(self) -> bool { ... } }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ChatRunCompletionPolicy { WaitForFinal, DetachDeliveryAck }

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChatRunRecord { /* all current fields; completion_policy + delivery_ack_at_ms use #[serde(skip_serializing, default)] */ }
impl ChatRunRecord { pub fn new(...) -> Self { ... } }

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CasOutcome { Applied(ChatRunRecord), Conflict(Option<ChatRunRecord>), Terminal(Option<ChatRunRecord>) }

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ChatRunRepoError {
    #[error("chat run {0} already exists")] DuplicateRunId(String),
    #[error("chat run {0} not found")] NotFound(String),
    #[error("chat run {0} compare-and-set conflict")] Conflict(String),
    #[error("chat run store backend error: {0}")] Backend(String),
}

#[async_trait]
pub trait ChatRunRepoPort: Send + Sync + 'static {
    async fn create(&self, record: ChatRunRecord) -> Result<(), ChatRunRepoError>;
    async fn get(&self, run_id: &str) -> Result<Option<ChatRunRecord>, ChatRunRepoError>;
    async fn compare_and_set_state(&self, run_id: &str, expected_version: u64, new: ChatRunRecord) -> Result<CasOutcome, ChatRunRepoError>;
    async fn compare_and_set_terminal(&self, run_id: &str, expected_version: u64, new: ChatRunRecord) -> Result<CasOutcome, ChatRunRepoError>;
    async fn append_streaming_content(&self, run_id: &str, expected_version: u64, accumulated: String, truncated: bool) -> Result<bool, ChatRunRepoError>;
    async fn list_active(&self, now_ms: u64) -> Result<Vec<ChatRunRecord>, ChatRunRepoError>;
    async fn delete_expired_terminal(&self, now_ms: u64, retention_ms: u64) -> Result<usize, ChatRunRepoError>;
    async fn metric_counts(&self) -> Result<Vec<ChatRunMetricCount>, ChatRunRepoError>;
}
```

`chat_run.rs` uses `use crate::{ChatResponseMode, ChatRunMetricCount};` (both already crate-re-exported). `ChatRunRecord::new(...)` signature MUST stay byte-identical to the current one in `run_store.rs` (same field order/types) so existing callers compile.

- [ ] **Step 1: Reconcile `chat_run.rs`** to the API above (add `Deserialize`, `default` serde attrs, `thiserror::Error` derive; ensure `ChatRunRecord::new` matches current signature exactly). Verify the `completion_policy` serde default fn returns `WaitForFinal`.

- [ ] **Step 2: Register module + re-exports**
  - `port/repo/mod.rs`: `pub mod chat_run;` and `pub use chat_run::{CasOutcome, ChatRunCompletionPolicy, ChatRunRecord, ChatRunRepoError, ChatRunRepoPort, ChatRunState, MAX_CONTENT_BYTES};`
  - `lib.rs`: `pub use port::repo::chat_run::{CasOutcome, ChatRunCompletionPolicy, ChatRunRecord, ChatRunRepoError, ChatRunRepoPort, ChatRunState, MAX_CONTENT_BYTES as CHAT_RUN_MAX_CONTENT_BYTES};`

- [ ] **Step 3: Build the crate**

Run: `cargo build -p bcs-service-api`
Expected: PASS (new module compiles; existing code unaffected).

- [ ] **Step 4: Commit**

```bash
git add crates/service-api/bcs-service-api/src/port/repo/chat_run.rs crates/service-api/bcs-service-api/src/port/repo/mod.rs crates/service-api/bcs-service-api/src/lib.rs
git commit -m "feat(bcs): add ChatRunRepoPort and move run record types to service-api

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: `MemoryChatRunRepo` (behavior-equivalent in-process impl)

**Files:**
- Create: `crates/services/bcs-chat-run-store/Cargo.toml`, `crates/services/bcs-chat-run-store/src/lib.rs`, `crates/services/bcs-chat-run-store/src/memory.rs`
- Create test: `crates/services/bcs-chat-run-store/tests/memory_repo.rs`
- Modify: workspace `Cargo.toml` (add member `crates/services/bcs-chat-run-store` and workspace dep `bcs-chat-run-store = { path = "crates/services/bcs-chat-run-store" }`)

**Interfaces:**
- Consumes: `bcs_service_api::port::repo::ChatRunRepoPort` (Task 1).
- Produces: `bcs_chat_run_store::MemoryChatRunRepo { fn new() -> Self; fn with_capacity(max: usize) -> Self }`, plus `pub use` of port + record types.

**`Cargo.toml`:**
```toml
[package]
name = "bcs-chat-run-store"
description = "Direct Chat run persistence stores for BCS"
edition = { workspace = true }
license = { workspace = true }
repository = { workspace = true }
rust-version = { workspace = true }
version = { workspace = true }

[dependencies]
async-trait = { workspace = true }
bcs-service-api = { workspace = true }
serde = { workspace = true }
serde_json = { workspace = true }
tokio = { workspace = true }
tracing = { workspace = true }

[dev-dependencies]
tokio = { workspace = true, features = ["macros", "rt", "time"] }

[lints]
workspace = true
```
(Add `bcs-db-api`, `bcs-cache-api` deps in Task 4; keep this crate minimal now.)

Register in workspace `Cargo.toml`: add `"crates/services/bcs-chat-run-store",` to `members` and `bcs-chat-run-store = { path = "crates/services/bcs-chat-run-store" }` to `[workspace.dependencies]`.

**`src/memory.rs` (full):**
```rust
use std::collections::HashMap;
use std::sync::Arc;
use async_trait::async_trait;
use tokio::sync::RwLock;

use bcs_service_api::port::repo::{
    CasOutcome, ChatRunMetricCount, ChatRunRecord, ChatRunRepoError, ChatRunRepoPort,
    ChatRunState, DirectChatClientKind, DirectChatRunState,
};

#[derive(Debug, Default)]
struct Inner {
    runs: HashMap<String, ChatRunRecord>,
    cap: usize,
}

#[derive(Debug, Default, Clone)]
pub struct MemoryChatRunRepo {
    inner: Arc<RwLock<Inner>>,
}

impl MemoryChatRunRepo {
    pub fn new() -> Self { Self::with_capacity(100_000) }
    pub fn with_capacity(cap: usize) -> Self {
        Self { inner: Arc::new(RwLock::new(Inner { runs: HashMap::new(), cap })) }
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis() as u64
}

fn metric_state(s: ChatRunState) -> DirectChatRunState {
    match s {
        ChatRunState::Pending => DirectChatRunState::Pending,
        ChatRunState::Submitted => DirectChatRunState::Submitted,
        ChatRunState::Running => DirectChatRunState::Running,
        ChatRunState::Completed => DirectChatRunState::Completed,
        ChatRunState::Failed => DirectChatRunState::Failed,
        ChatRunState::Cancelled => DirectChatRunState::Cancelled,
    }
}
fn client_kind(c: Option<&str>) -> DirectChatClientKind {
    match c.map(str::trim).filter(|s| !s.is_empty()) {
        None => DirectChatClientKind::None,
        Some("http-chat") => DirectChatClientKind::HttpChat,
        Some("http-chat-async") => DirectChatClientKind::HttpChatAsync,
        Some(raw) if raw.starts_with("bcs-cli") => DirectChatClientKind::BcsCli,
        Some(_) => DirectChatClientKind::Unknown,
    }
}

#[async_trait]
impl ChatRunRepoPort for MemoryChatRunRepo {
    async fn create(&self, record: ChatRunRecord) -> Result<(), ChatRunRepoError> {
        let mut g = self.inner.write().await;
        if g.cap > 0 && g.runs.len() >= g.cap {
            return Err(ChatRunRepoError::Backend(format!("capacity exceeded ({})", g.cap)));
        }
        if g.runs.contains_key(&record.run_id) {
            return Err(ChatRunRepoError::DuplicateRunId(record.run_id.clone()));
        }
        g.runs.insert(record.run_id.clone(), record);
        Ok(())
    }
    async fn get(&self, run_id: &str) -> Result<Option<ChatRunRecord>, ChatRunRepoError> {
        Ok(self.inner.read().await.runs.get(run_id).cloned())
    }
    async fn compare_and_set_state(&self, run_id: &str, expected: u64, mut new: ChatRunRecord) -> Result<CasOutcome, ChatRunRepoError> {
        let mut g = self.inner.write().await;
        let Some(cur) = g.runs.get(run_id) else { return Ok(CasOutcome::Conflict(None)); };
        if cur.state.is_terminal() { return Ok(CasOutcome::Terminal(Some(cur.clone()))); }
        if cur.version != expected { return Ok(CasOutcome::Conflict(Some(cur.clone()))); }
        new.version = cur.version + 1;
        new.updated_at_ms = now_ms();
        g.runs.insert(run_id.to_string(), new.clone());
        Ok(CasOutcome::Applied(new))
    }
    async fn compare_and_set_terminal(&self, run_id: &str, expected: u64, mut new: ChatRunRecord) -> Result<CasOutcome, ChatRunRepoError> {
        // Terminal transition uses the same guard; successful apply records terminal content.
        let mut g = self.inner.write().await;
        let Some(cur) = g.runs.get(run_id) else { return Ok(CasOutcome::Conflict(None)); };
        if cur.state.is_terminal() { return Ok(CasOutcome::Terminal(Some(cur.clone()))); }
        if cur.version != expected { return Ok(CasOutcome::Conflict(Some(cur.clone()))); }
        new.version = cur.version + 1;
        new.updated_at_ms = now_ms();
        if new.completed_at_ms.is_none() { new.completed_at_ms = Some(now_ms()); }
        g.runs.insert(run_id.to_string(), new.clone());
        Ok(CasOutcome::Applied(new))
    }
    async fn append_streaming_content(&self, run_id: &str, expected: u64, accumulated: String, truncated: bool) -> Result<bool, ChatRunRepoError> {
        let mut g = self.inner.write().await;
        let Some(cur) = g.runs.get_mut(run_id) else { return Ok(false); };
        if cur.state.is_terminal() || cur.version != expected { return Ok(false); }
        cur.accumulated_content = accumulated;
        cur.content_truncated = truncated;
        cur.version += 1;
        cur.updated_at_ms = now_ms();
        Ok(true)
    }
    async fn list_active(&self, now_ms: u64) -> Result<Vec<ChatRunRecord>, ChatRunRepoError> {
        Ok(self.inner.read().await.runs.values()
            .filter(|r| !r.state.is_terminal() && r.expires_at_ms < now_ms)
            .cloned().collect())
    }
    async fn delete_expired_terminal(&self, now_ms: u64, retention_ms: u64) -> Result<usize, ChatRunRepoError> {
        let mut g = self.inner.write().await;
        let drop_ids: Vec<String> = g.runs.iter()
            .filter(|(_, r)| r.state.is_terminal()
                && r.completed_at_ms.map_or(false, |c| now_ms.saturating_sub(c) >= retention_ms))
            .map(|(k, _)| k.clone()).collect();
        let n = drop_ids.len();
        for k in drop_ids { g.runs.remove(&k); }
        Ok(n)
    }
    async fn metric_counts(&self) -> Result<Vec<ChatRunMetricCount>, ChatRunRepoError> {
        let mut counts: Vec<ChatRunMetricCount> = Vec::new();
        for r in self.inner.read().await.runs.values() {
            let st = metric_state(r.state);
            let ck = client_kind(r.client.as_deref());
            if let Some(e) = counts.iter_mut().find(|c| c.state == st && c.client_kind == ck) {
                e.count = e.count.saturating_add(1);
            } else {
                counts.push(ChatRunMetricCount { state: st, client_kind: ck, count: 1 });
            }
        }
        Ok(counts)
    }
}
```

**`src/lib.rs`:**
```rust
//! Direct Chat run persistence stores.
pub mod memory;
pub use memory::MemoryChatRunRepo;
pub use bcs_service_api::port::repo::{
    CasOutcome, ChatRunCompletionPolicy, ChatRunRecord, ChatRunRepoError, ChatRunRepoPort,
    ChatRunState, MAX_CONTENT_BYTES,
};
```

> Note: `DirectChatRunState`/`DirectChatClientKind` must be reachable from `bcs_service_api::port::repo`. They are crate-re-exported at `bcs_service_api::` root; if `port::repo` cannot `use crate::...`, import via `bcs_service_api::{DirectChatClientKind, DirectChatRunState}` — adjust the `use` in `memory.rs` accordingly to `use bcs_service_api::{DirectChatClientKind, DirectChatRunState};`.

- [ ] **Step 1: Write the failing test** `tests/memory_repo.rs` covering: create dup → `DuplicateRunId`; capacity → `Backend`; `compare_and_set_state` wrong version → `Conflict`, terminal → `Terminal`, happy → `Applied` with version+1; `append_streaming_content` updates content+version; `list_active` only non-terminal past expiry; `delete_expired_terminal` only terminal past retention; `metric_counts` aggregates.

```rust
use bcs_chat_run_store::{CasOutcome, ChatRunCompletionPolicy, ChatRunRecord, ChatRunRepoPort, ChatRunState};
use bcs_service_api::ChatResponseMode;

fn rec(run_id: &str, version: u64) -> ChatRunRecord {
    let mut r = ChatRunRecord::new(run_id.into(), "bot".into(), "from".into(), "sk".into(), 0, 100_000, Some("http-chat-async".into()), ChatResponseMode::Full, ChatRunCompletionPolicy::WaitForFinal);
    r.version = version;
    r
}
#[tokio::test]
async fn cas_state_applies_and_bumps_version() {
    let s = bcs_chat_run_store::MemoryChatRunRepo::new();
    s.create(rec("r1", 1)).await.unwrap();
    let mut n = rec("r1", 1); n.state = ChatRunState::Running;
    match s.compare_and_set_state("r1", 1, n).await.unwrap() {
        CasOutcome::Applied(r) => assert_eq!(r.version, 2),
        other => panic!("expected Applied, got {other:?}"),
    }
    // wrong version -> Conflict
    match s.compare_and_set_state("r1", 1, rec("r1", 1)).await.unwrap() {
        CasOutcome::Conflict(_) => {}
        other => panic!("expected Conflict, got {other:?}"),
    }
}
#[tokio::test]
async fn terminal_is_immutable() {
    let s = bcs_chat_run_store::MemoryChatRunRepo::new();
    s.create(rec("r2", 1)).await.unwrap();
    let mut t = rec("r2", 1); t.state = ChatRunState::Completed; t.completed_at_ms = Some(5);
    s.compare_and_set_terminal("r2", 1, t).await.unwrap();
    match s.compare_and_set_state("r2", 2, rec("r2", 2)).await.unwrap() {
        CasOutcome::Terminal(_) => {}
        other => panic!("expected Terminal, got {other:?}"),
    }
}
```
(add remaining cases for capacity/list_active/delete_expired/metric_counts in the same file)

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p bcs-chat-run-store`
Expected: FAIL (crate / types missing)

- [ ] **Step 3: Implement `Cargo.toml`, `lib.rs`, `memory.rs`** as above; register workspace member.

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p bcs-chat-run-store`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add Cargo.toml crates/services/bcs-chat-run-store
git commit -m "feat(bcs): add bcs-chat-run-store with MemoryChatRunRepo

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: Refactor `ChatRunStore` into engine-over-port

**Files:**
- Modify: `crates/services/bcs-message-flow/src/a2a_chat/run_store.rs`, `crates/services/bcs-message-flow/Cargo.toml` (add `bcs-chat-run-store` dep)
- Test: existing `crates/services/bcs-message-flow/tests/contract_a2a_chat.rs`, `crates/services/bcs-message-flow/tests/conformance_message_flow_services.rs` (must stay green; no new test required here but the engine's own `#[cfg(test)]` `wait_update` behavior is covered by contract tests)

**Interfaces:**
- Consumes: `bcs_chat_run_store::MemoryChatRunRepo` (Task 2) + port (Task 1).
- Produces: unchanged public API of `ChatRunStore`: `new()`, `with_capacity(usize)`, `with_repo(Arc<dyn ChatRunRepoPort>)`, `create`, `get`, `mark_running`, `mark_submitted`, `mark_detach_delivery_acknowledged`, `append_delta`, `replace_content`, `mark_completed`, `mark_failed`, `mark_cancelled`, `wait_update`, `cleanup_expired`, `metric_counts`, `metric_client_kinds`. `ChatRunStore::new()` MUST default to `MemoryChatRunRepo` so all existing call sites and tests compile and behave identically.

**Refactor approach (preserve behavior):**
- Replace the `slots: RwLock<HashMap<String, Arc<Slot>>>` backing with `repo: Arc<dyn ChatRunRepoPort>` plus a node-local `notifiers: Arc<RwLock<HashMap<String, Arc<Notify>>>>` (lazily-created per run_id, removed on terminal/cleanup).
- Move the `mutate(...)` closure-based logic: each mutator now does:
  1. `let cur = repo.get(run_id).await?` (engine returns `false`/propagates `Backend`);
  2. if `cur.state.is_terminal()` → return `false`;
  3. compute the desired `new = cur.clone()`; apply the same field mutation the closure used;
  4. `match repo.compare_and_set_state(run_id, cur.version, new.clone()).await`:
     - `Applied(r)` → `notify_waiters(run_id)`; return `true`
     - `Conflict(_)` → return `false` (single attempt preserves current single-shot semantics; do NOT add retry loops in memory mode — keep behavior parity. Cross-replica retry is a persistent-mode concern handled in Task 4's engine variant.)
     - `Terminal(_)` → return `false`
     - `Err(Backend)` → propagate up (caller in `mod.rs` handles — see Task 5)
- `append_delta`/`replace_content`: use `append_streaming_content` (computes accumulated + truncated with the EXISTING `is_char_boundary` logic unchanged). For memory mode, `append_streaming_content` does the CAS-gated update and returns `bool`.
- `mark_completed/mark_failed/mark_cancelled`: use `compare_and_set_terminal`. After `Applied`, fire notify and remove the notifier.
- `wait_update`: keep the loop but read via `repo.get`; on `version>since || terminal` return; else wait on `notifiers` notify with a bounded `poll_interval` (use `Duration::from_millis(500)` — same order as today's `Notify`-only behavior; memory mode still wakes immediately via notify). **For memory mode, do NOT add cross-replica polling behavior beyond what tests require** — keep it equivalent.
- `cleanup_expired`: call `repo.list_active(now)` → for each `mark_failed("timeout")`; then `repo.delete_expired_terminal(now, retention)` for drops. Return `(expired, dropped)` matching current signature `(Vec<String>, Vec<String>)`. **client_kind attribution**: `metric_client_kinds()` currently iterates slots — replace with reading `client` from records obtained via a new helper `repo.get` per run before deletion, OR keep a node-local `metric_client_kinds` snapshot. Simplest behavior-preserving option: before `delete_expired_terminal`, query `repo.get` for each to-be-dropped id to capture `client` for the lifecycle hook. Add a private `async fn snapshot_client_kinds(&self, ids) -> HashMap<String, DirectChatClientKind>` that does `repo.get` per id.
- `metric_counts`: delegate to `repo.metric_counts()`.
- `metric_client_kinds`: return a `HashMap` by scanning — but we no longer hold all slots. Keep this method but implement via `repo.metric_counts()` is insufficient (it aggregates, not per-run). **Decision:** wire `metric_client_kinds` to be used ONLY by `cleanup_expired` for lifecycle attribution (Task 5 will show it's the only caller — confirmed in prep: `mod.rs:888`). Replace the `cleanup_expired` attribution with per-run `repo.get` (above) and DELETE `metric_client_kinds` if no other caller exists. Verify with: `grep -rn "metric_client_kinds" crates --include=*.rs` — if only `mod.rs:888` + the impl, remove both.

- [ ] **Step 1: Write a focused engine test** in `run_store.rs`'s `#[cfg(test)]` mod asserting `ChatRunStore::new()` (default memory repo) preserves: create→get, mark_running from Pending only, terminal immortality, append_delta version bump, wait_update returns after a mutation (spawn a mutator concurrently). Reuse the existing test patterns in that file.

- [ ] **Step 2: Run it to verify it fails** (after step 3 scaffolding) — or run existing contract tests first to establish the refactor doesn't regress.

Run: `cargo test -p bcs-message-flow -- a2a` (contract) and `cargo test -p bcs-message-flow -- conformance`
Expected before refactor: PASS. After refactor: PASS.

- [ ] **Step 3: Implement the refactor** in `run_store.rs`. Keep `MAX_CONTENT_BYTES` re-exported (`pub use bcs_service_api::port::repo::MAX_CONTENT_BYTES`) and the type re-exports (`pub use bcs_service_api::port::repo::{ChatRunRecord, ChatRunState, ChatRunCompletionPolicy}`). Remove the now-moved struct/enum/impl definitions. Keep `ChatRunStoreError`, `direct_chat_metric_state`, `direct_chat_client_kind` (engine-local). Update `ChatRunStoreError::direct_chat_reason` unchanged.

- [ ] **Step 4: Run message-flow tests**

Run: `cargo test -p bcs-message-flow`
Expected: PASS (all existing tests green = behavior preserved).

- [ ] **Step 5: Commit**

```bash
git add crates/services/bcs-message-flow/src/a2a_chat/run_store.rs crates/services/bcs-message-flow/Cargo.toml
git commit -m "refactor(bcs): ChatRunStore as state-machine engine over ChatRunRepoPort

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: `SqlChatRunRepo` (MySQL authoritative + Redis hot cache)

**Files:**
- Create: `crates/services/bcs-chat-run-store/src/sql.rs`; extend `src/lib.rs`; extend `Cargo.toml` with `bcs-db-api`, `bcs-cache-api` deps + dev-deps `bcs-db-local`, `bcs-cache-local`.
- Create test: `crates/services/bcs-chat-run-store/tests/sql_repo.rs`
- Modify: `crates/bootstrap/bcs/src/migrations.rs` (SQLite DDL), `migrations/mysql/011_chat_runs.sql` (MySQL DDL)

**Interfaces:**
- Produces: `bcs_chat_run_store::SqlChatRunRepo` with `pub fn new(db: Arc<dyn DbPlugin>, flavor: DbSqlFlavor, cache: Arc<dyn CachePlugin>, key_prefix: String, env: String) -> Self` and `pub fn sqlite(db, cache, key_prefix, env) -> Self` (flavor = Sqlite). Implements `ChatRunRepoPort`.

**DDL — SQLite (add to `SQLITE_DDL_STATEMENTS`):**
```sql
"CREATE TABLE IF NOT EXISTS bcs_chat_runs (
    run_id TEXT PRIMARY KEY,
    bot_uuid TEXT NOT NULL,
    from_bot_id TEXT NOT NULL,
    session_key TEXT NOT NULL,
    state TEXT NOT NULL,
    accumulated_content TEXT,
    error_message TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    expires_at_ms INTEGER NOT NULL,
    version INTEGER NOT NULL,
    content_truncated INTEGER NOT NULL DEFAULT 0,
    client TEXT,
    response_mode TEXT NOT NULL,
    completion_policy TEXT NOT NULL,
    delivery_ack_at_ms INTEGER
)",
"CREATE INDEX IF NOT EXISTS idx_chat_runs_expires ON bcs_chat_runs(state, expires_at_ms)",
"CREATE INDEX IF NOT EXISTS idx_chat_runs_completed ON bcs_chat_runs(state, completed_at_ms)",
"CREATE INDEX IF NOT EXISTS idx_chat_runs_from_bot ON bcs_chat_runs(from_bot_id)",
```

**`migrations/mysql/011_chat_runs.sql`:**
```sql
CREATE TABLE IF NOT EXISTS `bcs_chat_runs` (
  `run_id`              VARCHAR(64)  NOT NULL,
  `bot_uuid`            VARCHAR(128) NOT NULL,
  `from_bot_id`         VARCHAR(128) NOT NULL,
  `session_key`         VARCHAR(128) NOT NULL,
  `state`               VARCHAR(16)  NOT NULL,
  `accumulated_content` MEDIUMTEXT,
  `error_message`       TEXT,
  `created_at_ms`       BIGINT       NOT NULL,
  `updated_at_ms`       BIGINT       NOT NULL,
  `completed_at_ms`     BIGINT,
  `expires_at_ms`       BIGINT       NOT NULL,
  `version`             BIGINT       NOT NULL,
  `content_truncated`   TINYINT      NOT NULL DEFAULT 0,
  `client`              VARCHAR(64),
  `response_mode`       VARCHAR(32)  NOT NULL,
  `completion_policy`   VARCHAR(32)  NOT NULL,
  `delivery_ack_at_ms`  BIGINT,
  PRIMARY KEY (`run_id`),
  KEY `idx_chat_runs_expires` (`state`, `expires_at_ms`),
  KEY `idx_chat_runs_completed` (`state`, `completed_at_ms`),
  KEY `idx_chat_runs_from_bot` (`from_bot_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**SQL (shared subset works on both SQLite and MySQL):**
```sql
-- create
INSERT INTO bcs_chat_runs (run_id, bot_uuid, from_bot_id, session_key, state, accumulated_content,
  error_message, created_at_ms, updated_at_ms, completed_at_ms, expires_at_ms, version,
  content_truncated, client, response_mode, completion_policy, delivery_ack_at_ms)
VALUES (?,?,?,?,?,'','',?,?,NULL,?,?,0,?,?,?);
-- get
SELECT ... FROM bcs_chat_runs WHERE run_id = ?;
-- compare_and_set_state
UPDATE bcs_chat_runs SET state=?, updated_at_ms=?, delivery_ack_at_ms=?, version=version+1
  WHERE run_id=? AND version=? AND state NOT IN ('completed','failed','cancelled');
-- compare_and_set_terminal
UPDATE bcs_chat_runs SET state=?, accumulated_content=?, error_message=?, updated_at_ms=?,
  completed_at_ms=?, content_truncated=?, version=version+1
  WHERE run_id=? AND version=? AND state NOT IN ('completed','failed','cancelled');
-- list_active
SELECT ... FROM bcs_chat_runs WHERE state NOT IN ('completed','failed','cancelled') AND expires_at_ms < ?;
-- delete_expired_terminal
DELETE FROM bcs_chat_runs WHERE state IN ('completed','failed','cancelled') AND completed_at_ms < ?;
-- metric_counts
SELECT state, client, COUNT(*) AS c FROM bcs_chat_runs GROUP BY state, client;
```
Implementation parses affected_rows from `DbExecuteResult` (1 = Applied; 0 = need a follow-up `SELECT` to classify Conflict vs Terminal: read current row — if missing → `Conflict(None)`, if terminal → `Terminal(cur)`, else → `Conflict(cur)`).

**Redis hot cache (streaming content):**
- Cache key `{key_prefix}chat_run:{run_id}` stores a JSON `ChatRunRecord` snapshot via `cache.set_value(key, bytes, Some(ttl), Upsert)`, ttl = remaining `expires_at_ms - now` (min 1s).
- `append_streaming_content`: read current from cache (or DB) to compute new accumulated (engine passes the full new `accumulated` already, so just write the snapshot with version+1). Set cache ttl = remaining expiry.
- `get`: for non-terminal runs, read cache first; if miss, read DB and warm cache. For terminal runs, read DB authoritative (cache may be stale/absent) — warm optional.
- `compare_and_set_terminal`: after DB UPDATE succeeds, `cache.delete(key)` (terminal is authoritative in DB; avoid stale streaming snapshot). Version continuity: the terminal UPDATE sets `version=version+1` from DB's current; because streaming version advanced only in cache, a terminal arriving when DB version is lower is fine (DB version is the CAS guard). The engine MUST pass `expected_version = DB current version` (obtained from a prior `get` that read DB authoritative for the terminal path). Document this in `sql.rs`.

**Row↔record mapping** in `sql.rs`: a `fn row_to_record(row: &DbRow) -> Result<ChatRunRecord, DbError>` using `db_get_column`/`row.get_string` etc. Map `state`/`response_mode`/`completion_policy` from strings (mirror `ChatRunState::as_str` reverse + existing `ChatResponseMode` serde). `content_truncated`/bool via `get_bool`.

- [ ] **Step 1: Write failing test** `tests/sql_repo.rs` using `bcs_db_local` SQLite plugin + `bcs_cache_local::InMemoryCachePlugin`. Cover: create/get roundtrip; CAS state Applied/Conflict/Terminal; terminal writes content + sets completed_at_ms; append_streaming_content writes cache and is visible; list_active/delete_expired_terminal/metric_counts. Run schema via `run_sqlite_bootstrap_tables`-equivalent or direct `db.execute(CREATE TABLE)` in test setup.

- [ ] **Step 2: Run to verify fail**

Run: `cargo test -p bcs-chat-run-store --features '' --test sql_repo` (or plain `cargo test -p bcs-chat-run-store`)
Expected: FAIL

- [ ] **Step 3: Implement `sql.rs` + `lib.rs` exports + DDL (both migrations).**

- [ ] **Step 4: Run to verify pass**

Run: `cargo test -p bcs-chat-run-store`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crates/services/bcs-chat-run-store crates/bootstrap/bcs/src/migrations.rs migrations/mysql/011_chat_runs.sql
git commit -m "feat(bcs): SqlChatRunRepo with MySQL authority + Redis hot cache

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: `A2aChat` write-failure propagation + terminal co-op

**Files:**
- Modify: `crates/services/bcs-message-flow/src/a2a_chat/mod.rs`

**Interfaces:** unchanged public API; internal: `A2aChat` holds `run_store: Arc<ChatRunStore>` (engine). Engine mutators that used to return `bool` now may return `Result<bool, ChatRunRepoError>` OR keep `bool` and log on `Backend` (decision: keep `bool` return to preserve call sites, and on `Err(Backend)` log `error!` + `emit_run_lifecycle(Failed, InternalError)` and return `false`). This avoids cascading signature changes across ~20 call sites while still propagating the failure observably and never claiming success.

**Changes:**
- In `run_store.rs`, each mutating method that calls `repo.compare_and_set_*` / `append_streaming_content`: on `Err(ChatRunRepoError::Backend(e))` → `tracing::error!(run_id, error=%e, "chat run store backend failure")`; return `false` (do not panic). The store keeps `bool` returns. Engine exposes `pub async fn last_backend_ok(&self) -> bool`? No — keep simple. Document that `create()` returns `Result<(), ChatRunStoreError>` already and propagates `Backend` (map `ChatRunRepoError::Backend` → `ChatRunStoreError` → `ServiceError::InternalError`).
- In `chat()` (mod.rs:488) the `run_store.create(...)` failure path already returns `ServiceError::InternalError` — ensure `ChatRunRepoError::Backend` is mapped there (add a mapping fn `repo_err_to_service_error`).
- **Terminal co-op with BotRun:** after a successful `mark_completed`/`mark_failed`/`mark_cancelled` in `record_run_event` and `cancel_run`, call `self.bot_run_context` (if wired) `mark_terminal(run_id)` best-effort (fire-and-forget + log on error). This aligns the BotRun terminal flag when direct chat terminates. group_flow path already calls `mark_terminal` independently; no change there.
- `cleanup_expired`'s `emit_run_lifecycle` for `Expired`/`Dropped` unchanged; attribution uses per-run `repo.get` (Task 3) — verify `mod.rs:888 metric_client_kinds` call is replaced.

- [ ] **Step 1: Test** — extend `contract_a2a_chat.rs` with a case injecting a failing repo (wrap `MemoryChatRunRepo` in a struct that returns `Backend` on `create`) and assert `start_async_chat` returns `Err(ServiceError::InternalError(...))` (not success).

- [ ] **Step 2: Run to verify fail / baseline**

Run: `cargo test -p bcs-message-flow -- a2a`
Expected: FAIL (new test) or PASS baseline.

- [ ] **Step 3: Implement** the error mapping + terminal co-op calls.

- [ ] **Step 4: Run to verify pass**

Run: `cargo test -p bcs-message-flow`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crates/services/bcs-message-flow/src/a2a_chat/mod.rs crates/services/bcs-message-flow/src/a2a_chat/run_store.rs
git commit -m "feat(bcs): propagate chat run store write failures and co-op BotRun terminal

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: `RedisBotRunContextStore` (BotRun Redis governance)

**Files:**
- Create: `crates/services/bcs-message-flow/src/run_context_redis.rs`; modify `src/lib.rs` (`pub mod run_context_redis;` + re-export `RedisBotRunContextStore`).
- Create test: `crates/services/bcs-message-flow/tests/run_context_redis.rs`
- Modify: `crates/services/bcs-message-flow/Cargo.toml` — add `bcs-cache-api` dep (already present? verify; add if not).

**Interfaces:** implements `bcs_service_api::BotRunContextPort` (existing trait in `port/chat_run.rs`: `put_context`, `get_context`, `try_begin_terminal`, `mark_terminal`, `release_terminal`, `begin_provider_transport`, `bind_provider_transport`, `get_provider_transport`, `mark_provider_transport_terminal`, `clear_provider_transport`, `cleanup_expired`). Constructor `pub fn new(cache: Arc<dyn CachePlugin>, key_prefix: String, retention_ms: u64) -> Self`.

**Design:**
- Context record key `{prefix}botrun:{run_id}` → JSON `BotRunContext`, TTL = `deadline_ms + retention_ms` (seconds). `put_context` upserts; `get_context` reads.
- Terminal claim: `try_begin_terminal` uses `cache.set_value("{prefix}botrun:claim:{run_id}", b"1", Some(claim_ttl), CacheSetMode::InsertOnly)` → true if acquired (`SET NX` success). `release_terminal` deletes claim. `mark_terminal`: read context, set `terminal=true`, write back via `set_value` (upsert) + delete claim. (Atomic terminal monotonicity across replicas: since `mark_terminal` only runs after `try_begin_terminal` succeeded on this caller, and the claim key is `NX`, duplicate terminals are already prevented at claim time. Document this — Lua is NOT required because the claim-then-mark sequence with the NX claim key is the atomic gate.)
- `provider_transports`: key `{prefix}botrun:transport:{run_id}` → JSON `{state,deadline_ms}` (or a small struct). `begin_provider_transport`: `set_value NX`; `bind_provider_transport`: read-modify-write (acceptable race window: only one BCS node delivers a run, so no cross-replica contention); `mark_provider_transport_terminal`/`clear_provider_transport`: write/delete.
- `cleanup_expired`: `CachePlugin` TTLs handle eviction; this method can be a no-op returning 0 (TTL-driven) — keep the signature; document that Redis-side TTL replaces the in-memory sweep.

- [ ] **Step 1: Write failing test** against `bcs_cache_local::InMemoryCachePlugin` (no real Redis needed): put/get context; `try_begin_terminal` true once, false second; `mark_terminal` flips terminal; transport begin/bind once/reject mixed; `get_context` returns terminal after mark.

- [ ] **Step 2: Run to verify fail**

Run: `cargo test -p bcs-message-flow -- run_context_redis`
Expected: FAIL

- [ ] **Step 3: Implement `run_context_redis.rs`.** Mirror `MemoryBotRunContextStore`'s behavior exactly (same enum transitions, same mixed-source rejection) but backed by `CachePlugin` JSON.

- [ ] **Step 4: Run to verify pass**

Run: `cargo test -p bcs-message-flow -- run_context`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crates/services/bcs-message-flow/src/run_context_redis.rs crates/services/bcs-message-flow/src/lib.rs crates/services/bcs-message-flow/Cargo.toml crates/services/bcs-message-flow/tests/run_context_redis.rs
git commit -m "feat(bcs): RedisBotRunContextStore for governed provider run context

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: Config switch + bootstrap wiring

**Files:**
- Modify: `crates/bootstrap/bcs/src/config.rs` (add fields + default fns), `crates/bootstrap/bcs/src/server.rs` (wire repos at production + in-memory sites), `crates/bootstrap/bcs/Cargo.toml` (add `bcs-chat-run-store`).

**Config (in `BcsConfig`, near `async_chat_run_max_entries` ~line 754):**
```rust
/// Direct chat run store backend: "memory" (in-process, pre-#1546 behavior,
/// not restart/replica safe) or "persistent" (MySQL authority + Redis cache).
/// Default "memory".
#[serde(default = "default_async_chat_run_store")]
pub async_chat_run_store: String,

/// Provider downlink run context backend: "memory" or "redis".
/// Default "memory".
#[serde(default = "default_bot_run_context_store")]
pub bot_run_context_store: String,
```
Defaults:
```rust
fn default_async_chat_run_store() -> String { "memory".to_string() }
fn default_bot_run_context_store() -> String { "memory".to_string() }
```

**Bootstrap wiring (`server.rs`):**
- At production site (`new_with_infrastructure`, ~line 4254 where `ChatRunStore::with_capacity` is currently called):
  ```rust
  let chat_run_repo: Arc<dyn ChatRunRepoPort> = match config.async_chat_run_store.as_str() {
      "persistent" => Arc::new(bcs_chat_run_store::SqlChatRunRepo::new(
          db_plugin.clone(), db_flavor, cache_plugin.clone(), cache_key_prefix.clone(), env.clone(),
      )),
      _ => Arc::new(bcs_chat_run_store::MemoryChatRunRepo::with_capacity(config.async_chat_run_max_entries)),
  };
  let a2a_run_store = Arc::new(bcs_message_flow::a2a_chat::ChatRunStore::with_repo(chat_run_repo));
  ```
  (Remove the old `ChatRunStore::with_capacity(...)` line.)
- `BotRunContextPort` construction near `server.rs:1902` (`let bot_run_context: Arc<dyn BotRunContextPort> = ...`):
  ```rust
  let bot_run_context: Arc<dyn BotRunContextPort> = if config.bot_run_context_store == "redis" {
      Arc::new(bcs_message_flow::RedisBotRunContextStore::new(cache_plugin.clone(), cache_key_prefix.clone(), config.async_chat_run_retention_ms))
  } else {
      Arc::new(bcs_message_flow::MemoryBotRunContextStore::new())  // or existing constructor
  };
  ```
  Apply at all three sites (4254 production, 3472 in-memory, 1927 Default) — Default site always memory (no DB); production reads config; the mid site reads config but DB may be absent so guard: if `persistent`/`redis` requested but DB/cache unavailable, fall back to memory with a `warn!` log.
- `ChatRunStore` constructor change: the current calls use `ChatRunStore::with_capacity(...)`. Keep `with_capacity` as a convenience that builds a `MemoryChatRunRepo`. Add `with_repo(Arc<dyn ChatRunRepoPort>) -> Self`.

- [ ] **Step 1: Write a smoke test** asserting default config (`async_chat_run_store == "memory"`) and that a config with `async_chat_run_store = "persistent"` parses. (Config parse test in `config.rs` test module following existing `test_config_*` patterns.)

- [ ] **Step 2: Run to verify fail/baseline**

Run: `cargo test -p bcs -- config`
Expected: FAIL (new test) or baseline PASS.

- [ ] **Step 3: Implement** config fields + defaults + server.ts wiring.

- [ ] **Step 4: Build + run bootstrap tests**

Run: `cargo build -p bcs && cargo test -p bcs`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add crates/bootstrap/bcs/src/config.rs crates/bootstrap/bcs/src/server.rs crates/bootstrap/bcs/Cargo.toml
git commit -m "feat(bcs): configurable run store backends (memory default, persistent opt-in)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: Governance integration tests (restart / replica / idempotent / write-fail / audit / SSE-timeout / config)

**Files:**
- Create: `crates/bootstrap/bcs/tests/run_governance_restart.rs`

**Cases:**
1. **Restart queryable:** `SqlChatRunRepo` (SQLite) creates a run; drop the repo handle; build a new `ChatRunStore::with_repo` over a `SqlChatRunRepo` on the SAME db file; `get` returns accumulated_content/version/terminal unchanged.
2. **Cross-replica:** two `ChatRunStore` engines over one `SqlChatRunRepo`; A.create, B.get sees it; A.mark_completed, B.wait_run sees terminal.
3. **Terminal idempotent:** concurrent `mark_completed` + `mark_failed` (two tokio tasks) → exactly one `Applied`, the other `Conflict`/`Terminal` → only one terminal state stored.
4. **Cancel idempotent:** two `cancel_run` → both report terminal, state stays Cancelled, version advances once.
5. **Write-fail propagation:** `MemoryChatRunRepo` wrapped to fail `create` → HTTP `POST /bots/{id}/chat-async` returns 5xx (via existing route test harness or a unit-level `start_async_chat` call asserting `Err(ServiceError::InternalError)`).
6. **TTL not over-deleting:** active run with future `expires_at` survives `cleanup_expired`; terminal run within retention survives; only overdue active → failed, terminal past retention → dropped.
7. **Notify latency-only:** with two engines over one repo, B.wait_run returns within `poll_interval` after A mutates even though A's Notify doesn't reach B.
8. **Audit SQL:** after terminal, raw `SELECT * FROM bcs_chat_runs WHERE run_id=?` via DbPlugin returns state/content/timestamps/owner matching the API record.
9. **Config switch:** `memory` config → `MemoryChatRunRepo` selected (assert via a type-tag or behavior: no DB row written after create when memory); `persistent` → row exists.
10. **SSE reader-death timeout (option A):** simulate by creating a run with short `expires_at`, never marking terminal, run `cleanup_expired(now)` past expiry → run becomes `failed("timeout")` and is queryable with accumulated content.

- [ ] **Step 1: Write the tests** (one `#[tokio::test]` per case; use `bcs_db_local` SQLite + temp file, `bcs_cache_local`).

- [ ] **Step 2: Run**

Run: `cargo test -p bcs -- run_governance_restart`
Expected: PASS (Cases 1–4,6–10 likely pass once Tasks 1–7 done; Case 5 depends on Task 5; fix until green.)

- [ ] **Step 3: Commit**

```bash
git add crates/bootstrap/bcs/tests/run_governance_restart.rs
git commit -m "test(bcs): run governance restart/replica/idempotency/audit/config

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 9: Spec-linked design doc finalization

**Files:**
- Already created: `docs/superpowers/specs/2026-08-27-bcs-run-governance-design.md`
- Verify it matches the implemented config field names and the `with_repo`/`SqlChatRunRepo::new` signatures; fix any drift.

- [ ] **Step 1: Diff spec against final code** (config keys, constructor signatures, key prefixes). Edit spec to match if needed.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-27-bcs-run-governance-design.md
git commit -m "docs(bcs): finalize run governance design spec

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Verification (whole plan)

```bash
cargo build --release -p bcs -p bcs-chat-run-store -p bcs-cache-redis
cargo test -p bcs-service-api
cargo test -p bcs-chat-run-store
cargo test -p bcs-message-flow
cargo test -p bcs --workspace
```
All green = done. Any `ChatRunEventPort`/`RunChannelManager`/SSE-reader code unchanged (grep-diff to confirm no accidental edits).

## Out of Scope (do not implement in this plan)
- SSE text-assembly unification (`StreamTextAssembler`) — separate refactor.
- group `MessageTracker` / `ProviderBotEvents.visible_text` — issue decision-pending.
- admin invocation / interaction persistence — separate issue work items.
- `ChatRunEventPort`/`RunChannelManager` cross-node routing / SSE reader handoff — issue non-acceptance.