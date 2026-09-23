//! Passive HTTP acceptance: no server tasks, external Bots, or workflow reads.
use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};
use async_trait::async_trait;
use axum::{body::{Body, to_bytes}, http::{HeaderMap, Request, StatusCode, Uri}, Router};
use bcs_api_http::{ApiState, PrincipalVerifier, PrincipalVerificationError};
use bcs_app_session::{SessionServiceImpl, SessionServiceConfig};
use bcs_auth_api::{AuthError, UserIdentityInfo};
use bcs_domain::MessageViewScope;
use bcs_db_api::{DbPlugin, DbStatement, DbResult, DbRow, DbExecuteResult, DbTransactionStep, DbTransactionStepResult, DbHealth};
use bcs_db_local::LocalSqliteDbPlugin;
use bcs_http::state::{HttpAppState, HttpUserIdentity, UserIdentityPort};
use bcs_service_api::*;
use bcs_service_api::application::v1::{AuthenticatedCaller, AuthenticatedUserIdentity};
use bcs_service_api::port::repo::{MessageRepoPort, SessionRepoPort, NewSessionParams};
use bcs_services_container::Services;
use serde_json::{Value, json};
use tower::ServiceExt;

struct ReadOnlyHistoryDb { inner: Arc<dyn DbPlugin>, reads: AtomicUsize, allow_workflow: bool }
#[async_trait]
impl DbPlugin for ReadOnlyHistoryDb {
    async fn query(&self, statement: DbStatement) -> DbResult<Vec<DbRow>> {
        let sql = statement.sql().to_ascii_lowercase();
        assert!(self.allow_workflow || (!sql.contains("bcs_state_machine") && !sql.contains("bcs_collaboration")), "history accessed workflow storage");
        self.reads.fetch_add(1, Ordering::SeqCst);
        self.inner.query(statement).await
    }
    async fn execute(&self, _: DbStatement) -> DbResult<DbExecuteResult> { panic!("history wrote data") }
    async fn transaction(&self, _: Vec<DbTransactionStep>) -> DbResult<Vec<DbTransactionStepResult>> { panic!("history opened a write transaction") }
    async fn health_check(&self) -> DbResult<DbHealth> { self.inner.health_check().await }
}
struct UnusedPorts;
#[async_trait]
impl JudgeEvaluatorPort for UnusedPorts {
    async fn judge(&self, _: JudgeRequest) -> Result<JudgeDecision, ServiceError> { panic!("history invoked a judge") }
}
#[async_trait]
impl PendingGroupMessagePort for UnusedPorts {
    async fn list_pending(&self, _: &str, _: Option<&str>) -> Vec<PendingGroupMessage> { Vec::new() }
}
#[async_trait]
impl GroupMessageHistoryService for UnusedPorts {
    async fn get_session_history(&self, _: SessionHistoryCommand) -> Result<SessionHistoryResult, GroupUseCaseError> { panic!("history accessed native Bot history") }
    async fn get_history(&self, _: GroupHistoryCommand) -> Result<GroupHistoryResult, GroupUseCaseError> { panic!("history accessed native Bot history") }
}
struct Identity(String);
#[async_trait]
impl UserIdentityPort for Identity {
    async fn extract(&self, _: &HeaderMap, _: &Uri) -> Option<HttpUserIdentity> {
        Some(HttpUserIdentity { staff_no: Some(self.0.clone()), nick_name: Some("History acceptance".into()) })
    }
    async fn ensure_identity(&self, _: &str, _: &str, _: Option<&str>, _: Option<&str>, _: &str) -> Result<String, AuthError> { panic!("history created identity") }
    async fn get_identity_by_token(&self, _: &str) -> Result<Option<UserIdentityInfo>, AuthError> { Ok(None) }
    async fn get_identity_by_user_id(&self, _: &str) -> Result<Option<UserIdentityInfo>, AuthError> { Ok(None) }
}
#[async_trait]
impl PrincipalVerifier for Identity {
    async fn verify(&self, _: &HeaderMap) -> Result<bcs_api_http::VerifiedRequestIdentity, PrincipalVerificationError> {
        Ok(bcs_api_http::VerifiedRequestIdentity {
            caller: AuthenticatedCaller { tenant: None, user: Some(AuthenticatedUserIdentity {
                id: self.0.clone(), username: self.0.clone(), display_name: None, full_name: None,
            }), bot: None, app: None, access_key: None },
            authentication_context: bcs_api_http::AuthenticationContext {
                source: "test".to_string(),
                credential_kind: bcs_api_http::CredentialKind::GatewayPrincipalHeader,
            },
            display: Default::default(),
        })
    }
}

