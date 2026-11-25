//! Runtime-grant isolation (bot-authority plan Task 6.6, spec
//! §3.2.5 / A16): OWNER/MANAGER management edges and the
//! runtime permission dimensions (`permission_profile`,
//! `rules`) share the `edge_grants` table but are strictly
//! independent.
//!
//! Pinned at the `EdgeGrantRepoPort` surface — the ONLY access
//! the connect/friend/admission runtime has:
//! - a management edge alone grants NO friend route, NOR
//!   admission (`list_active_grants` / `is_authorized` /
//!   `list_friends` stay empty);
//! - adding a legitimate runtime profile restores the existing
//!   behavior;
//! - REVOKING the management edge afterwards does not break the
//!   runtime grants;
//! - Task 1 kind filtering stays complete: no owner re-seed
//!   re-introduces a manager onto the runtime surface either.
//!
//! SQLite (plan §6 MySQL note): the identical SQL runs against
//! MySQL in CI (`migrations/mysql/014_edge_permission.sql`
//! column set mirrored below).

#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use bcs_db_local::LocalSqliteDbPlugin;
use bcs_edge_permission_store::{DbEdgeGrantStore, DbPermissionProfileStore};
use bcs_service_api::port::repo::PermissionProfileRepoPort;
use bcs_db_api::DbPlugin;
use bcs_service_api::port::repo::edge_grant::FriendListQuery;
use bcs_service_api::port::repo::EdgeGrantRepoPort;
use serde_json::json;

const ENV: &str = "dev";
const ENV_OTHER: &str = "other-env";
const HUMAN: &str = "human_operator";
const BOT: &str = "bot_target";

struct IsolationFixture {
    db: Arc<LocalSqliteDbPlugin>,
    store: DbEdgeGrantStore,
}

async fn fixture() -> IsolationFixture {
    let db = LocalSqliteDbPlugin::new().expect("local sqlite");
    db.execute(bcs_db_api::DbStatement::new(
        "CREATE TABLE edge_grants (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            env VARCHAR(32) NOT NULL, \
            from_id VARCHAR(128) NOT NULL, \
            to_id VARCHAR(128) NOT NULL, \
            grant_kind VARCHAR(32) NOT NULL, \
            grant_ref_id INTEGER NOT NULL, \
            rules TEXT, \
            status VARCHAR(16) NOT NULL DEFAULT 'approved', \
            originator_policy_type VARCHAR(32) NOT NULL DEFAULT 'any', \
            originator_policy_data TEXT, \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            UNIQUE (from_id, to_id, env, grant_kind, grant_ref_id))",
    ))
    .await
    .expect("create edge_grants");
    db.execute(bcs_db_api::DbStatement::new(
        "CREATE TABLE permission_profiles (\
            id INTEGER PRIMARY KEY AUTOINCREMENT, \
            bot_id VARCHAR(128) NOT NULL, \
            env VARCHAR(32) NOT NULL, \
            name VARCHAR(128) NOT NULL DEFAULT 'default', \
            description VARCHAR(512), \
            rules_template TEXT NOT NULL, \
            revision INTEGER NOT NULL DEFAULT 1, \
            digest VARCHAR(128) NOT NULL, \
            is_default INTEGER NOT NULL DEFAULT 0, \
            status VARCHAR(16) NOT NULL DEFAULT 'active', \
            created_by VARCHAR(128) NOT NULL, \
            updated_by VARCHAR(128), \
            gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, \
            UNIQUE (bot_id, env, is_default, status))",
    ))
    .await
    .expect("create permission_profiles");
    IsolationFixture {
        db: Arc::new(db.clone()),
        store: DbEdgeGrantStore::sqlite(Arc::new(db)),
    }
}

async fn seed_default_profile(db: &Arc<LocalSqliteDbPlugin>) -> u64 {
    DbPermissionProfileStore::sqlite(db.clone())
        .ensure_default_profile(BOT, ENV)
        .await
        .expect("seed target default profile")
}

fn owner_edge(from: &str, env: &str) -> bcs_domain::edge_permission::EdgeGrant {
    bcs_domain::edge_permission::EdgeGrant {
        edge_id: 0,
        env: env.to_string(),
        from_id: from.to_string(),
        to_id: BOT.to_string(),
        grant_kind: bcs_domain::edge_permission::GrantKind::Owner,
        grant_ref_id: 0,
        rules: None,
        status: bcs_domain::edge_permission::EdgeStatus::Approved,
        originator_policy_type: bcs_domain::edge_permission::OriginatorPolicyType::Any,
        originator_policy_data: None,
    }
}

fn manager_edge(from: &str, env: &str) -> bcs_domain::edge_permission::EdgeGrant {
    bcs_domain::edge_permission::EdgeGrant {
        grant_kind: bcs_domain::edge_permission::GrantKind::Manager,
        ..owner_edge(from, env)
    }
}

async fn assert_no_runtime_permission(store: &DbEdgeGrantStore) {
    assert!(
        store
            .list_active_grants(HUMAN, BOT, ENV)
            .await
            .is_empty(),
        "a management edge must never surface as a runtime grant"
    );
    assert!(
        !store.is_authorized(HUMAN, BOT, ENV).await,
        "a management edge must not authorize admission"
    );
    assert!(
        !store.has_friend_edge(HUMAN, BOT, ENV).await,
        "a management edge is not a friend edge"
    );
    assert!(
        store.list_friends(HUMAN, ENV).await.is_empty(),
        "a management edge must not grant friendship"
    );
}

