use std::sync::Arc;

use bcs_domain::edge_permission::{
    BotActorConfig, EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType, PermissionRequest,
    RequestKind, RequestStatus,
};
use bcs_service_api::port::repo::edge_grant::{FriendIdsPage, FriendListQuery};
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort,
    PermissionRequestRepoPort,
};

use super::*;

const ENV: &str = "dev";

fn request(id: &str, from: &str, to: &str) -> PermissionRequest {
    PermissionRequest {
        request_id: id.to_string(),
        edge_id: None,
        env: ENV.to_string(),
        from_id: from.to_string(),
        to_id: to.to_string(),
        request_kind: RequestKind::Connect,
        requested_ref_id: None,
        requested_rules: None,
        message: Some("hello".to_string()),
        status: RequestStatus::Pending,
        decision_reason: None,
        created_by: from.to_string(),
        decided_by: None,
        decided_at: None,
        created_at: 0,
        updated_at: 0,
    }
}

fn grant(
    from: &str,
    to: &str,
    grant_kind: GrantKind,
    grant_ref_id: u64,
) -> EdgeGrant {
    EdgeGrant {
        edge_id: 0,
        env: ENV.to_string(),
        from_id: from.to_string(),
        to_id: to.to_string(),
        grant_kind,
        grant_ref_id,
        rules: None,
        status: EdgeStatus::Approved,
        originator_policy_type: OriginatorPolicyType::Any,
        originator_policy_data: None,
    }
}

// ---- PermissionRequestRepoPort -------------------------------------

#[tokio::test]
async fn memory_permission_request_store_insert_get_decide() {
    let store = MemoryPermissionRequestStore::new();

    store.insert(request("r1", "a", "b")).await.unwrap();
    store.insert(request("r2", "c", "b")).await.unwrap();

    let got = store.get("r1", ENV).await.expect("inserted request");
    assert_eq!(got.status, RequestStatus::Pending);
    assert_eq!(got.request_kind, RequestKind::Connect);
    assert!(got.created_at > 0, "insert stamps gmt_create");
    assert_eq!(got.created_at, got.updated_at, "insert stamps both gmt_*");
    assert!(got.decided_at.is_none());
    assert!(store.get("r1", "other-env").await.is_none(), "env scoped");
    assert!(store.get("missing", ENV).await.is_none());

    // Inbox is recipient-scoped, newest first.
    let inbox = store.list_inbox("b", ENV, None).await.expect("request listing");
    assert_eq!(
        inbox.iter().map(|r| r.request_id.as_str()).collect::<Vec<_>>(),
        vec!["r2", "r1"],
        "inbox returns both rows, most recently updated first"
    );
    assert!(
        store.list_inbox("a", ENV, None).await.expect("request listing").is_empty(),
        "inbox is keyed on to_id"
    );

    // Sent is sender-scoped and status-filterable.
    let sent = store.list_sent("a", ENV, Some(RequestStatus::Pending)).await.expect("request listing");
    assert_eq!(sent.len(), 1);
    assert_eq!(sent[0].request_id, "r1");
    assert!(
        store
            .list_sent("a", ENV, Some(RequestStatus::Approved))
            .await.expect("request listing")
            .is_empty(),
        "status filter applies"
    );

    let before = store.get("r1", ENV).await.unwrap().updated_at;
    store
        .decide(
            "r1",
            ENV,
            RequestStatus::Approved,
            "owner",
            Some("ok"),
        )
        .await
        .unwrap();
    let decided = store.get("r1", ENV).await.unwrap();
    assert_eq!(decided.status, RequestStatus::Approved);
    assert_eq!(decided.decided_by.as_deref(), Some("owner"));
    assert_eq!(decided.decision_reason.as_deref(), Some("ok"));
    assert!(decided.decided_at.is_some(), "decide stamps decided_at");
    assert!(
        decided.updated_at > before,
        "decide advances gmt_modified ({} > {})",
        decided.updated_at,
        before
    );
    assert_eq!(decided.created_at, got.created_at, "gmt_create is stable");

    assert!(
        store
            .list_inbox("b", ENV, Some(RequestStatus::Pending))
            .await.expect("request listing")
            .iter()
            .all(|r| r.request_id == "r2"),
        "decided rows leave the pending inbox filter"
    );
    assert_eq!(
        store.list_inbox("b", ENV, None).await.expect("request listing")[0].request_id,
        "r1",
        "the just-decided row moved to the front"
    );

    store.backfill_edge_id("r1", ENV, 42).await.unwrap();
    let backfilled = store.get("r1", ENV).await.unwrap();
    assert_eq!(backfilled.edge_id, Some(42));
    assert!(backfilled.updated_at >= decided.updated_at);
}

