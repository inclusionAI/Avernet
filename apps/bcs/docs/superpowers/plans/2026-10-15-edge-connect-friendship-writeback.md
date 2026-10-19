# Edge-Connect Friendship Writeback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror edge-permission Bot↔Bot friendship decisions (connect auto-approve, manual approve, revoke) back into the legacy `bcs_friendships` store that the collaboration group gate still reads, fixing the `403 Bot is not friends` on `/collaboration/groups`.

**Architecture:** New outbound port `FriendshipWritebackPort` in `bcs-service-api::port`, implemented by a new `FriendshipWritebackService` in `bcs-friend` over `FriendRepoPort` + `RelationCoreService` (no Arc cycle: it holds only repos, never `FriendCore`/`DbConnectService`). `DbConnectService` gains an optional port field, a builder setter, and two best-effort private helpers called from three trigger points. Bootstrap wires the real impl in the DB profile only.

**Tech Stack:** Rust (tokio + async-trait), axum-free service layer, SQLite-backed test doubles. Spec: `apps/bcs/docs/superpowers/specs/2026-10-15-edge-connect-friendship-writeback-design.md`.

**Workspace root for all cargo commands: `apps/bcs/`** (crate names are workspace members, e.g. `--package bcs-edge-permission`).

**Repo rule:** never run `cargo fmt` (see `apps/bcs/CLAUDE.md`).

---

### Task 1: `FriendshipWritebackPort` trait + Noop (bcs-service-api)

**Files:**
- Create: `apps/bcs/crates/service-api/bcs-service-api/src/port/friendship_writeback.rs`
- Modify: `apps/bcs/crates/service-api/bcs-service-api/src/port/mod.rs`

- [ ] **Step 1: Create the port file with trait + Noop**

Create `apps/bcs/crates/service-api/bcs-service-api/src/port/friendship_writeback.rs`:

```rust
//! Edge-permission → legacy friendship reverse-sync port (coexistence window).
//!
//! While the old friend system (`bcs_friendships`, gated by the collaboration
//! group check in `bcs-group`) and the new edge-permission model
//! (`edge_grants`, gated by the friend-connection admission check) run in
//! parallel, friendships approved through the edge-permission Connect flow
//! must be mirrored into the legacy store so the group gate sees them.
//!
//! Spec: `docs/superpowers/specs/2026-10-15-edge-connect-friendship-writeback-design.md`
//!
//! Implementations are expected to be best-effort from the *caller's* point of
//! view: `DbConnectService` logs-and-ignores errors from this port because
//! `edge_grants` is already the source of truth and there is no cross-store
//! transaction.

use async_trait::async_trait;

use crate::ServiceResult;

/// Mirror an edge-permission friendship decision into the legacy store.
///
/// Only Bot↔Bot pairs are in scope; implementations may assume the pair has
/// been kind-checked by the caller but should stay idempotent.
#[async_trait]
pub trait FriendshipWritebackPort: Send + Sync {
    /// Insert the friendship row (idempotent; re-approving an approved pair
    /// backfills legacy rows written before this sync existed).
    async fn add_friendship(&self, caller: &str, target: &str) -> ServiceResult<()>;

    /// Remove the friendship row. Returns `true` when a row was actually
    /// removed, `false` when the pair did not exist.
    async fn remove_friendship(&self, caller: &str, target: &str) -> ServiceResult<bool>;
}

/// No-op writeback port for tests and for profiles running without the
/// legacy store (memory/local bootstrap profiles).
pub struct NoopFriendshipWritebackPort;

#[async_trait]
impl FriendshipWritebackPort for NoopFriendshipWritebackPort {
    async fn add_friendship(&self, _caller: &str, _target: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn remove_friendship(&self, _caller: &str, _target: &str) -> ServiceResult<bool> {
        Ok(false)
    }
}
```

- [ ] **Step 2: Register the module in `port/mod.rs`**

In `apps/bcs/crates/service-api/bcs-service-api/src/port/mod.rs`, next to the existing `pub mod friend_auth_sync;` line (≈line 12) add:

```rust
pub mod friendship_writeback;
```