fn routers(db: Arc<ReadOnlyHistoryDb>, env: &str, user: &str, cutoff: u64) -> [Router; 2] {
    let mut services = Services::noop();
    let group_repo = Arc::new(bcs_group_store::MySqlGroupStore::sqlite(db.clone(), env.into()));
    let session_repo = Arc::new(bcs_session_store::MySqlSessionStore::sqlite(db.clone(), env.into()));
    services.group = Arc::new(bcs_group::GroupCore::with_repo(group_repo.clone()));
    services.session_management = Arc::new(bcs_session::SessionManagementServiceImpl::new(session_repo.clone(), group_repo));
    let messages = Arc::new(bcs_message_store::MySqlMessageStore::sqlite(db.clone(), env.into()));
    let workflow = Arc::new(bcs_collaboration_store::MySqlCollaborationStore::sqlite(db, env.into()));
    services.collaboration_runtime = Arc::new(bcs_collaboration_runtime::CollaborationRuntime::new(
        workflow.clone(), workflow.clone(), workflow.clone(), workflow,
        services.group.clone(), services.session_management.clone(), services.bot_delivery.clone(), Arc::new(UnusedPorts),
    ).with_message_repo(messages.clone()).with_history_persistence(true).with_history_cutoff_timestamp(cutoff));
    services.group_message_history = Arc::new(bcs_message::MessageService::new(
        messages, Arc::new(UnusedPorts), session_repo.clone(), services.group.clone(), services.registry.clone(),
        services.session_files.clone(), Arc::new(UnusedPorts), 0, 0, 100, 50, 100, 600,
    ).with_persisted_state_machine_history(true, cutoff));
    let facade = Arc::new(SessionServiceImpl::new(services.session_launch.clone(), services.session_management.clone(),
        services.group.clone(), services.registry.clone(), services.friend.clone(), services.relation.clone(), session_repo,
        services.group_message_history.clone(), services.collaboration_runtime.clone(), services.system_message.clone(),
        SessionServiceConfig { relation_env: env.into() }));
    let group = Arc::new(bcs_app_group::GroupServiceImpl::new(services.group.clone(), services.registry.clone(),
        services.friend.clone(), services.relation.clone(), services.session_management.clone(), services.group_management.clone(),
        bcs_app_group::GroupServiceConfig { relation_env: env.into() }));
    let invite = Arc::new(bcs_group::application::invite::InviteServiceImpl {
        registry: services.registry.clone(), group: services.group.clone(), session: services.session_management.clone(),
        system_message: services.system_message.clone(), token_secret: Vec::new(), default_ttl_seconds: 60,
        base_url: None, group_link_url: None, session_link_url: None,
    });
    let invitations = Arc::new(bcs_app_invitation::InvitationFriendshipServiceImpl::new(services.friend.clone(),
        Arc::new(bcs_test_support::NoopFriendRequestCoreService), services.group.clone(), services.session_management.clone(),
        services.registry.clone(), invite, Vec::new(), bcs_app_invitation::InvitationFriendshipServiceConfig { default_ttl_seconds: 60 }));
    let register = Arc::new(bcs_app_register::RegisterServiceImpl::new(services.bot_management.clone(), services.bot_onboarding.clone(), Vec::new()));
    let identity = Arc::new(Identity(user.into()));
    let api = bcs_api_http::router(ApiState::new(group, facade.clone(), facade, invitations.clone(), register, invitations, identity.clone()));
    let legacy = bcs_http::router::build_router(HttpAppState::new(services).with_user_identity(identity));
    [legacy, api]
}