#[tokio::test]
async fn management_edge_alone_establishes_no_runtime_permission() {
    let f = fixture().await; let store = &f.store;
    // The owner edge exists (management dimension), the target Bot's
    // default profile exists (registration-time) — yet NO runtime
    // grant was ever created for this pair.
    store
        .insert_grant(owner_edge(HUMAN, ENV))
        .await
        .expect("owner edge");
    store
        .insert_grant(manager_edge("human_second_manager", ENV))
        .await
        .expect("manager edge");
    let _profile_id = seed_default_profile(&f.db).await;

    assert_no_runtime_permission(&store).await;
    assert!(
        store.list_friends_paginated(
            HUMAN,
            ENV,
            FriendListQuery {
                target_type: None,
                offset: 0,
                limit: 20,
            },
        )
        .await
        .expect("friend page read must not fail")
        .items
        .is_empty(),
        "management edges stay out of the friend list surface"
    );
}

#[tokio::test]
async fn a_management_edge_in_another_env_grants_nothing_here() {
    let f = fixture().await; let store = &f.store;
    store
        .insert_grant(owner_edge(HUMAN, ENV_OTHER))
        .await
        .expect("owner edge in another env");
    assert_no_runtime_permission(&store).await;
}

#[tokio::test]
async fn legitimate_runtime_profile_restores_existing_behavior() {
    let f = fixture().await; let store = &f.store;
    store
        .insert_grant(owner_edge(HUMAN, ENV))
        .await
        .expect("owner edge");
    let profile_id = seed_default_profile(&f.db).await;
    store
        .insert_grant(bcs_domain::edge_permission::EdgeGrant {
            grant_kind: bcs_domain::edge_permission::GrantKind::PermissionProfile,
            grant_ref_id: profile_id,
            ..owner_edge(HUMAN, ENV)
        })
        .await
        .expect("profiles edge");

    let listed = store.list_active_grants(HUMAN, BOT, ENV).await;
    assert_eq!(listed.len(), 1, "only the runtime edge is visible");
    assert_eq!(listed[0].grant_kind, bcs_domain::edge_permission::GrantKind::PermissionProfile);
    assert!(store.is_authorized(HUMAN, BOT, ENV).await);
    assert!(store.has_friend_edge(HUMAN, BOT, ENV).await);
    // The runtime surface never exposes the coexisting owner edge.
    assert!(
        listed
            .iter()
            .all(|grant| grant.grant_kind != bcs_domain::edge_permission::GrantKind::Owner
                && grant.grant_kind != bcs_domain::edge_permission::GrantKind::Manager)
    );
}

#[tokio::test]
async fn revoking_the_management_edge_does_not_break_runtime_grants() {
    let f = fixture().await; let store = &f.store;
    let profile_id = seed_default_profile(&f.db).await;
    let owner_edge_id = store
        .insert_grant(owner_edge(HUMAN, ENV))
        .await
        .expect("owner edge");
    let manager_edge_id = store
        .insert_grant(manager_edge(HUMAN, ENV))
        .await
        .expect("manager edge");
    let profile_edge_id = store
        .insert_grant(bcs_domain::edge_permission::EdgeGrant {
            grant_kind: bcs_domain::edge_permission::GrantKind::PermissionProfile,
            grant_ref_id: profile_id,
            ..owner_edge(HUMAN, ENV)
        })
        .await
        .expect("profiles edge");

    // Management handover / manager withdrawal (spec §3.3) revokes
    // the OWNER and MANAGER edges ONLY — the independent runtime
    // dimension stays live.
    store.revoke_grant(owner_edge_id, ENV).await.expect("revoke owner");
    store
        .revoke_grant(manager_edge_id, ENV)
        .await
        .expect("revoke manager");

    let listed = store.list_active_grants(HUMAN, BOT, ENV).await;
    assert_eq!(listed.len(), 1, "only the runtime edge survives");
    assert_eq!(listed[0].edge_id, profile_edge_id);
    assert!(store.is_authorized(HUMAN, BOT, ENV).await);
    assert_eq!(store.list_friends(HUMAN, ENV).await, vec![BOT.to_string()]);
}

#[tokio::test]
async fn rules_edges_follow_the_same_isolation() {
    let f = fixture().await; let store = &f.store;
    store
        .insert_grant(bcs_domain::edge_permission::EdgeGrant {
            grant_kind: bcs_domain::edge_permission::GrantKind::Rules,
            grant_ref_id: 0,
            rules: Some(json!([{"tool": "ls", "specifier": "*", "effect": "allow"}])),
            ..owner_edge(HUMAN, ENV)
        })
        .await
        .expect("rules edge");
    let listed = store.list_active_grants(HUMAN, BOT, ENV).await;
    assert_eq!(listed.len(), 1);
    assert_eq!(listed[0].grant_kind, bcs_domain::edge_permission::GrantKind::Rules);
    assert!(store.is_authorized(HUMAN, BOT, ENV).await);

    // The owner edge added afterwards never widens the rules edge or
    // leaks onto the runtime surface; the pair list stays
    // rules-only.
    store
        .insert_grant(manager_edge(HUMAN, ENV))
        .await
        .expect("manager edge");
    let listed = store.list_active_grants(HUMAN, BOT, ENV).await;
    assert_eq!(
        listed.len(),
        1,
        "the manager edge must not surface next to the rules edge"
    );
}
