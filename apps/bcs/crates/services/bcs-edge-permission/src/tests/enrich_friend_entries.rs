use super::*;

use bcs_bot::BotCore;
use bcs_domain::actor::{ActorKind, ActorStatus};
use bcs_domain::edge_permission::{EdgeGrant, GrantKind, OriginatorPolicyType, EdgeStatus};
use bcs_domain::registry::BotCapabilities;
use bcs_service_api::application::connect::{ConnectService, FriendEntriesPage, FriendListQuery};
use bcs_service_api::port::repo::{
    BotActorConfigRepoPort, EdgeGrantRepoPort, PermissionProfileRepoPort, PermissionRequestRepoPort,
};
use bcs_service_api::BotRegistryCoreService;
use bcs_user_directory_api::UserDirectoryPlugin;

/// Build a `DbConnectService` with a real in-memory registry and an
/// optional user-directory plugin wired in.
fn service_with_enrichment(
    edge_grants: &Arc<dyn EdgeGrantRepoPort>,
    profiles: &Arc<dyn PermissionProfileRepoPort>,
    requests: &Arc<dyn PermissionRequestRepoPort>,
    bot_config: &Arc<dyn BotActorConfigRepoPort>,
    registry: Arc<dyn BotRegistryCoreService>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
) -> DbConnectService {
    DbConnectService::new(
        edge_grants.clone(),
        profiles.clone(),
        requests.clone(),
        bot_config.clone(),
        user_directory,
        Arc::new(bcs_service_api::NoopFriendConnectNotificationPort),
        Arc::new(bcs_service_api::NoopFriendAuthSyncPort),
        "dev".to_string(),
    )
    .with_collaboration_registry(registry)
}

/// Seed a bot into the DB (via the existing `seed_bot` helper) AND register
/// its capabilities in the in-memory registry for enrichment.
async fn seed_bot_for_enrich(
    db: &Arc<LocalSqliteDbPlugin>,
    id: &str,
    name: &str,
    summary: &str,
    registry: &BotCore,
) {
    // Use the project's own seed helper for complete bot-actor config.
    seed_bot(db, id, "protected", "protected", "OPEN", "online", Some("85020")).await;
    // Register capabilities in the registry for enrichment lookups.
    registry
        .register(
            id.to_string(),
            BotCapabilities {
                name: Some(name.to_string()),
                summary: Some(summary.to_string()),
                ..Default::default()
            },
        )
        .await
        .unwrap();
}

// ── Bot enrichment ─────────────────────────────────────────────────

#[tokio::test]
async fn list_friends_enriches_bot_name_and_summary() {
    let (eg, pp, rq, bc, db) = assemble().await;
    let registry = BotCore::memory();
    let registry_arc = Arc::new(registry);
    seed_bot_for_enrich(&db, "x:bot1", "Bot One", "does stuff", &registry_arc).await;
    seed_bot_for_enrich(&db, "x:bot2", "Bot Two", "helper", &registry_arc).await;
    let svc = service_with_enrichment(&eg, &pp, &rq, &bc, registry_arc.clone(), None);
    svc.create_connect("human_1", "x:bot1", None, None).await.expect("c1");
    svc.create_connect("human_1", "x:bot2", None, None).await.expect("c2");

    let friends = svc.list_friends("human_1").await.expect("list ok");
    assert_eq!(friends.len(), 2);

    let b1 = friends.iter().find(|f| f.actor_id == "x:bot1").unwrap();
    assert_eq!(b1.name.as_deref(), Some("Bot One"));
    assert_eq!(b1.summary.as_deref(), Some("does stuff"));
    assert!(!b1.is_online);
    assert_eq!(b1.kind, ActorKind::Bot);

    let b2 = friends.iter().find(|f| f.actor_id == "x:bot2").unwrap();
    assert_eq!(b2.name.as_deref(), Some("Bot Two"));
    assert_eq!(b2.summary.as_deref(), Some("helper"));
}