async fn page(app: &Router, prefix: &str, sid: &str, query: &str) -> (StatusCode, Value) {
    let response = app.clone().oneshot(Request::builder().uri(format!("{prefix}/sessions/{sid}/messages?{query}"))
        .body(Body::empty()).unwrap()).await.unwrap();
    let status = response.status();
    let body: Value = serde_json::from_slice(&to_bytes(response.into_body(), 8 * 1024 * 1024).await.unwrap()).unwrap();
    (status, body)
}
async fn verify(db: Arc<dyn DbPlugin>, env: &str, user: &str, sessions: &[String], cutoff: u64) {
    let guarded = Arc::new(ReadOnlyHistoryDb { inner: db, reads: AtomicUsize::new(0), allow_workflow: false });
    let apps = routers(guarded.clone(), env, user, cutoff);
    let prefixes = ["", "/openapi/v1/collaboration"];
    for sid in sessions {
        let mut reference = None;
        for (app, prefix) in apps.iter().zip(prefixes) {
            guarded.reads.store(0, Ordering::SeqCst);
            let (status, body) = page(app, prefix, sid, "limit=100").await;
            assert_eq!(status, StatusCode::OK, "route failed: {prefix}");
            let rows = if prefix.is_empty() { &body } else { &body["data"] }.as_array().expect("message page");
            let ids = rows.iter().map(|row| row["id"].as_str().unwrap()).collect::<std::collections::BTreeSet<_>>();
            assert_eq!(ids.len(), rows.len(), "duplicate public IDs");
            assert!(rows.windows(2).all(|pair| pair[0]["timestamp"].as_u64() >= pair[1]["timestamp"].as_u64()));
            assert!(rows.iter().filter(|row| row["metadata"].get("state_machine").is_some()).all(|row| row["run_id"].as_str().unwrap_or("").is_empty()));
            if let Some(expected) = reference.as_ref() { assert_eq!(rows, expected, "HTTP projections differ"); }
            else { reference = Some(rows.clone()); }
            assert!(guarded.reads.load(Ordering::SeqCst) <= 8, "history query count must stay bounded");
            println!("session={sid} route={prefix:?} messages={} sql_reads={}", rows.len(), guarded.reads.load(Ordering::SeqCst));
            let (status, first) = page(app, prefix, sid, "limit=2").await;
            assert_eq!(status, StatusCode::OK);
            let first_rows = if prefix.is_empty() { &first } else { &first["data"] }.as_array().unwrap();
            assert_eq!(first_rows, &rows[..rows.len().min(2)]);
            if let Some(cursor) = first_rows.last().and_then(|row| row["timestamp"].as_u64()) {
                let (status, older) = page(app, prefix, sid, &format!("limit=100&before={cursor}")).await;
                assert_eq!(status, StatusCode::OK);
                let older_rows = if prefix.is_empty() { &older } else { &older["data"] }.as_array().unwrap();
                assert!(older_rows.iter().all(|row| row["timestamp"].as_u64().unwrap() < cursor));
            }
        }
    }
    let outsider = routers(guarded, env, "history-outsider", cutoff);
    for (app, prefix) in outsider.iter().zip(prefixes) {
        let (status, _) = page(app, prefix, &sessions[0], "limit=100").await;
        assert_eq!(status, StatusCode::FORBIDDEN);
    }
}

