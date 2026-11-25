//! Real SQLite regressions through existing application services. No new
//! production invocation or approval capability is mounted by this phase.
use super::*;
use bcs_domain::edge_permission::RequestKind;

fn agent_edge() -> EdgeGrant {
    EdgeGrant {
        edge_id: 0, env: "dev".into(), from_id: "agent-a".into(), to_id: "agent-b".into(),
        grant_kind: GrantKind::AgentInvoke, grant_ref_id: 0, rules: None,
        status: EdgeStatus::Approved, originator_policy_type: OriginatorPolicyType::SameAsFrom,
        originator_policy_data: None,
    }
}

#[tokio::test]
async fn agent_candidate_does_not_expand_generic_admission_or_authz_context() {
    let (eg, pp, _rq, bc, db) = assemble().await;
    seed_bot(&db, "agent-b", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
    eg.insert_grant(agent_edge()).await.unwrap();
    assert!(eg.is_authorized("agent-a", "agent-b", "dev").await);
    assert!(!eg.has_friend_edge("agent-a", "agent-b", "dev").await);
    let svc = admission_service(&eg, &bc, &pp);
    let decision = svc.check_admission("agent-a", "agent-b", "agent-a", "dev").await.unwrap();
    assert!(!decision.allowed);
    assert_eq!(decision.reason_code, AdmissionReason::NoEdge);
    let ctx = svc.build_authz_context("agent-a", "agent-b", "agent-a", "t", "r", "dev").await.unwrap();
    assert!(ctx.grants.is_empty());
    assert!(pp.get_active_default("agent-b", "dev").await.is_none(), "no implicit default seeding");
}

#[tokio::test]
async fn independent_rules_and_public_default_behavior_is_preserved() {
    let (eg, pp, _rq, bc, db) = assemble().await;
    seed_bot(&db, "agent-b", "protected", "protected", "APPROVAL", "online", Some("85020")).await;
    eg.insert_grant(agent_edge()).await.unwrap();
    eg.insert_grant(EdgeGrant {
        grant_kind: GrantKind::Rules, grant_ref_id: 23, rules: Some(serde_json::json!([])),
        ..agent_edge()
    }).await.unwrap();
    let svc = admission_service(&eg, &bc, &pp);
    assert!(svc.check_admission("agent-a", "agent-b", "o", "dev").await.unwrap().allowed);
    let ctx = svc.build_authz_context("agent-a", "agent-b", "o", "t", "r", "dev").await.unwrap();
    assert_eq!(ctx.grants.len(), 1);
    assert_eq!(ctx.grants[0].kind, GrantKind::Rules);
    assert_eq!(ctx.grants[0].ref_id, 23);

    let (eg, pp, _rq, bc, db) = assemble().await;
    seed_bot(&db, "agent-b", "public", "protected", "APPROVAL", "online", Some("85020")).await;
    eg.insert_grant(agent_edge()).await.unwrap();
    let svc = admission_service(&eg, &bc, &pp);
    let result = svc.check_admission("agent-a", "agent-b", "o", "dev").await.unwrap();
    assert!(result.public_default);
    assert_eq!(result.grants[0].source, GrantSource::PublicDefault);
    let ctx = svc.build_authz_context("agent-a", "agent-b", "o", "t", "r", "dev").await.unwrap();
    assert_eq!(ctx.grants.len(), 1);
    assert_eq!(ctx.grants[0].source, GrantSource::PublicDefault);
}

fn agent_request(status: RequestStatus) -> PermissionRequest {
    PermissionRequest {
        request_id: "agent-request".into(), edge_id: None, env: "dev".into(),
        from_id: "agent-a".into(), to_id: "agent-b".into(),
        request_kind: RequestKind::AgentInvoke, requested_ref_id: Some(0), requested_rules: None,
        message: None, status, decision_reason: None, created_by: "agent-a".into(),
        decided_by: None, decided_at: None, created_at: 0, updated_at: 0,
    }
}

#[tokio::test]
async fn friend_read_approve_reject_cancel_cannot_consume_agent_requests() {
    // Test both pending and already-approved rows: the latter used to reach
    // the idempotent writeback path before checking the request kind.
    for status in [RequestStatus::Pending, RequestStatus::Approved] {
        let (eg, pp, rq, bc, _db) = assemble().await;
        rq.insert(agent_request(status)).await.unwrap();
        let svc = service(&eg, &pp, &rq, &bc);
        assert!(matches!(svc.get_request("agent-request").await, Err(ServiceError::FriendRequestNotFound(_))));
        assert!(matches!(svc.approve("agent-request", "owner", None).await, Err(ServiceError::FriendRequestNotFound(_))));
        assert!(matches!(svc.reject("agent-request", "owner", None).await, Err(ServiceError::FriendRequestNotFound(_))));
        assert!(matches!(svc.cancel("agent-request").await, Err(ServiceError::FriendRequestNotFound(_))));
        assert_eq!(rq.get("agent-request", "dev").await.unwrap().status, status);
        assert!(eg.list_active_grants("agent-a", "agent-b", "dev").await.is_empty());
        for (actor, direction) in [
            ("agent-a", RequestDirection::Sent), ("agent-b", RequestDirection::Received),
            ("agent-a", RequestDirection::All), ("agent-b", RequestDirection::All),
        ] {
            let page = svc.list_requests(actor, direction, None, 1, 20).await.unwrap();
            assert!(page.items.is_empty());
            assert_eq!(page.total, 0);
        }
    }
}

#[tokio::test]
async fn unfriend_keeps_agent_candidate_and_legacy_friend_lifecycle_works() {
    let (eg, pp, rq, bc, db) = assemble().await;
    for bot in ["agent-a", "agent-b"] {
        seed_bot(&db, bot, "protected", "protected", "OPEN", "online", Some("85020")).await;
    }
    let id = eg.insert_grant(agent_edge()).await.unwrap();
    let svc = service(&eg, &pp, &rq, &bc);
    let result = svc.create_connect("agent-a", "agent-b", None, None).await.unwrap();
    assert_eq!(result.status, ConnectStatus::Approved);
    assert!(eg.has_friend_edge("agent-a", "agent-b", "dev").await);
    let revoked = svc.revoke_friend("agent-a", "agent-b", None).await.unwrap();
    assert!(!revoked.contains(&id));
    assert!(!eg.has_friend_edge("agent-a", "agent-b", "dev").await);
    let active = eg.list_runtime_grants_checked("agent-a", "agent-b", "dev").await.unwrap();
    assert_eq!(active.len(), 1);
    assert_eq!(active[0].edge_id, id);
    assert_eq!(active[0].grant_kind, GrantKind::AgentInvoke);
}