#[tokio::test]
async fn list_friends_paginated_enriches_bot_name_and_online() {
    let (eg, pp, rq, bc, db) = assemble().await;
    let registry = BotCore::memory();
    let registry_arc = Arc::new(registry);
    seed_bot_for_enrich(&db, "x:botA", "Alpha", "first", &registry_arc).await;
    seed_bot_for_enrich(&db, "x:botB", "Beta", "second", &registry_arc).await;
    // Make botA online.
    registry_arc.register_streaming_connection("x:botA".into()).await.unwrap();
    registry_arc.update_actor_status("x:botA", ActorStatus::Online).await.unwrap();
    let svc = service_with_enrichment(&eg, &pp, &rq, &bc, registry_arc.clone(), None);
    svc.create_connect("human_2", "x:botA", None, None).await.expect("c1");
    svc.create_connect("human_2", "x:botB", None, None).await.expect("c2");

    let page: FriendEntriesPage = svc
        .list_friends_paginated("human_2", FriendListQuery {
            target_type: None, offset: 0, limit: 10,
        })
        .await
        .expect("list ok");
    assert_eq!(page.total, 2);

    let a = page.items.iter().find(|f| f.actor_id == "x:botA").unwrap();
    assert_eq!(a.name.as_deref(), Some("Alpha"));
    assert!(a.is_online);
    assert_eq!(a.kind, ActorKind::Bot);

    let b = page.items.iter().find(|f| f.actor_id == "x:botB").unwrap();
    assert_eq!(b.name.as_deref(), Some("Beta"));
    assert!(!b.is_online);
    assert_eq!(b.kind, ActorKind::Bot);
}

#[tokio::test]
async fn list_friends_graceful_when_registry_missing() {
    let (eg, pp, rq, bc, db) = assemble().await;
    seed_bot(&db, "x:r0", "protected", "protected", "OPEN", "online", Some("85020")).await;
    seed_bot(&db, "x:r1", "protected", "protected", "OPEN", "online", Some("85020")).await;
    // No registry injected via `service()` → enrichment skipped.
    let svc = service(&eg, &pp, &rq, &bc);
    svc.create_connect("human_1", "x:r0", None, None).await.expect("c1");
    svc.create_connect("human_1", "x:r1", None, None).await.expect("c2");

    let friends = svc.list_friends("human_1").await.expect("list ok");
    assert_eq!(friends.len(), 2);
    for f in &friends {
        assert_eq!(f.kind, ActorKind::Bot);
        assert!(f.name.is_none(), "name stays None without registry");
        assert!(!f.is_online, "is_online stays false without registry");
    }
}

// ── Human enrichment ───────────────────────────────────────────────

#[tokio::test]
async fn list_friends_enriches_human_name_via_user_directory() {
    let (eg, pp, rq, bc, db) = assemble().await;
    let registry = Arc::new(BotCore::memory());
    seed_bot_for_enrich(&db, "x:hf1", "FriendBot", "helper", &registry).await;
    let user_dir = Arc::new(FixedNickUserDirectoryPlugin { nick: "Alice".into() });
    let svc = service_with_enrichment(&eg, &pp, &rq, &bc, registry, Some(user_dir));
    svc.create_connect("human_88001", "x:hf1", None, None).await.expect("c1");

    // List from the bot's perspective → it should see the human friend.
    let friends = svc.list_friends("x:hf1").await.expect("list ok");
    assert_eq!(friends.len(), 1);
    let human = &friends[0];
    assert_eq!(human.actor_id, "human_88001");
    assert_eq!(human.kind, ActorKind::Human);
    assert_eq!(human.name.as_deref(), Some("Alice"));
}

#[tokio::test]
async fn list_friends_human_name_none_when_directory_absent() {
    let (eg, pp, rq, bc, db) = assemble().await;
    let registry = Arc::new(BotCore::memory());
    seed_bot_for_enrich(&db, "x:hn1", "FriendBot", "helper", &registry).await;
    let svc = service_with_enrichment(&eg, &pp, &rq, &bc, registry, None);
    svc.create_connect("human_99001", "x:hn1", None, None).await.expect("c1");

    let friends = svc.list_friends("x:hn1").await.expect("list ok");
    assert_eq!(friends.len(), 1);
    let human = &friends[0];
    assert_eq!(human.actor_id, "human_99001");
    assert_eq!(human.kind, ActorKind::Human);
    assert!(human.name.is_none(), "name stays None without user_directory");
}

