//! Retry behaviour of the friend-edge sync on transient duplicate-key errors.
//!
//! CDO 2026111600120678347: concurrent `create_connect` / `sync_add_friendship`
//! hit `edge_grants.uk_edge_from_to_env_kind_ref` and surfaced MySQL 1062
//! (`ERROR 23000 (1062): Duplicate entry ...`) to callers. The friend-edge
//! sync retries that transient class next to the existing lock-wait-timeout
//! (1205) retry; these tests pin the retry budget and the surfaced error.

use super::*;
use bcs_service_api::port::repo::edge_grant::FriendIdsPage;
use std::sync::atomic::{AtomicUsize, Ordering};

/// The error text the store produces for a 1062: the `edge_grants` mapping
/// wraps the `DbError::Backend` message, so both the code and the SQL text
/// reach the caller — the shape `is_duplicate_key` matches on.
const DUPLICATE_KEY_ERROR: &str = "edge_grants db insert_grant: database backend error: Server error: \
     `ERROR 23000 (1062): Duplicate entry 'human_539467-20261103_1mmh3khi:539467-prod-0' \
     for key 'edge_grants.uk_edge_from_to_env_kind_ref'`";

/// Decorates an `EdgeGrantRepoPort` and fails `insert_grant` with a duplicate
/// key error a fixed number of times before delegating to the real store.
struct DuplicateKeyTail {
    inner: Arc<dyn EdgeGrantRepoPort>,
    remaining_failures: AtomicUsize,
}

impl DuplicateKeyTail {
    fn new(inner: Arc<dyn EdgeGrantRepoPort>, failures: usize) -> Self {
        Self {
            inner,
            remaining_failures: AtomicUsize::new(failures),
        }
    }
}

#[async_trait]
impl EdgeGrantRepoPort for DuplicateKeyTail {
    async fn list_active_grants(&self, from: &str, to: &str, env: &str) -> Vec<EdgeGrant> {
        self.inner.list_active_grants(from, to, env).await
    }

    async fn is_authorized(&self, from: &str, to: &str, env: &str) -> bool {
        self.inner.is_authorized(from, to, env).await
    }

    async fn has_friend_edge(&self, x: &str, y: &str, env: &str) -> bool {
        self.inner.has_friend_edge(x, y, env).await
    }

    async fn list_friends(&self, actor: &str, env: &str) -> Vec<String> {
        self.inner.list_friends(actor, env).await
    }

    async fn list_friends_paginated(
        &self,
        actor: &str,
        env: &str,
        query: FriendListQuery,
    ) -> ServiceResult<FriendIdsPage> {
        self.inner.list_friends_paginated(actor, env, query).await
    }

    async fn insert_grant(&self, grant: EdgeGrant) -> ServiceResult<u64> {
        let failed = self
            .remaining_failures
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |n| n.checked_sub(1))
            .is_ok();
        if failed {
            return Err(ServiceError::InternalError(DUPLICATE_KEY_ERROR.to_string()));
        }
        self.inner.insert_grant(grant).await
    }

    async fn revoke_grant(&self, edge_id: u64, env: &str) -> ServiceResult<()> {
        self.inner.revoke_grant(edge_id, env).await
    }

    async fn get_default_profile_id(&self, bot_id: &str, env: &str) -> Option<u64> {
        self.inner.get_default_profile_id(bot_id, env).await
    }
}

#[tokio::test]
async fn sync_add_friendship_retries_after_duplicate_key() {
    let (eg, pp, rq, bc, db) = assemble().await;
    seed_bot(
        &db,
        "x:retry_once",
        "protected",
        "protected",
        "OPEN",
        "online",
        Some("85020"),
    )
    .await;

    let flaky: Arc<dyn EdgeGrantRepoPort> = Arc::new(DuplicateKeyTail::new(eg.clone(), 1));
    let svc = service(&flaky, &pp, &rq, &bc);

    svc.sync_add_friendship("human_1", "x:retry_once")
        .await
        .expect("one duplicate-key failure should be retried, not surfaced");

    let active = eg
        .list_active_grants("human_1", "x:retry_once", "dev")
        .await;
    assert_eq!(
        active.len(),
        1,
        "retry should still leave exactly one forward friend edge"
    );
}

#[tokio::test]
async fn sync_add_friendship_surfaces_duplicate_key_after_retry_budget() {
    let (eg, pp, rq, bc, db) = assemble().await;
    seed_bot(
        &db,
        "x:retry_exhausted",
        "protected",
        "protected",
        "OPEN",
        "online",
        Some("85020"),
    )
    .await;

    // Initial attempt + two retries all hit 1062.
    let flaky: Arc<dyn EdgeGrantRepoPort> = Arc::new(DuplicateKeyTail::new(eg.clone(), 3));
    let svc = service(&flaky, &pp, &rq, &bc);

    let err = svc
        .sync_add_friendship("human_1", "x:retry_exhausted")
        .await
        .expect_err("duplicate key should surface once the retry budget is spent");
    assert!(
        err.to_string().contains("1062"),
        "surfaced error should keep the db diagnostic, got: {err}"
    );
    assert!(
        eg.list_active_grants("human_1", "x:retry_exhausted", "dev")
            .await
            .is_empty(),
        "exhausted retries must not leave a friend edge behind"
    );
}