#[tokio::test]
async fn memory_permission_request_find_request_matches_all_predicates() {
    let store = MemoryPermissionRequestStore::new();
    store.insert(request("r1", "a", "b")).await.unwrap();
    // Same pair authored by someone else (Bot↔Bot reverse-row shape)
    // must not match.
    let mut foreign = request("r2", "a", "b");
    foreign.created_by = "b".to_string();
    store.insert(foreign).await.unwrap();
    store.insert(request("r3", "a", "c")).await.unwrap();

    let found = store
        .find_request("a", "b", ENV, RequestKind::Connect, RequestStatus::Pending, "a")
        .await
        .expect("exact lookup hits");
    assert_eq!(found.request_id, "r1");
    // Positive control: the foreign-authored row is findable by ITS
    // author only (the reverse-row shape never satisfies the caller's
    // own duplicate guard).
    let foreign_found = store
        .find_request("a", "b", ENV, RequestKind::Connect, RequestStatus::Pending, "b")
        .await
        .expect("the foreign-authored row hits its own author");
    assert_eq!(foreign_found.request_id, "r2");
    assert!(
        store
            .find_request("a", "b", ENV, RequestKind::Connect, RequestStatus::Approved, "a")
            .await
            .is_none(),
        "status predicate applies"
    );
    assert!(
        store
            .find_request("a", "c", ENV, RequestKind::Connect, RequestStatus::Pending, "a")
            .await
            .is_some(),
        "other pairs stay findable"
    );
    assert!(
        store
            .find_request("a", "b", ENV, RequestKind::Connect, RequestStatus::Pending, "nobody")
            .await
            .is_none(),
        "author predicate keeps non-authors out"
    );
}

// ---- EdgeGrantRepoPort ---------------------------------------------

#[tokio::test]
async fn memory_edge_grant_store_friend_edges() {
    let profiles = Arc::new(MemoryPermissionProfileStore::new());
    let store = MemoryEdgeGrantStore::new(profiles.clone());

    let target_default = profiles.ensure_default_profile("target", ENV).await.unwrap();
    let caller_default = profiles.ensure_default_profile("caller", ENV).await.unwrap();
    let human_default = profiles.ensure_default_profile("human_1", ENV).await.unwrap();

    // D12: a friend edge is a PermissionProfile edge whose ref is the
    // peer's active default profile id.
    let forward = grant("caller", "target", GrantKind::PermissionProfile, target_default);
    let forward_id = store.insert_grant(forward.clone()).await.unwrap();
    let reverse = grant("target", "caller", GrantKind::PermissionProfile, caller_default);
    let reverse_id = store.insert_grant(reverse.clone()).await.unwrap();

    assert!(store.has_friend_edge("caller", "target", ENV).await);
    assert!(store.has_friend_edge("target", "caller", ENV).await);
    assert_eq!(store.list_friends("caller", ENV).await, vec!["target".to_string()]);
    assert_eq!(store.list_friends("target", ENV).await, vec!["caller".to_string()]);
    assert_eq!(store.list_active_grants("caller", "target", ENV).await.len(), 1);
    assert!(store.is_authorized("caller", "target", ENV).await);

    // Idempotent insert on the unique key.
    let duplicate = store.insert_grant(forward.clone()).await.unwrap();
    assert_eq!(duplicate, forward_id, "duplicate insert returns the existing id");
    assert_eq!(store.list_active_grants("caller", "target", ENV).await.len(), 1);
    assert_eq!(
        store.list_friends("caller", ENV).await,
        vec!["target".to_string()],
        "duplicate edges do not duplicate friends"
    );

    // A same-pair, wrong-ref edge is authorized but is not a friend edge.
    store
        .insert_grant(grant(
            "caller",
            "target",
            GrantKind::PermissionProfile,
            target_default + 100,
        ))
        .await
        .unwrap();
    assert_eq!(
        store.list_active_grants("caller", "target", ENV).await.len(),
        2
    );
    assert_eq!(
        store.list_friends("caller", ENV).await,
        vec!["target".to_string()],
        "wrong-ref edge is not a friend edge"
    );

    // A Rules edge authorizes without being a friendship.
    store
        .insert_grant(grant("caller", "rules-bot", GrantKind::Rules, 3))
        .await
        .unwrap();
    assert!(store.is_authorized("caller", "rules-bot", ENV).await);
    assert!(!store.has_friend_edge("caller", "rules-bot", ENV).await);

    // Paginated friend list: dedupe, actor-kind filter, byte-order sort,
    // and total across the whole filtered set.
    profiles.ensure_default_profile("human_1", ENV).await.unwrap();
    store
        .insert_grant(grant(
            "caller",
            "human_1",
            GrantKind::PermissionProfile,
            human_default,
        ))
        .await
        .unwrap();

    let all = store
        .list_friends_paginated("caller", ENV, FriendListQuery { target_type: None, offset: 0, limit: 100 })
        .await
        .unwrap();
    assert_eq!(
        all,
        FriendIdsPage {
            items: vec!["human_1".to_string(), "target".to_string()],
            total: 2,
        }
    );
    let humans = store
        .list_friends_paginated(
            "caller",
            ENV,
            FriendListQuery { target_type: Some(ActorKind::Human), offset: 0, limit: 100 },
        )
        .await
        .unwrap();
    assert_eq!(humans.items, vec!["human_1".to_string()]);
    assert_eq!(humans.total, 1);
    let bots = store
        .list_friends_paginated(
            "caller",
            ENV,
            FriendListQuery { target_type: Some(ActorKind::Bot), offset: 0, limit: 100 },
        )
        .await
        .unwrap();
    assert_eq!(bots.items, vec!["target".to_string()]);
    assert_eq!(bots.total, 1);
    let second_page = store
        .list_friends_paginated(
            "caller",
            ENV,
            FriendListQuery { target_type: None, offset: 1, limit: 1 },
        )
        .await
        .unwrap();
    assert_eq!(
        second_page,
        FriendIdsPage { items: vec!["target".to_string()], total: 2 },
        "total counts before pagination"
    );
    assert!(store
        .list_friends_paginated(
            "caller",
            ENV,
            FriendListQuery { target_type: None, offset: 0, limit: 0 },
        )
        .await
        .is_err());

    // Revoking is per edge: one direction is enough to keep a friendship
    // alive (D12 is symmetric); the wrong-ref edge cannot substitute for it.
    store.revoke_grant(forward_id, ENV).await.unwrap();
    assert!(
        store.has_friend_edge("caller", "target", ENV).await,
        "the reverse friend edge still holds the friendship"
    );
    assert_eq!(
        store.list_friends("caller", ENV).await,
        vec!["human_1".to_string(), "target".to_string()],
        "the reverse edge still lists target as a friend"
    );
    store.revoke_grant(reverse_id, ENV).await.unwrap();
    assert!(!store.has_friend_edge("caller", "target", ENV).await);
    assert_eq!(
        store.list_friends("caller", ENV).await,
        vec!["human_1".to_string()]
    );
    assert_eq!(
        store.list_active_grants("caller", "target", ENV).await.len(),
        1,
        "wrong-ref edge stays active"
    );
    assert!(store.is_authorized("caller", "target", ENV).await);
}

