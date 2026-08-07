use bcs_authorization_store::{
    AuthzDecisionLogRepoPort, CapabilityCatalogRepoPort, EdgeGrantRepoPort,
    MemoryAuthorizationStore, PermissionProfileRepoPort, PermissionRequestRepoPort,
};
use bcs_service_api::{
    AuthzContext, AuthzContextType, AuthzDecisionLog, AuthzGrantRef, AuthzRuntimeContext,
    Capability, CapabilitySource, CapabilityStatus, Decision, EdgeGrant, GrantKind, GrantSource,
    GrantStatus, PermissionProfile, PermissionProfileStatus, PermissionRequest,
    PermissionRequestKind, PermissionRequestStatus, Rule, RuleEffect,
};
use serde_json::json;

#[tokio::test]
async fn memory_authorization_store_passes_capability_catalog_repo_contract() {
    let repo = MemoryAuthorizationStore::new();
    capability_catalog_contract(&repo).await;
}

#[tokio::test]
async fn memory_authorization_store_passes_permission_profile_repo_contract() {
    let repo = MemoryAuthorizationStore::new();
    permission_profile_contract(&repo).await;
}

#[tokio::test]
async fn memory_authorization_store_passes_edge_grant_repo_contract() {
    let repo = MemoryAuthorizationStore::new();
    edge_grant_contract(&repo).await;
}

#[tokio::test]
async fn memory_authorization_store_passes_permission_request_repo_contract() {
    let repo = MemoryAuthorizationStore::new();
    permission_request_contract(&repo).await;
}

#[tokio::test]
async fn memory_authorization_store_passes_authz_decision_log_repo_contract() {
    let repo = MemoryAuthorizationStore::new();
    authz_decision_log_contract(&repo).await;
}

async fn capability_catalog_contract(repo: &dyn CapabilityCatalogRepoPort) {
    repo.upsert_capabilities(vec![
        capability("cap-active", CapabilityStatus::Active),
        capability("cap-inactive", CapabilityStatus::Inactive),
    ])
    .await
    .expect("upsert capabilities");

    let active = repo
        .list_active_capabilities("bot-a", "dev")
        .await
        .expect("list active capabilities");
    assert_eq!(active.len(), 1);
    assert_eq!(active[0].capability_id, "cap-active");
    assert_eq!(active[0].tool, "tool-x");
}

async fn permission_profile_contract(repo: &dyn PermissionProfileRepoPort) {
    repo.upsert_permission_profile(permission_profile("profile-v1", 1, false))
        .await
        .expect("upsert profile v1");
    repo.upsert_permission_profile(permission_profile("profile-v2", 2, true))
        .await
        .expect("upsert profile v2");

    let active = repo
        .list_active_permission_profiles("bot-a", "dev")
        .await
        .expect("list active profiles");
    assert_eq!(active.len(), 2);
    assert_eq!(active[0].permission_profile_id, "profile-v2");
    assert_eq!(active[0].revision, 2);

    let by_revision = repo
        .get_permission_profile_by_revision("profile-v2", 2)
        .await
        .expect("get profile by revision")
        .expect("profile exists");
    assert_eq!(by_revision.is_default, true);

    let default_profile = repo
        .load_active_default_permission_profile("bot-a", "dev")
        .await
        .expect("load active default profile")
        .expect("default profile exists");
    assert_eq!(default_profile.permission_profile_id, "profile-v2");
}

async fn edge_grant_contract(repo: &dyn EdgeGrantRepoPort) {
    repo.insert_edge_grant(edge_grant("grant-1", GrantStatus::Approved))
        .await
        .expect("insert approved grant");
    repo.insert_edge_grant(edge_grant("grant-2", GrantStatus::Pending))
        .await
        .expect("insert pending grant");

    let grants = repo
        .list_approved_active_edge_grants("from-a", "to-a", "dev")
        .await
        .expect("list approved grants");
    assert_eq!(grants.len(), 1);
    assert_eq!(grants[0].edge_id, "grant-1");
}

async fn permission_request_contract(repo: &dyn PermissionRequestRepoPort) {
    repo.insert_permission_request(permission_request(
        "request-1",
        PermissionRequestStatus::Pending,
    ))
    .await
    .expect("insert request");
    repo.insert_permission_request(permission_request(
        "request-2",
        PermissionRequestStatus::Approved,
    ))
    .await
    .expect("insert second request");

    let pending = repo
        .list_permission_requests_by_to_id("to-a", Some(PermissionRequestStatus::Pending))
        .await
        .expect("list pending requests");
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].request_id, "request-1");

    repo.update_permission_request_status("request-1", PermissionRequestStatus::Rejected)
        .await
        .expect("update request status");

    let updated = repo
        .get_permission_request("request-1")
        .await
        .expect("get request")
        .expect("request exists");
    assert_eq!(updated.status, PermissionRequestStatus::Rejected);
}