And in the corresponding `pub use` block (the one containing
`pub use friend_auth_sync::...` style re-exports; if the item is re-exported as a
whole module, follow the file's existing pattern) add:

```rust
pub use friendship_writeback::{FriendshipWritebackPort, NoopFriendshipWritebackPort};
```

Note: match the file's actual layout — `friend_connect_notification` is imported via
`use bcs_service_api::port::{FriendConnectNotificationCommand, ...}` in consumers, so a
`pub use` re-export at `port::` level is the established pattern.

- [ ] **Step 3: Verify compilation**

Run: `cd apps/bcs && cargo check --package bcs-service-api 2>&1 | tail -5`
Expected: no errors (warnings acceptable). Exit code 0.

- [ ] **Step 4: Commit**

```bash
git add apps/bcs/crates/service-api/bcs-service-api/src/port/friendship_writeback.rs \
        apps/bcs/crates/service-api/bcs-service-api/src/port/mod.rs
git commit -m "feat(bcs): add FriendshipWritebackPort for edge->legacy friendship sync"
```

---

### Task 2: `FriendshipWritebackService` in bcs-friend (TDD)

**Files:**
- Test: `apps/bcs/crates/services/bcs-friend/tests/friendship_writeback.rs` (new)
- Create: `apps/bcs/crates/services/bcs-friend/src/core/friendship_writeback.rs`
- Modify: `apps/bcs/crates/services/bcs-friend/src/core/mod.rs`
- Modify: `apps/bcs/crates/services/bcs-friend/src/lib.rs`

- [ ] **Step 1: Write the failing tests**

Create `apps/bcs/crates/services/bcs-friend/tests/friendship_writeback.rs`:

```rust
//! `FriendshipWritebackService` contract: mirror add/remove into the legacy
//! friendship repo, with a best-effort relation-graph mirror.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_friend::{FriendshipWritebackService, MemoryFriendRepo};
use bcs_service_api::{
    FriendshipWritebackPort, FriendRepoPort, RelationCoreService, ServiceError, ServiceResult,
};
use bcs_test_support::NoopRelationCoreService;

fn writeback() -> (FriendshipWritebackService, Arc<MemoryFriendRepo>) {
    let repo = Arc::new(MemoryFriendRepo::new());
    let service = FriendshipWritebackService::new(
        repo.clone(),
        Arc::new(NoopRelationCoreService),
    );
    (service, repo)
}

#[tokio::test]
async fn add_friendship_writes_legacy_row_idempotently() {
    let (service, repo) = writeback();

    service
        .add_friendship("bot_a:1", "20260525_x:85020")
        .await
        .expect("add friendship");

    assert!(repo
        .are_friends("bot_a:1", "20260525_x:85020")
        .await
        .expect("are_friends"));

    // Idempotent: re-approve of the same pair must not error.
    service
        .add_friendship("20260525_x:85020", "bot_a:1")
        .await
        .expect("re-add friendship (idempotent)");

    assert_eq!(
        repo.list_friends("bot_a:1").await.expect("list friends"),
        vec!["20260525_x:85020".to_string()]
    );
}

#[tokio::test]
async fn remove_friendship_deletes_row_and_reports_once() {
    let (service, repo) = writeback();
    service
        .add_friendship("bot_a:1", "bot_b:2")
        .await
        .expect("add friendship");

    assert!(
        service
            .remove_friendship("bot_a:1", "bot_b:2")
            .await
            .expect("remove friendship")
    );
    assert!(
        !service
            .remove_friendship("bot_a:1", "bot_b:2")
            .await
            .expect("re-remove friendship")
    );
    assert!(!repo
        .are_friends("bot_a:1", "bot_b:2")
        .await
        .expect("are_friends"));
}

#[tokio::test]
async fn repo_failure_propagates_to_caller() {
    let service = FriendshipWritebackService::new(
        Arc::new(FailingFriendRepo),
        Arc::new(NoopRelationCoreService),
    );

    let err = service
        .add_friendship("bot_a:1", "bot_b:2")
        .await
        .expect_err("repo failure must surface");

    assert!(
        matches!(err, ServiceError::InternalError(ref message) if message == "friend store unavailable"),
        "got {err:?}"
    );
}

/// Minimal failing repo: enough of `FriendRepoPort` for the propagation test;
/// unused methods use the trait's default/panic-free stubs where provided.
struct FailingFriendRepo;

#[async_trait]
impl FriendRepoPort for FailingFriendRepo {
    async fn list_friends(&self, _bot_id: &str) -> ServiceResult<Vec<String>> {
        Err(ServiceError::InternalError(
            "friend store unavailable".to_string(),
        ))
    }

    async fn are_friends(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<bool> {
        Err(ServiceError::InternalError(
            "friend store unavailable".to_string(),
        ))
    }

    async fn add_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<()> {
        Err(ServiceError::InternalError(
            "friend store unavailable".to_string(),
        ))
    }

    async fn remove_all_friendships(&self, _bot_id: &str) -> ServiceResult<usize> {
        Ok(0)
    }

    async fn remove_friendship(&self, _bot_a: &str, _bot_b: &str) -> ServiceResult<bool> {
        Ok(false)
    }
}

// Silence unused-import warning for RelationCoreService if NoopRelation covers it.
#[allow(dead_code)]
fn _assert_relation_bound(relation: &dyn RelationCoreService) {}
```

Note: check whether `FriendshipWritebackPort` is reachable at the service-api
root (`bcs_service_api::FriendshipWritebackPort`). If Task 1's `port/mod.rs`
re-export makes the root `lib.rs` `pub use` chain expose it, prefer the shorter
path; otherwise keep `bcs_service_api::port::FriendshipWritebackPort` in this
import list.

- [ ] **Step 2: Run tests to verify they fail to compile**

Run: `cargo test --package bcs-friend --test friendship_writeback 2>&1 | tail -10`
Expected: FAIL — compile error: `FriendshipWritebackService` / `FriendshipWritebackPort` not found.

- [ ] **Step 3: Implement `FriendshipWritebackService`**

Create `apps/bcs/crates/services/bcs-friend/src/core/friendship_writeback.rs`:

```rust
//! Reverse-sync adapter: mirrors edge-permission friendship decisions back
//! into the legacy friendship store while both systems coexist.
//!
//! Same persistence shape as `FriendCore::add_friendship` /
//! `FriendCore::remove_friendship` (repo row + relation-graph friend-edge
//! mirror) minus the edge-permission forward sync — the edge already exists
//! whenever this runs, so calling back into it would be redundant.
//!
//! Error policy split:
//! - repo failures propagate (`Err`) so the caller can log them;
//! - relation-mirror failures are best-effort `warn!` — the repo row is what
//!   the collaboration group gate reads, a missing relation mirror is
//!   reconcilable later.
//!
//! Env: the repo resolves its own env (`resolve_env()`), the same resolution
//! the group gate (`bcs-group` `ensure_v1_reachable` → `DbFriendStore`) uses,
//! so a row written here is always readable by the gate. Do NOT pass
//! `DbConnectService`'s env through — aligning with the reader is the
//! invariant (see spec §4.2).

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::{
    FriendRepoPort, RelationCoreService, ServiceResult,
};
use tracing::warn;

use bcs_service_api::port::FriendshipWritebackPort;

/// Writes friendship pairs into the legacy `bcs_friendships` store plus the
/// `bcs_actor_relations` friend-edge mirror.
pub struct FriendshipWritebackService {
    repo: Arc<dyn FriendRepoPort>,
    relation: Arc<dyn RelationCoreService>,
}

impl FriendshipWritebackService {
    pub fn new(
        repo: Arc<dyn FriendRepoPort>,
        relation: Arc<dyn RelationCoreService>,
    ) -> Self {
        Self { repo, relation }
    }
}

#[async_trait]
impl FriendshipWritebackPort for FriendshipWritebackService {
    async fn add_friendship(&self, caller: &str, target: &str) -> ServiceResult<()> {
        self.repo.add_friendship(caller, target).await?;

        let env = bcs_config::resolve_env_str();
        if let Err(err) = self.relation.add_friend_edges(caller, target, &env).await {
            warn!(
                left_bot = %caller,
                right_bot = %target,
                env = %env,
                error = %err,
                "friendship writeback: relation mirror add failed; friendship repo row exists"
            );
        }

        Ok(())
    }

    async fn remove_friendship(&self, caller: &str, target: &str) -> ServiceResult<bool> {
        let removed = self.repo.remove_friendship(caller, target).await?;

        if removed {
            let env = bcs_config::resolve_env_str();
            if let Err(err) = self.relation.remove_friend_edges(caller, target, &env).await {
                warn!(
                    left_bot = %caller,
                    right_bot = %target,
                    env = %env,
                    error = %err,
                    "friendship writeback: relation mirror remove failed; friendship repo row removed"
                );
            }
        }

        Ok(removed)
    }
}
```

- [ ] **Step 4: Wire module exports**

`apps/bcs/crates/services/bcs-friend/src/core/mod.rs` — add after `pub mod friend_request_core;`:

```rust
pub mod friendship_writeback;
```

and after `pub use friend_request_core::FriendRequestCore;`:

```rust
pub use friendship_writeback::FriendshipWritebackService;
```

`apps/bcs/crates/services/bcs-friend/src/lib.rs` — extend the core re-export:

```rust
pub use core::{FriendCore, FriendRequestCore, FriendRequestStore, FriendStore, FriendshipWritebackService};
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cargo test --package bcs-friend --test friendship_writeback 2>&1 | tail -10`
Expected: 3 passed.

- [ ] **Step 6: Run the crate's full test suite (no regressions)**

Run: `cargo test --package bcs-friend 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add apps/bcs/crates/services/bcs-friend/
git commit -m "feat(bcs): FriendshipWritebackService mirrors friendship into legacy store"
```

---

### Task 3: `DbConnectService` writeback hooks (bcs-edge-permission, TDD)

**Files:**
- Modify: `apps/bcs/crates/services/bcs-edge-permission/src/lib.rs`
  - struct fields (≈line 67-76), imports (≈line 34-39), `impl DbConnectService` (after `pub fn env`, ≈line 104-110),
  - trigger 1: `create_connect` auto-approve branch (≈line 356-411),
  - trigger 2: `approve()` idempotent branch (≈line 433-436) + tail (≈line 529-531),
  - trigger 3: `revoke_friend` (≈line 736-746),
  - tests: `#[cfg(test)] mod tests` (bottom of file).

- [ ] **Step 1: Write the failing tests (mock writeback)**

In the `mod tests` at the bottom of `apps/bcs/crates/services/bcs-edge-permission/src/lib.rs`,
add these test doubles next to the existing `RecordingEdgePermissionFriendSync`:

```rust
#[derive(Default)]
struct RecordingWriteback {
    calls: Mutex<Vec<String>>,
}

impl RecordingWriteback {
    fn calls(&self) -> Vec<String> {
        self.calls.lock().expect("calls lock").clone()
    }
}

#[async_trait]
impl bcs_service_api::port::FriendshipWritebackPort for RecordingWriteback {
    async fn add_friendship(&self, caller: &str, target: &str) -> ServiceResult<()> {
        self.calls
            .lock()
            .expect("calls lock")
            .push(format!("add:{caller}:{target}"));
        Ok(())
    }

    async fn remove_friendship(&self, caller: &str, target: &str) -> ServiceResult<bool> {
        self.calls
            .lock()
            .expect("calls lock")
            .push(format!("remove:{caller}:{target}"));
        Ok(true)
    }
}

struct FailingWriteback;

#[async_trait]
impl bcs_service_api::port::FriendshipWritebackPort for FailingWriteback {
    async fn add_friendship(&self, _caller: &str, _target: &str) -> ServiceResult<()> {
        Err(ServiceError::InternalError(
            "friendship writeback unavailable".to_string(),
        ))
    }

    async fn remove_friendship(&self, _caller: &str, _target: &str) -> ServiceResult<bool> {
        Err(ServiceError::InternalError(
            "friendship writeback unavailable".to_string(),
        ))
    }
}
```

Add this helper next to the existing `service_in_env` helper (≈line 1809):

```rust
fn service_with_writeback(
    edge_grants: &Arc<dyn EdgeGrantRepoPort>,
    profiles: &Arc<dyn PermissionProfileRepoPort>,
    requests: &Arc<dyn PermissionRequestRepoPort>,
    bot_config: &Arc<dyn BotActorConfigRepoPort>,
    writeback: Arc<bcs_service_api::port::FriendshipWritebackPort>,
) -> DbConnectService {
    service(edge_grants, profiles, requests, bot_config)
        .with_friendship_writeback(Some(writeback))
}
```

Add these tests (place near the other `create_connect`/`approve` tests;

they reuse the existing `assemble()` and `seed_bot(...)` helpers):

```rust
#[tokio::test]
async fn auto_approved_bot_bot_connect_writebacks_legacy_friendship() {
    let (eg, pp, rq, bc, db) = assemble().await;
    // OPEN strategy → needs_approval false → auto-approve inside create_connect.
    seed_bot(&db, "x:autoA", "protected", "protected", "OPEN", "online", Some("85020")).await;
    seed_bot(&db, "x:autoB", "protected", "protected", "OPEN", "online", Some("85020")).await;
    let recorder = Arc::new(RecordingWriteback::default());
    let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

    let result = svc
        .create_connect("x:autoA", "x:autoB", None, None)
        .await
        .expect("auto connect");

    assert_eq!(result.status, ConnectStatus::Approved);
    assert_eq!(
        recorder.calls(),
        vec!["add:x:autoA:x:autoB".to_string()],
        "auto-approve must mirror the Bot↔Bot pair into the legacy store"
    );
}

#[tokio::test]
async fn human_bot_connect_does_not_writeback() {
    let (eg, pp, rq, bc, db) = assemble().await;
    seed_bot(&db, "x:api", "public", "public", "OPEN", "online", Some("85020")).await;
    let recorder = Arc::new(RecordingWriteback::default());
    let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

    let result = svc
        .create_connect("human_1", "x:api", None, None)
        .await
        .expect("human connect");

    assert_eq!(result.status, ConnectStatus::Approved);
    assert!(
        recorder.calls().is_empty(),
        "Human→Bot edges are out of writeback scope"
    );
}

#[tokio::test]
async fn manual_approve_writebacks_and_idempotent_approve_backfills() {
    let (eg, pp, rq, bc, db) = assemble().await;
    seed_bot(&db, "bot_82ce3565", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
    seed_bot(&db, "20260525_tzgcxt13:195302", "protected", "protected", "APPROVAL", "online", Some("330172")).await;
    let recorder = Arc::new(RecordingWriteback::default());
    let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

    let result = svc
        .create_connect("bot_82ce3565", "20260525_tzgcxt13:195302", None, None)
        .await
        .expect("pending connect");
    assert_eq!(result.status, ConnectStatus::Pending);

    let request_id = result.request_ids.first().expect("pending request id").clone();
    svc.approve(&request_id, "human_330172", None)
        .await
        .expect("manual approve");
    assert_eq!(
        recorder.calls(),
        vec!["add:bot_82ce3565:20260525_tzgcxt13:195302".to_string()]
    );

    // Idempotent re-approve hits the already-Approved branch and backfills the
    // legacy row (covers friendships approved before this sync shipped).
    recorder.calls.lock().expect("clear").clear();
    svc.approve(&request_id, "human_330172", None)
        .await
        .expect("idempotent approve");
    assert_eq!(
        recorder.calls(),
        vec!["add:bot_82ce3565:20260525_tzgcxt13:195302".to_string()],
        "idempotent approve must backfill the legacy row"
    );
}

#[tokio::test]
async fn revoke_friend_writebacks_removal() {
    let (eg, pp, rq, bc, db) = assemble().await;
    seed_bot(&db, "x:r1", "public", "public", "OPEN", "online", Some("85020")).await;
    seed_bot(&db, "x:r2", "public", "public", "OPEN", "online", Some("85020")).await;
    let recorder = Arc::new(RecordingWriteback::default());
    let svc = service_with_writeback(&eg, &pp, &rq, &bc, recorder.clone());

    svc.create_connect("x:r1", "x:r2", None, None)
        .await
        .expect("connect");
    recorder.calls.lock().expect("clear").clear();

    svc.revoke_friend("x:r1", "x:r2", None)
        .await
        .expect("revoke");

    assert_eq!(
        recorder.calls(),
        vec!["remove:x:r1:x:r2".to_string()],
        "unfriend must drop the legacy friendship row"
    );
}

#[tokio::test]
async fn writeback_failure_is_best_effort() {
    let (eg, pp, rq, bc, db) = assemble().await;
    seed_bot(&db, "x:f1", "public", "public", "OPEN", "online", Some("85020")).await;
    seed_bot(&db, "x:f2", "public", "public", "OPEN", "online", Some("85020")).await;
    let svc = service_with_writeback(&eg, &pp, &rq, &bc, Arc::new(FailingWriteback));

    let result = svc
        .create_connect("x:f1", "x:f2", None, None)
        .await
        .expect("connect must succeed despite writeback failure");

    assert_eq!(result.status, ConnectStatus::Approved);
    assert!(eg.has_friend_edge("x:f1", "x:f2", "dev").await);
}
```

- [ ] **Step 2: Run tests to verify they fail to compile**

Run: `cargo test --package bcs-edge-permission writeback 2>&1 | tail -10`
Expected: FAIL — `with_friendship_writeback` does not exist on `DbConnectService`.

- [ ] **Step 3: Add the field, builder, and helpers to `DbConnectService`**

In `apps/bcs/crates/services/bcs-edge-permission/src/lib.rs`:

3a. Extend the `use bcs_service_api::port::{...}` import (≈line 34-38) with:

```rust
use bcs_service_api::port::FriendshipWritebackPort;
```

Add this as its own `use` line next to the existing
`use bcs_service_api::RequestAuthHeaders;` import if that reads more naturally
with file style — either import form is fine.

3b. Add the struct field (≈line 75, after `friend_auth_sync`):

```rust
    /// Edge→legacy reverse sync (coexistence window): mirrors Bot↔Bot
    /// friendship decisions into `bcs_friendships`, which the collaboration
    /// group gate (`bcs-group`) still reads. `None` = disabled (memory/local
    /// profiles, tests without a legacy store).
    friendship_writeback: Option<Arc<dyn FriendshipWritebackPort>>,
```

3c. In `DbConnectService::new` (≈line 84-97), initialize the field as `None`
(leave the constructor signature unchanged):

```rust
        Self {
            edge_grants,
            profiles,
            requests,
            bot_config,
            user_directory,
            friend_connect_notification,
            friend_auth_sync,
            env,
            friendship_writeback: None,
        }
    }
```

3d. Add builder + helpers right after `pub fn env(&self)` (≈line 104-106):

```rust
    /// Inject the edge→legacy friendship reverse-sync port.
    ///
    /// Builder style (consumes and returns self) so the existing `new`
    /// signature and all current callers stay untouched; bootstrap is the
    /// only production caller that sets it.
    pub fn with_friendship_writeback(
        mut self,
        writeback: Option<Arc<dyn FriendshipWritebackPort>>,
    ) -> Self {
        self.friendship_writeback = writeback;
        self
    }

    /// Reverse-sync a Bot↔Bot friendship into the legacy store. Best-effort:
    /// missing port, non-bot pair, and write failures are all non-fatal —
    /// `edge_grants` remains the source of truth during the coexistence window.
    async fn writeback_add_friendship(&self, caller: &str, target: &str) {
        let Some(writeback) = self.friendship_writeback.as_ref() else {
            return;
        };
        if actor_kind_of(caller) != ActorKind::Bot || actor_kind_of(target) != ActorKind::Bot {
            return;
        }
        if let Err(err) = writeback.add_friendship(caller, target).await {
            warn!(
                caller = %caller,
                target = %target,
                error = %err,
                "friendship writeback add failed (best-effort; edge grant remains source of truth)"
            );
        }
    }

    /// Reverse-sync symmetric removal; same best-effort policy as
    /// [`Self::writeback_add_friendship`].
    async fn writeback_remove_friendship(&self, caller: &str, target: &str) {
        let Some(writeback) = self.friendship_writeback.as_ref() else {
            return;
        };
        if actor_kind_of(caller) != ActorKind::Bot || actor_kind_of(target) != ActorKind::Bot {
            return;
        }
        if let Err(err) = writeback.remove_friendship(caller, target).await {
            warn!(
                caller = %caller,
                target = %target,
                error = %err,
                "friendship writeback remove failed (best-effort)"
            );
        }
    }
```

- [ ] **Step 4: Wire trigger 1 — `create_connect` auto-approve branch**

In `create_connect`'s auto-approve branch, immediately before the final
`Ok(ConnectResult { request_ids, edge_ids, status: ConnectStatus::Approved,
auto_accepted: true })` (≈line 404):

```rust
                    // Reverse-sync (coexistence): mirror the Bot↔Bot friendship
                    // into the legacy store the group gate still reads.
                    self.writeback_add_friendship(caller, to_bot).await;
```

(Note the indent: this sits inside the `else` block of `needs_approval`, at the
same depth as the `if caller_kind == ActorKind::Human` block above it.)

- [ ] **Step 5: Wire trigger 2 — `approve()`**

5a. Idempotent already-Approved branch (≈line 433-436). Change:

```rust
        // Only pending requests can be approved.
        if req.status == RequestStatus::Approved {
            // Idempotent: return existing edge ids (if any). Also backfill the
            // legacy friendship row — pairs approved before the reverse-sync
            // shipped have no bcs_friendships mirror (INSERT is idempotent).
            self.writeback_add_friendship(&req.from_id, &req.to_id).await;
            return Ok(req.edge_id.into_iter().collect());
        }
```

5b. Tail of `approve()`, immediately before `Ok(edge_ids)` (≈line 529-531):

```rust
        // Reverse-sync (coexistence): mirror the Bot↔Bot friendship into the
        // legacy store the group gate still reads.
        self.writeback_add_friendship(&caller, &to_bot).await;

        Ok(edge_ids)
```

- [ ] **Step 6: Wire trigger 3 — `revoke_friend()`**

In `revoke_friend`, immediately before `Ok(revoked)` (≈line 749):

```rust
        // Reverse-sync (coexistence): drop the legacy friendship mirror for
        // Bot↔Bot unfriends so stale rows cannot keep the group gate open.
        self.writeback_remove_friendship(caller, target).await;

        Ok(revoked)
```

- [ ] **Step 7: Run the new tests**

Run: `cargo test --package bcs-edge-permission writeback 2>&1 | tail -10`
Expected: 5 passed (`auto_approved_…`, `human_bot_…`, `manual_approve_…`,
`revoke_friend_…`, `writeback_failure_…`).

- [ ] **Step 8: Run the full crate suite (no regressions)**

Run: `cargo test --package bcs-edge-permission 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add apps/bcs/crates/services/bcs-edge-permission/src/lib.rs
git commit -m "feat(bcs): mirror edge-permission friendships into legacy store on connect/revoke"
```

---

### Task 4: Composite regression test (real writeback + connect flow)

Reproduces the production failure at service-composition level: edge connect
approval makes `are_friends` true on the legacy repo the group gate reads;
revoke makes it false again.

**Files:**
- Modify: `apps/bcs/crates/services/bcs-edge-permission/Cargo.toml` (add dev-dependency)
- Modify: `apps/bcs/crates/services/bcs-edge-permission/src/lib.rs` (test at bottom of `mod tests`)

- [ ] **Step 1: Add the dev-dependency**

In `apps/bcs/crates/services/bcs-edge-permission/Cargo.toml` `[dev-dependencies]` add:

```toml
bcs-friend = { workspace = true }
```

(Business deps stay unchanged — production code sees only the port trait;
only the test composes the real `bcs-friend` impl. No cycle: `bcs-friend` does
not depend on `bcs-edge-permission`.)

- [ ] **Step 2: Write the composite test**

In `apps/bcs/crates/services/bcs-edge-permission/src/lib.rs` `mod tests`, add:

```rust
#[tokio::test]
async fn connect_flow_keeps_legacy_friendship_in_sync() {
    // Composition mirrors the DB profile in bootstrap/server.rs: DbConnectService
    // with the real FriendshipWritebackService over a shared FriendRepoPort.
    // The plain-token bot ids reproduce the original 403 payload shape.
    let (eg, pp, rq, bc, db) = assemble().await;
    seed_bot(&db, "bot_82ce3565", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
    seed_bot(&db, "20260525_tzgcxt13:195302", "protected", "protected", "APPROVAL", "online", Some("330172")).await;

    let friend_repo: Arc<bcs_friend::MemoryFriendRepo> = Arc::new(bcs_friend::MemoryFriendRepo::new());
    let writeback = Arc::new(bcs_friend::FriendshipWritebackService::new(
        friend_repo.clone(),
        Arc::new(bcs_test_support::NoopRelationCoreService),
    ));
    let svc = service_with_writeback(&eg, &pp, &rq, &bc, writeback);

    // Before: the legacy gate sees no friendship (the original 403 state).
    assert!(!friend_repo
        .are_friends("bot_82ce3565", "20260525_tzgcxt13:195302")
        .await
        .expect("are_friends before"));

    let result = svc
        .create_connect("bot_82ce3565", "20260525_tzgcxt13:195302", None, None)
        .await
        .expect("connect");
    assert_eq!(result.status, ConnectStatus::Pending);

    let request_id = result.request_ids.first().expect("pending request id").clone();
    svc.approve(&request_id, "human_330172", None)
        .await
        .expect("approve");

    // After approve: the legacy gate (bcs-group ensure_v1_reachable reads this
    // exact repo/port) now sees the friendship → group creation passes.
    assert!(friend_repo
        .are_friends("bot_82ce3565", "20260525_tzgcxt13:195302")
        .await
        .expect("are_friends after approve"));

    // After unfriend: the legacy row is gone → gate closes again.
    svc.revoke_friend("bot_82ce3565", "20260525_tzgcxt13:195302", None)
        .await
        .expect("revoke");
    assert!(!friend_repo
        .are_friends("bot_82ce3565", "20260525_tzgcxt13:195302")
        .await
        .expect("are_friends after revoke"));
}
```

- [ ] **Step 3: Run the composite test**

Run: `cargo test --package bcs-edge-permission connect_flow_keeps_legacy 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 4: Run the full crate suite**

Run: `cargo test --package bcs-edge-permission 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/bcs/crates/services/bcs-edge-permission/
git commit -m "test(bcs): composite regression for edge connect -> legacy friendship sync"
```

---

### Task 5: Bootstrap wiring (production DB profile) + full verification

**Files:**
- Modify: `apps/bcs/crates/bootstrap/bcs/src/server.rs` (≈line 4662)

- [ ] **Step 1: Wire the writeback into `DbConnectService`**

In `apps/bcs/crates/bootstrap/bcs/src/server.rs`, locate the DB-profile block
starting `let connect_service_impl = Arc::new(bcs_edge_permission::DbConnectService::new(`
(≈line 4662). Insert before it:

```rust
        // Edge→legacy friendship reverse sync (coexistence): bots friended via
        // the edge-permission Connect flow get a mirrored bcs_friendships row
        // so the collaboration group gate (still reading the legacy store)
        // sees the relationship. Spec: 2026-10-15-edge-connect-friendship-writeback.
        let friendship_writeback: Arc<dyn bcs_service_api::port::FriendshipWritebackPort> =
            Arc::new(FriendshipWritebackService::new(
                friend_repo.clone(),
                relation_svc.clone(),
            ));
```

Then change the construction to consume the builder:

```rust
        let connect_service_impl = Arc::new(
            bcs_edge_permission::DbConnectService::new(
                edge_grant_store.clone(),
                profile_store.clone(),
                request_store.clone(),
                bot_config_store.clone(),
                user_directory.clone(),
                friend_connect_notification,
                friend_auth_sync.clone(),
                edge_permission_env,
            )
            .with_friendship_writeback(Some(friendship_writeback)),
        );
```

Note both moved values get `.clone()` where they were previously consumed by
the later `FriendCore::with_repo(friend_repo)` (≈line 4683) /
`.with_relation(relation_svc.clone())` (≈line 4684) — the writeback and
FriendCore deliberately share the SAME repo Arc so both read one store.

Also extend the existing import at the top of server.rs
(≈line 75: `use bcs_friend::{FriendCore, FriendRequestCore};`) to:

```rust
use bcs_friend::{FriendCore, FriendRequestCore, FriendshipWritebackService};
```

Caveat for the implementer: verify the `relation_svc` variable name and type
at ≈line 4684 (`.with_relation(relation_svc.clone())`) and the `friend_repo`
binding (returned by the DB-friend-store block at ≈line 4556-4562) before
editing; if their names differ (e.g. `relation_core`), use the real names —
only the wiring intent is fixed by this step. `Arc<Concrete>` coerces to
`Arc<dyn Trait>` implicitly.

- [ ] **Step 2: Verify the bootstrap crate compiles**

Run: `cargo check --package bcs 2>&1 | tail -5`
Expected: exit 0, no errors.

- [ ] **Step 3: Run the bootstrap test suite**

Run: `cargo test --package bcs 2>&1 | tail -5`
Expected: all pass (memory/local-profile tests use `NoopConnectService` and
are unaffected; compilation covers the DB-profile wiring).

- [ ] **Step 4: Run the full workspace test suite**

Run: `cargo test --workspace 2>&1 | tail -10`
Expected: all pass. If unrelated pre-existing failures appear, confirm they
existed on `dev` before this change (re-run on a clean checkout) and report —
do not fix unrelated failures in this plan's commits.

- [ ] **Step 5: Commit**

```bash
git add apps/bcs/crates/bootstrap/bcs/src/server.rs
git commit -m "feat(bcs): wire friendship writeback into DB-profile connect service"
```

---

## Self-Review (performed 2026-10-15, fixed inline)

1. **Spec coverage check** (spec §4.1–§4.5, §5):
   - §4.1 port → Task 1 ✓
   - §4.2 impl + env alignment + no edge-sync callback → Task 2 ✓
   - §4.3 three triggers + idempotent backfill + Bot↔Bot-only kind gate → Task 3 Steps 4-6 ✓
   - §4.4 best-effort failure → Task 3 (helpers) + `writeback_failure_is_best_effort` test ✓
   - §4.5 bootstrap wiring, shared repo Arc, DB profile only → Task 5 ✓
   - §5 tests: edge-permission unit tests (Task 3) ✓, bcs-friend unit tests (Task 2) ✓,
     composite regression reproducing the 403 payload shape (Task 4) ✓
   - Full HTTP-level E2E (`/collaboration/groups` returning 201) is not
     achievable in the existing integration harness because the memory profile
     boots `NoopConnectService`; the composite test (Task 4) exercises the same
     wiring composition at service level. Manual verification against the pre
     environment after deploy covers the HTTP surface.
2. **Placeholder scan:** no TBD/TODO; every code step contains complete code. ✓
3. **Type consistency:** `FriendshipWritebackPort::{add_friendship, remove_friendship}` signatures used in tasks 1/2/3/5 match; builder name `with_friendship_writeback` consistent. ✓