#[tokio::test]
async fn memory_edge_grant_reinsert_after_revoke_reactivates_the_same_edge() {
    // Parity with the SQL upsert: re-adding after a revoke revives the
    // revoked row in place (same edge id, approved again).
    let profiles = Arc::new(MemoryPermissionProfileStore::new());
    let store = MemoryEdgeGrantStore::new(profiles.clone());

    let target_default = profiles.ensure_default_profile("target", ENV).await.unwrap();
    let edge_id = store
        .insert_grant(grant("caller", "target", GrantKind::PermissionProfile, target_default))
        .await
        .unwrap();
    store.revoke_grant(edge_id, ENV).await.unwrap();
    assert!(!store.has_friend_edge("caller", "target", ENV).await);

    let again = store
        .insert_grant(grant("caller", "target", GrantKind::PermissionProfile, target_default))
        .await
        .unwrap();
    assert_eq!(again, edge_id, "the conflicting insert reuses the same in-memory row");
    assert!(store.has_friend_edge("caller", "target", ENV).await);
    let active = store.list_active_grants("caller", "target", ENV).await;
    assert_eq!(active.len(), 1);
    assert_eq!(active[0].edge_id, edge_id);
    assert_eq!(active[0].status, EdgeStatus::Approved);
}

// ---- BotActorConfigRepoPort ----------------------------------------

#[tokio::test]
async fn memory_bot_actor_config_projects_registry_bot() {
    let registry = bcs_bot::BotCore::memory();
    registry
        .register(
            "bot-private".to_string(),
            bcs_service_api::BotCapabilities {
                name: Some("Private Bot".to_string()),
                visibility: "private".to_string(),
                ..Default::default()
            },
        )
        .await
        .expect("register bot");
    let registry: Arc<dyn BotRegistryCoreService> = Arc::new(registry);

    let store = MemoryBotActorConfigStore::new(registry);
    let config = store
        .get("bot-private", ENV)
        .await
        .expect("registry bot projects to an actor config");

    assert_eq!(
        config,
        BotActorConfig {
            bot_id: "bot-private".to_string(),
            env: ENV.to_string(),
            name: "Private Bot".to_string(),
            visibility: "private".to_string(),
            status: "online".to_string(),
            created_by: None,
            user_visibility: "protected".to_string(),
            friend_check_in_strategy: "APPROVAL".to_string(),
            friend_ext: serde_json::Map::new(),
        }
    );
    assert!(store.get("missing-bot", ENV).await.is_none());
}