#[tokio::test]
async fn both_http_apis_read_only_messages_for_full_and_participant_views() {
    use bcs_domain::{MessageAudience, MessageVisibilityDomain, NewMessage, SenderType};
    let db = Arc::new(LocalSqliteDbPlugin::new().unwrap());
    bcs::migrations::run_sqlite_migrations(db.as_ref()).await.unwrap();
    let group_repo = Arc::new(bcs_group_store::MySqlGroupStore::sqlite(db.clone(), "local".into()));
    let groups = bcs_group::GroupCore::with_repo(group_repo);
    let sessions = bcs_session_store::MySqlSessionStore::sqlite(db.clone(), "local".into());
    let messages = bcs_message_store::MySqlMessageStore::sqlite(db.clone(), "local".into());
    let mut ids = Vec::new();
    for (name, strategy, scope) in [("pure", GroupStrategy::StateMachine, MessageViewScope::Full),
        ("mixed-full", GroupStrategy::ManagerWorker, MessageViewScope::Full),
        ("mixed-participant", GroupStrategy::ManagerWorker, MessageViewScope::Participant)] {
        let mut human = Participant::human("human_1", ParticipantRole::Observer);
        human.message_view_scope = scope;
        let mut group = Group::new(name, "human_1", vec![human.clone()]);
        group.group_strategy = strategy;
        groups.upsert(group).await.unwrap();
        let sid = format!("{name}:abcdef12");
        sessions.create(name, NewSessionParams { id: Some(sid.clone()), participants: vec![human], ..Default::default() }).await.unwrap();
        for (index, kind, audience) in [(0, "state_machine_panel", MessageAudience::Public),
            (1, "state_machine_output", MessageAudience::FullOnly),
            (2, "state_machine_human_input_prompt", MessageAudience::Directed { actor_ids: vec!["human_1".into()] }),
            (3, "state_machine_human_input_response", MessageAudience::Directed { actor_ids: vec!["human_1".into()] })] {
            messages.append_message_with_id(format!("{name}-{index}"), NewMessage {
                group_id: name.into(), session_id: sid.clone(), sender_id: "bot-1".into(), sender_type: SenderType::Bot,
                message_type: kind.into(), content: json!({"text": format!("frozen {index}"), "metadata": {"state_machine": {
                    "run_id": "deleted-run", "node_id": format!("node-{index}"), "attempt": 0}}}),
                client_msg_id: Some(format!("key-{index}")), owner_bot_id: None, created_at: 100 + index,
                run_id: "deleted-run".into(), visibility_domain: MessageVisibilityDomain::StateMachine, audience: Some(audience),
            }).await.unwrap();
        }
        ids.push(sid);
    }
    let cutoff = sessions.get(&ids[0]).await.unwrap().created_at;
    verify(db.clone(), "local", "1", &ids, 0).await;
    verify(db.clone(), "local", "1", &ids, cutoff).await;
    // Before the cutoff, the pure StateMachine Session still uses its legacy
    // workflow source. This fixture has persisted messages but no legacy Run.
    let guarded = Arc::new(ReadOnlyHistoryDb { inner: db, reads: AtomicUsize::new(0), allow_workflow: true });
    for (app, prefix) in routers(guarded, "local", "1", cutoff + 1).iter().zip(["", "/openapi/v1/collaboration"]) {
        let (status, body) = page(app, prefix, &ids[0], "limit=100").await;
        assert_eq!(status, StatusCode::OK);
        let rows = if prefix.is_empty() { &body } else { &body["data"] }.as_array().unwrap();
        assert!(rows.is_empty(), "pre-cutoff Session must keep the runtime source in {prefix}");
    }
}

#[tokio::test]
#[ignore = "requires BCS_HISTORY_ACCEPTANCE_DB (SQLite snapshot) and BCS_HISTORY_ACCEPTANCE_SESSIONS"]
async fn snapshot_messages_http_acceptance() {
    let path = std::env::var("BCS_HISTORY_ACCEPTANCE_DB").expect("SQLite snapshot path");
    let sessions = std::env::var("BCS_HISTORY_ACCEPTANCE_SESSIONS").expect("comma separated Session IDs").split(',').map(str::to_string).collect::<Vec<_>>();
    let user = std::env::var("BCS_HISTORY_ACCEPTANCE_USER").unwrap_or_else(|_| "001".into());
    let env = std::env::var("BCS_HISTORY_ACCEPTANCE_ENV").unwrap_or_else(|_| "local".into());
    verify(Arc::new(LocalSqliteDbPlugin::new_file(path).unwrap()), &env, &user, &sessions, 0).await;
}