async fn authz_decision_log_contract(repo: &dyn AuthzDecisionLogRepoPort) {
    let context = authz_context();
    repo.append_authz_decision_log(authz_log("decision-1", 10, &context, Decision::Allow))
        .await
        .expect("append matching log");
    repo.append_authz_decision_log(authz_log("decision-2", 20, &context, Decision::Deny))
        .await
        .expect("append second matching log");

    let logs = repo
        .list_recent_authz_decision_logs(&context)
        .await
        .expect("list recent logs");
    assert_eq!(logs.len(), 2);
    assert_eq!(logs[0].decision_id, "decision-2");
    assert_eq!(logs[1].decision_id, "decision-1");
}

fn capability(capability_id: &str, status: CapabilityStatus) -> Capability {
    Capability {
        capability_id: capability_id.to_string(),
        bot_id: "bot-a".to_string(),
        env: "dev".to_string(),
        tool: "tool-x".to_string(),
        operation: Some("operate".to_string()),
        specifier_schema: Some("schema".to_string()),
        description: Some("description".to_string()),
        source: CapabilitySource::Manual,
        status,
        raw_metadata: Some(json!({"source": "test"})),
        created_at: 1,
        updated_at: 2,
    }
}

fn permission_profile(
    permission_profile_id: &str,
    revision: i64,
    is_default: bool,
) -> PermissionProfile {
    PermissionProfile {
        permission_profile_id: permission_profile_id.to_string(),
        bot_id: "bot-a".to_string(),
        env: "dev".to_string(),
        name: format!("profile-{revision}"),
        description: Some("profile description".to_string()),
        rules_template: vec![Rule {
            tool: "tool-x".to_string(),
            operation: Some("operate".to_string()),
            specifier: None,
            effect: RuleEffect::Allow,
            description: Some("allow tool".to_string()),
            raw_metadata: None,
        }],
        revision,
        digest: format!("digest-{revision}"),
        is_default,
        status: PermissionProfileStatus::Active,
        created_by: "owner-a".to_string(),
        updated_by: Some("owner-b".to_string()),
        created_at: 100 + revision,
        updated_at: 200 + revision,
    }
}

fn edge_grant(edge_id: &str, status: GrantStatus) -> EdgeGrant {
    EdgeGrant {
        edge_id: edge_id.to_string(),
        from_id: "from-a".to_string(),
        to_id: "to-a".to_string(),
        env: "dev".to_string(),
        grant_kind: GrantKind::PermissionProfile,
        grant_ref_id: "profile-v2".to_string(),
        rules: None,
        rules_revision: None,
        rules_digest: None,
        status,
        request_id: Some("request-1".to_string()),
        requested_by: "requester-a".to_string(),
        approved_by: Some("approver-a".to_string()),
        revoked_by: None,
        reason: Some("reason".to_string()),
        expires_at: None,
        created_at: if edge_id == "grant-1" { 10 } else { 20 },
        updated_at: if edge_id == "grant-1" { 11 } else { 21 },
        approved_at: Some(12),
        revoked_at: None,
    }
}

fn permission_request(request_id: &str, status: PermissionRequestStatus) -> PermissionRequest {
    PermissionRequest {
        request_id: request_id.to_string(),
        env: "dev".to_string(),
        from_id: "from-a".to_string(),
        to_id: "to-a".to_string(),
        request_kind: PermissionRequestKind::Connect,
        requested_ref_id: Some("profile-v2".to_string()),
        requested_rules: None,
        message: Some("please allow".to_string()),
        status,
        decision_reason: None,
        created_by: "requester-a".to_string(),
        decided_by: None,
        created_at: if request_id == "request-1" { 10 } else { 20 },
        updated_at: if request_id == "request-1" { 11 } else { 21 },
    }
}

fn authz_context() -> AuthzContext {
    AuthzContext {
        task_id: Some("task-a".to_string()),
        run_id: Some("run-a".to_string()),
        from_id: "from-a".to_string(),
        to_id: "to-a".to_string(),
        env: "dev".to_string(),
        originator: Some("originator-a".to_string()),
        context: AuthzRuntimeContext::Collaboration {
            group_id: Some("group-a".to_string()),
            session_id: Some("session-a".to_string()),
        },
        grants: vec![AuthzGrantRef {
            kind: GrantKind::PermissionProfile,
            ref_id: "profile-v2".to_string(),
            revision: 2,
            digest: "digest-2".to_string(),
            source: GrantSource::EdgeGrant,
        }],
        issued_at: 5,
        expires_at: 30,
        signature: None,
    }
}

fn authz_log(
    decision_id: &str,
    created_at: i64,
    context: &AuthzContext,
    decision: Decision,
) -> AuthzDecisionLog {
    AuthzDecisionLog {
        decision_id: decision_id.to_string(),
        env: context.env.clone(),
        task_id: context.task_id.clone(),
        run_id: context.run_id.clone(),
        from_id: context.from_id.clone(),
        to_id: context.to_id.clone(),
        originator: context.originator.clone(),
        context_type: AuthzContextType::Collaboration,
        decision,
        reason_code: "rule-allow".to_string(),
        grant_refs: context.grants.clone(),
        context_json: Some(json!({"task_id": context.task_id.clone()})),
        created_at,
    }
}
