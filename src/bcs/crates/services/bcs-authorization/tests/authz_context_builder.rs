use std::sync::Arc;

use bcs_authorization::AuthzContextBuilderService;
use bcs_authorization_store::MemoryAuthorizationStore;
use bcs_service_api::{
    AuthzContextBuilderCoreService, AuthzRuntimeContext, BuildA2aAuthzContextRequest,
    CallerContext, EdgeGrant, GrantKind, GrantSource, GrantStatus, PermissionProfile,
    PermissionProfileStatus, Rule, RuleEffect,
};

#[tokio::test]
async fn direct_chat_uses_current_profile_revision_from_edge_grant() {
    let store = Arc::new(MemoryAuthorizationStore::new());
    seed_profile(&store, "profile-writer", 1, false, "old").await;
    seed_profile(&store, "profile-writer", 2, false, "new").await;
    seed_profile(&store, "profile-default", 1, true, "default").await;
    seed_profile_edge(&store, "edge-1", "from-a", "to-b", "profile-writer").await;

    let context = service(store.clone())
        .build_a2a_authz_context(request(AuthzRuntimeContext::Direct))
        .await
        .expect("build authz context");

    assert_eq!(context.grants.len(), 1);
    assert_eq!(context.grants[0].kind, GrantKind::PermissionProfile);
    assert_eq!(context.grants[0].ref_id, "profile-writer");
    assert_eq!(context.grants[0].revision, 2);
    assert_eq!(context.grants[0].digest, "new");
    assert_eq!(context.grants[0].source, GrantSource::EdgeGrant);
}

#[tokio::test]
async fn direct_chat_can_use_rules_grant() {
    let store = Arc::new(MemoryAuthorizationStore::new());
    seed_rules_edge(
        &store,
        "edge-rules",
        "from-a",
        "to-b",
        "rules-ref-1",
        3,
        "rules-digest",
    )
    .await;

    let context = service(store)
        .build_a2a_authz_context(request(AuthzRuntimeContext::Direct))
        .await
        .expect("build authz context");

    assert_eq!(context.grants.len(), 1);
    assert_eq!(context.grants[0].kind, GrantKind::Rules);
    assert_eq!(context.grants[0].ref_id, "rules-ref-1");
    assert_eq!(context.grants[0].revision, 3);
    assert_eq!(context.grants[0].digest, "rules-digest");
}

#[tokio::test]
async fn public_chat_adds_target_default_without_edge() {
    let store = Arc::new(MemoryAuthorizationStore::new());
    seed_profile(&store, "profile-default", 7, true, "default-digest").await;

    let context = service(store)
        .build_a2a_authz_context(request(AuthzRuntimeContext::PublicChat))
        .await
        .expect("build authz context");

    assert_eq!(context.grants.len(), 1);
    assert_eq!(context.grants[0].ref_id, "profile-default");
    assert_eq!(context.grants[0].revision, 7);
    assert_eq!(context.grants[0].source, GrantSource::PublicDefault);
}

#[tokio::test]
async fn collaboration_adds_target_default_without_n_squared_edge() {
    let store = Arc::new(MemoryAuthorizationStore::new());
    seed_profile(&store, "profile-default", 8, true, "collab-digest").await;

    let context = service(store)
        .build_a2a_authz_context(request(AuthzRuntimeContext::Collaboration {
            group_id: Some("group-1".to_string()),
            session_id: Some("session-1".to_string()),
        }))
        .await
        .expect("build authz context");

    assert_eq!(context.grants.len(), 1);
    assert_eq!(context.grants[0].source, GrantSource::CollaborationDefault);
    assert_eq!(
        context.context,
        AuthzRuntimeContext::Collaboration {
            group_id: Some("group-1".to_string()),
            session_id: Some("session-1".to_string()),
        }
    );
}

#[tokio::test]
async fn missing_grants_fail_closed() {
    let store = Arc::new(MemoryAuthorizationStore::new());

    let error = service(store)
        .build_a2a_authz_context(request(AuthzRuntimeContext::Direct))
        .await
        .expect_err("missing grants must deny");

    assert!(error.to_string().contains("no active authz grants"));
}