#[tokio::test]
async fn list_friends_human_name_none_when_profile_absent() {
    let (eg, pp, rq, bc, db) = assemble().await;
    let registry = Arc::new(BotCore::memory());
    seed_bot_for_enrich(&db, "x:np1", "FriendBot", "helper", &registry).await;
    // StaticUserDirectoryPlugin returns nick_name=None for every lookup.
    let user_dir = Arc::new(StaticUserDirectoryPlugin::default());
    let svc = service_with_enrichment(&eg, &pp, &rq, &bc, registry, Some(user_dir));
    svc.create_connect("human_77001", "x:np1", None, None).await.expect("c1");

    let friends = svc.list_friends("x:np1").await.expect("list ok");
    assert_eq!(friends.len(), 1);
    let human = &friends[0];
    assert_eq!(human.actor_id, "human_77001");
    assert_eq!(human.kind, ActorKind::Human);
    assert!(human.name.is_none(), "name stays None when nick_name absent");
}

#[tokio::test]
async fn list_friends_human_name_none_when_directory_error() {
    let (eg, pp, rq, bc, db) = assemble().await;
    let registry = Arc::new(BotCore::memory());
    seed_bot_for_enrich(&db, "x:err1", "FriendBot", "helper", &registry).await;
    // FailingUserDirectoryPlugin: every lookup returns an error.
    let user_dir = Arc::new(FailingUserLookupPlugin);
    let svc = service_with_enrichment(&eg, &pp, &rq, &bc, registry, Some(user_dir));
    svc.create_connect("human_66001", "x:err1", None, None).await.expect("c1");

    let friends = svc.list_friends("x:err1").await.expect("list ok");
    assert_eq!(friends.len(), 1);
    let human = &friends[0];
    assert_eq!(human.actor_id, "human_66001");
    assert_eq!(human.kind, ActorKind::Human);
    assert!(human.name.is_none(), "name stays None when directory lookup errors");
}

// ── Pagination semantics preserved ─────────────────────────────────

#[tokio::test]
async fn list_friends_paginated_preserves_total_after_enrichment() {
    let (eg, pp, rq, bc, db) = assemble().await;
    let registry = Arc::new(BotCore::memory());
    for i in 0..5 {
        let id = format!("x:pg{i}");
        seed_bot_for_enrich(&db, &id, &format!("PageBot{i}"), "paged", &registry).await;
        let profile = pp
            .ensure_default_profile(&id, "dev")
            .await
            .unwrap();
        eg.insert_grant(EdgeGrant {
            edge_id: 0,
            env: "dev".into(),
            from_id: "human_pager".into(),
            to_id: id,
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: profile,
            rules: None,
            status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        })
        .await
        .unwrap();
    }
    let svc = service_with_enrichment(&eg, &pp, &rq, &bc, registry, None);

    // First page.
    let page = svc
        .list_friends_paginated("human_pager", FriendListQuery {
            target_type: None, offset: 0, limit: 3,
        })
        .await
        .expect("list ok");
    assert_eq!(page.total, 5, "total reflects all edges");
    assert_eq!(page.items.len(), 3, "page capped by limit");
    for entry in &page.items {
        assert!(entry.name.is_some(), "name enriched on page 1: {entry:?}");
    }

    // Second page.
    let page2 = svc
        .list_friends_paginated("human_pager", FriendListQuery {
            target_type: None, offset: 3, limit: 3,
        })
        .await
        .expect("list ok");
    assert_eq!(page2.total, 5);
    assert_eq!(page2.items.len(), 2, "remaining entries on page 2");
    for entry in &page2.items {
        assert!(entry.name.is_some(), "name enriched on page 2: {entry:?}");
    }
}
