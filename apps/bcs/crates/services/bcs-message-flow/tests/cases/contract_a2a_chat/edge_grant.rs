//! Real existing-call consumer tests: trusted fixtures only create edge rows.
use super::*;
use bcs_db_api::{DbPlugin, DbStatement};
use bcs_domain::edge_permission::{EdgeGrant, EdgeStatus, GrantKind, OriginatorPolicyType};
use bcs_edge_permission_store::{DbBotActorConfigStore, DbEdgeGrantStore, DbPermissionProfileStore};
use bcs_service_api::port::repo::EdgeGrantRepoPort;

fn edge(from: &str, to: &str, kind: GrantKind) -> EdgeGrant {
    EdgeGrant {
        edge_id: 0, env: "dev".into(), from_id: from.into(), to_id: to.into(),
        grant_kind: kind, grant_ref_id: 0, rules: None, status: EdgeStatus::Approved,
        originator_policy_type: OriginatorPolicyType::SameAsFrom,
        originator_policy_data: None,
    }
}

async fn fixture(visibility: &str) -> (A2aChat, Arc<RecordingDelivery>, Arc<MemoryRegistry>,
    Arc<DbEdgeGrantStore>, Arc<bcs_db_local::LocalSqliteDbPlugin>) {
    let (service, delivery, registry) = build_service(vec![
        ("bot-source", "protected", Some("owner-1")),
        ("bot-target", visibility, Some("owner-2")),
        ("bot-third", "protected", Some("owner-3")),
    ], vec![]).await;
    let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
    bcs_db_migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    for id in ["bot-source", "bot-target", "bot-third"] {
        db.execute(DbStatement::with_params(
            "INSERT INTO bcs_bots (bot_uuid, name, env, visibility, user_visibility, status) \
             VALUES (?, ?, 'dev', 'protected', 'protected', 'online')",
            vec![id.into(), id.into()],
        )).await.unwrap();
    }
    let grants = Arc::new(DbEdgeGrantStore::sqlite(db.clone()));
    let admission = Arc::new(bcs_edge_permission::DbAdmissionService::new(grants.clone(),
        Arc::new(DbBotActorConfigStore::sqlite(db.clone())),
        Arc::new(DbPermissionProfileStore::sqlite(db.clone()))));
    (service.with_edge_admission(admission, "dev".into()), delivery, registry, grants, db)
}

fn command(from: &str, to: &str, id: &str) -> A2aChatCommand {
    A2aChatCommand {
        caller: CallerContext::Bot(BotActor { bot_uuid: from.into() }),
        authenticated_staff_id: None,
        run_id: Some(id.into()), session_key: Some(format!("session-{id}")),
        ..chat_command(to)
    }
}

#[tokio::test]
async fn agent_edge_consumed_by_existing_direct_chat_without_friend_or_profile() {
    for visibility in ["protected", "private"] {
        let (service, delivery, _registry, grants, db) = fixture(visibility).await;
        grants.insert_grant(edge("bot-source", "bot-target", GrantKind::AgentInvoke)).await.unwrap();
        assert!(!grants.has_friend_edge("bot-source", "bot-target", "dev").await);
        let result = service.chat(command("bot-source", "bot-target", "edge-call")).await.unwrap();
        assert_eq!(result.status, "running");
        assert_eq!(delivery.frames().await.len(), 1, "must reach the existing physical delivery port");
        let profiles = db.query(DbStatement::new("SELECT COUNT(*) AS n FROM permission_profiles")).await.unwrap();
        assert_eq!(bcs_db_api::db_get_column::<u64>(&profiles[0], "n").unwrap(), 0);
        // Inert, committed historical schema is not a qualification dependency.
        let scopes = db.query(DbStatement::new("SELECT COUNT(*) AS n FROM agent_invoke_scopes")).await.unwrap();
        assert_eq!(bcs_db_api::db_get_column::<u64>(&scopes[0], "n").unwrap(), 0);
    }
}

#[tokio::test]
async fn agent_edge_does_not_authorize_reverse_transitive_or_revoked_calls() {
    let (service, delivery, _registry, grants, _db) = fixture("protected").await;
    let id = grants.insert_grant(edge("bot-source", "bot-target", GrantKind::AgentInvoke)).await.unwrap();
    grants.insert_grant(edge("bot-target", "bot-third", GrantKind::AgentInvoke)).await.unwrap();
    for (from, to, run) in [("bot-target", "bot-source", "reverse"),
        ("bot-source", "bot-third", "transitive")] {
        assert!(matches!(service.chat(command(from, to, run)).await, Err(ServiceError::NotFriends(_))));
    }
    grants.revoke_grant(id, "dev").await.unwrap();
    assert!(matches!(service.chat(command("bot-source", "bot-target", "revoked")).await,
        Err(ServiceError::NotFriends(_))));
    assert!(delivery.frames().await.is_empty());
}

#[tokio::test]
async fn agent_edge_does_not_replace_existing_identity_hidden_and_security_guards() {
    let (service, delivery, registry, grants, _db) = fixture("protected").await;
    grants.insert_grant(edge("bot-source", "bot-target", GrantKind::AgentInvoke)).await.unwrap();
    let mut cmd = chat_command("bot-target");
    cmd.authenticated_staff_id = Some("not-the-owner".into());
    assert!(matches!(service.chat(cmd).await, Err(ServiceError::Unauthorized(_))));
    registry.bots.write().await.get_mut("bot-target").unwrap().status = ActorStatus::Hidden;
    assert!(matches!(service.chat(command("bot-source", "bot-target", "hidden")).await,
        Err(ServiceError::BotHidden(_))));
    assert!(delivery.frames().await.is_empty());
    registry.bots.write().await.get_mut("bot-target").unwrap().status = ActorStatus::Online;
    let mut chain = InterceptorChain::new();
    chain.push_arc(Arc::new(A2aBlockingInterceptor));
    let service = service.with_interceptors(Arc::new(chain));
    assert!(service.chat(command("bot-source", "bot-target", "blocked")).await.is_err());
    assert!(delivery.frames().await.is_empty(), "edge admission must not bypass the existing outbound chain");
}

#[tokio::test]
async fn direct_call_reports_edge_read_failures_without_dispatching() {
    for corruption in ["UPDATE edge_grants SET rules = 'null'", "DROP TABLE edge_grants"] {
        let (service, delivery, _registry, grants, db) = fixture("protected").await;
        grants.insert_grant(edge("bot-source", "bot-target", GrantKind::AgentInvoke)).await.unwrap();
        db.execute(DbStatement::new(corruption)).await.unwrap();
        assert!(matches!(service.chat(command("bot-source", "bot-target", "bad-read")).await,
            Err(ServiceError::InternalError(_))));
        assert!(delivery.frames().await.is_empty());
    }
}

#[tokio::test]
async fn async_start_uses_the_same_directed_edge_admission() {
    let (service, delivery, _registry, grants, _db) = fixture("protected").await;
    grants.insert_grant(edge("bot-source", "bot-target", GrantKind::AgentInvoke)).await.unwrap();
    let accepted = service.start_async_chat(AsyncA2aChatCommand {
        caller: CallerContext::Bot(BotActor { bot_uuid: "bot-source".into() }),
        target_bot_id: "bot-target".into(), authenticated_staff_id: None,
        ..detached_async_command("async-edge")
    }).await.unwrap();
    assert_eq!(accepted.run_id, "async-edge");
    assert_eq!(delivery.frames().await.len(), 1);
}