fn service(store: Arc<MemoryAuthorizationStore>) -> AuthzContextBuilderService {
    AuthzContextBuilderService::new(store.clone(), store.clone(), store)
}

fn request(context: AuthzRuntimeContext) -> BuildA2aAuthzContextRequest {
    BuildA2aAuthzContextRequest {
        from_id: "from-a".to_string(),
        to_id: "to-b".to_string(),
        env: "dev".to_string(),
        caller: CallerContext::Public,
        originator: Some("from-a".to_string()),
        context,
        task_id: Some("task-1".to_string()),
        run_id: Some("run-1".to_string()),
        issued_at: 1000,
        ttl_ms: 60_000,
    }
}

async fn seed_profile(
    store: &MemoryAuthorizationStore,
    id: &str,
    revision: i64,
    is_default: bool,
    digest: &str,
) {
    bcs_service_api::PermissionProfileRepoPort::upsert_permission_profile(
        store,
        PermissionProfile {
            permission_profile_id: id.to_string(),
            bot_id: "to-b".to_string(),
            env: "dev".to_string(),
            name: if is_default { "default" } else { "writer" }.to_string(),
            description: None,
            rules_template: vec![Rule {
                tool: "chat".to_string(),
                operation: Some("send".to_string()),
                specifier: None,
                effect: RuleEffect::Allow,
                description: None,
                raw_metadata: None,
            }],
            revision,
            digest: digest.to_string(),
            is_default,
            status: PermissionProfileStatus::Active,
            created_by: "owner".to_string(),
            updated_by: None,
            created_at: revision,
            updated_at: revision,
        },
    )
    .await
    .expect("seed profile");
}

async fn seed_profile_edge(
    store: &MemoryAuthorizationStore,
    id: &str,
    from_id: &str,
    to_id: &str,
    profile_id: &str,
) {
    bcs_service_api::EdgeGrantRepoPort::insert_edge_grant(
        store,
        EdgeGrant {
            edge_id: id.to_string(),
            from_id: from_id.to_string(),
            to_id: to_id.to_string(),
            env: "dev".to_string(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: profile_id.to_string(),
            rules: None,
            rules_revision: None,
            rules_digest: None,
            status: GrantStatus::Approved,
            request_id: None,
            requested_by: from_id.to_string(),
            approved_by: Some("owner".to_string()),
            revoked_by: None,
            reason: None,
            expires_at: None,
            created_at: 1,
            updated_at: 1,
            approved_at: Some(1),
            revoked_at: None,
        },
    )
    .await
    .expect("seed edge");
}

async fn seed_rules_edge(
    store: &MemoryAuthorizationStore,
    id: &str,
    from_id: &str,
    to_id: &str,
    rules_ref: &str,
    revision: i64,
    digest: &str,
) {
    bcs_service_api::EdgeGrantRepoPort::insert_edge_grant(
        store,
        EdgeGrant {
            edge_id: id.to_string(),
            from_id: from_id.to_string(),
            to_id: to_id.to_string(),
            env: "dev".to_string(),
            grant_kind: GrantKind::Rules,
            grant_ref_id: rules_ref.to_string(),
            rules: Some(vec![Rule {
                tool: "web_fetch".to_string(),
                operation: Some("read".to_string()),
                specifier: Some("https://example.com/*".to_string()),
                effect: RuleEffect::Allow,
                description: None,
                raw_metadata: None,
            }]),
            rules_revision: Some(revision),
            rules_digest: Some(digest.to_string()),
            status: GrantStatus::Approved,
            request_id: None,
            requested_by: from_id.to_string(),
            approved_by: Some("owner".to_string()),
            revoked_by: None,
            reason: None,
            expires_at: None,
            created_at: 1,
            updated_at: 1,
            approved_at: Some(1),
            revoked_at: None,
        },
    )
    .await
    .expect("seed rules edge");
}
