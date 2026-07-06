use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::BotCore;
use bcs_group::GroupStore;
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState, UserIdentityPort},
};
use bcs_service_api::{
    BotCapabilities, BotRegistryCoreService, GroupChatProposal, GroupProposalConfirmCommand,
    GroupProposalConfirmResult, GroupProposalCreateCommand, GroupProposalCreateResult,
    GroupProposalPreviewCommand, GroupProposalPreviewResult, GroupProposalService, ProposalContext,
    ProposalCoreService, Skill,
};
use bcs_services_container::Services;
use serde_json::Value;
use std::{collections::HashMap, sync::Arc};
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

#[derive(Default)]
struct MemoryProposalCoreService {
    proposals: Mutex<HashMap<String, GroupChatProposal>>,
}

#[async_trait::async_trait]
impl ProposalCoreService for MemoryProposalCoreService {
    async fn store(&self, proposal: GroupChatProposal) -> String {
        let token = proposal.token.clone();
        self.proposals.lock().await.insert(token.clone(), proposal);
        token
    }

    async fn get(&self, token: &str) -> Option<GroupChatProposal> {
        self.proposals.lock().await.get(token).cloned()
    }

    async fn take(&self, token: &str) -> Option<GroupChatProposal> {
        self.proposals.lock().await.remove(token)
    }

    async fn cleanup_expired(&self) -> usize {
        0
    }
}

#[derive(Default)]
struct RecordingGroupProposalService {
    create_calls: Mutex<Vec<GroupProposalCreateCommand>>,
    confirm_calls: Mutex<Vec<GroupProposalConfirmCommand>>,
    preview_calls: Mutex<Vec<GroupProposalPreviewCommand>>,
}

#[async_trait::async_trait]
impl GroupProposalService for RecordingGroupProposalService {
    async fn create_proposal(
        &self,
        cmd: GroupProposalCreateCommand,
    ) -> Result<GroupProposalCreateResult, bcs_service_api::GroupUseCaseError> {
        self.create_calls.lock().await.push(cmd);
        Ok(GroupProposalCreateResult {
            proposal_created: true,
            driver_bot_id: "driver-bot".to_string(),
            participant_bot_ids: vec!["target-bot".to_string(), "driver-bot".to_string()],
            member_intros: "**Target** (成员)\n**Driver** (Driver)".to_string(),
            confirm_url: "http://bcs.example.test/groups/recorded-token/confirm".to_string(),
            expires_in_seconds: 600,
            message: "recorded proposal message".to_string(),
        })
    }

    async fn confirm_proposal(
        &self,
        cmd: GroupProposalConfirmCommand,
    ) -> Result<GroupProposalConfirmResult, bcs_service_api::GroupUseCaseError> {
        self.confirm_calls.lock().await.push(cmd);
        Ok(GroupProposalConfirmResult {
            created: true,
            group_id: "created-by-use-case".to_string(),
            driver_bot_id: "driver-bot".to_string(),
            participant_bot_ids: vec!["target-bot".to_string(), "driver-bot".to_string()],
            chat_url: None,
            context_injected: 7,
        })
    }

    async fn preview_proposal(
        &self,
        cmd: GroupProposalPreviewCommand,
    ) -> Result<GroupProposalPreviewResult, bcs_service_api::GroupUseCaseError> {
        self.preview_calls.lock().await.push(cmd.clone());
        if cmd.token.contains("expired") {
            return Err(bcs_service_api::GroupUseCaseError::ProposalExpired(
                cmd.token,
            ));
        }
        Ok(GroupProposalPreviewResult {
            token: cmd.token.clone(),
            proposal: active_proposal(&cmd.token),
        })
    }
}

fn static_auth_chain(staff_no: &str, nick_name: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(principal))]))
}

#[tokio::test]
async fn group_request_create_delegates_to_group_proposal_use_case_and_preserves_legacy_json() {
    let fixture = Fixture::new().await;
    let app = fixture.app();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/request")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "topic": "Need help",
                        "suggested_driver": "target-bot",
                        "suggested_participants": ["target-bot"],
                        "context": {
                            "user_query": "how do we ship?",
                            "detected_gap": "needs release coordination",
                            "relevant_history": ["previous launch notes"]
                        }
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["proposal_created"], true);
    assert_eq!(json["driver_bot"], "driver-bot");
    assert_eq!(
        json["participants"],
        serde_json::json!(["target-bot", "driver-bot"])
    );
    assert_eq!(
        json["member_intros"],
        "**Target** (成员)\n**Driver** (Driver)"
    );
    assert_eq!(
        json["confirm_url"],
        "http://bcs.example.test/groups/recorded-token/confirm"
    );
    assert_eq!(json["expires_in_seconds"], 600);
    assert_eq!(json["message"], "recorded proposal message");

    let calls = fixture.proposals.create_calls.lock().await;
    assert_eq!(calls.len(), 1);
    let cmd = &calls[0];
    assert_eq!(cmd.caller_actor_id.as_deref(), Some("driver-bot"));
    assert_eq!(cmd.driver_bot_id, "driver-bot");
    assert_eq!(cmd.suggested_driver_bot_id.as_deref(), Some("target-bot"));
    assert_eq!(cmd.suggested_participants, vec!["target-bot"]);
    assert_eq!(cmd.topic, "Need help");
    assert!(matches!(
        cmd.context.as_ref(),
        Some(ProposalContext {
            user_query,
            detected_gap,
            relevant_history,
        }) if user_query.as_deref() == Some("how do we ship?")
            && detected_gap.as_deref() == Some("needs release coordination")
            && relevant_history == &vec!["previous launch notes".to_string()]
    ));
}

#[tokio::test]
async fn group_request_create_leaves_ownership_authorization_to_use_case() {
    let fixture = Fixture::new().await;
    let chain = static_auth_chain("bob", "Bob");
    let app = fixture.app_with_user_identity(Arc::new(ChainUserIdentityPort::new(chain)));

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/request")
                .header("authorization", "Bearer driver-token")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({
                        "topic": "Need help despite HTTP identity mismatch",
                        "suggested_participants": ["target-bot"]
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let calls = fixture.proposals.create_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].caller_actor_id.as_deref(), Some("driver-bot"));
    assert_eq!(calls[0].driver_bot_id, "driver-bot");
    assert_eq!(calls[0].topic, "Need help despite HTTP identity mismatch");
}

#[tokio::test]
async fn confirm_page_renders_from_proposal_preview_without_confirming_use_case() {
    let fixture = Fixture::new()
        .await
        .with_preview_proposal("preview-token")
        .await;
    let app = fixture.app();

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/preview-token/confirm")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let html = String::from_utf8(body.to_vec()).unwrap();
    assert!(html.contains("确认创建群聊"));
    assert!(html.contains("driver-bot"));
    assert!(html.contains("Need help"));
    assert!(fixture.proposals.confirm_calls.lock().await.is_empty());
    let preview_calls = fixture.proposals.preview_calls.lock().await;
    assert_eq!(preview_calls.len(), 1);
    assert_eq!(preview_calls[0].token, "preview-token");
}

#[tokio::test]
async fn group_request_confirm_delegates_to_use_case_and_preserves_legacy_json() {
    let fixture = Fixture::new()
        .await
        .with_preview_proposal("confirm-token")
        .await;
    let app = fixture.app();

    let response = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/groups/confirm-token/confirm")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["created"], true);
    assert_eq!(json["group_id"], "created-by-use-case");
    assert_eq!(json["driver_bot"], "driver-bot");
    assert_eq!(
        json["participants"],
        serde_json::json!(["target-bot", "driver-bot"])
    );
    assert_eq!(json["chat_url"], Value::Null);
    assert_eq!(json["context_injected"], 7);

    let calls = fixture.proposals.confirm_calls.lock().await;
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].token, "confirm-token");
    assert_eq!(calls[0].caller_actor_id, None);
}

#[tokio::test]
async fn confirm_page_for_expired_proposal_returns_expired_html() {
    let fixture = Fixture::new()
        .await
        .with_expired_proposal("expired-token")
        .await;
    let app = fixture.app();

    let response = app
        .oneshot(
            Request::builder()
                .uri("/groups/expired-token/confirm")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let html = String::from_utf8(body.to_vec()).unwrap();
    assert!(html.contains("提案已过期"));
}

struct Fixture {
    services: Services,
    proposals: Arc<RecordingGroupProposalService>,
    proposal_store: Arc<MemoryProposalCoreService>,
    _temp_dir: TempDir,
}

impl Fixture {
    async fn new() -> Self {
        let temp_dir = TempDir::new().unwrap();
        let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
        for (bot_id, name) in [("driver-bot", "Driver"), ("target-bot", "Target")] {
            registry
                .register(
                    bot_id.to_string(),
                    BotCapabilities {
                        name: Some(name.to_string()),
                        summary: Some(format!("{name} summary")),
                        skills: vec![Skill::new("help")],
                        visibility: "public".to_string(),
                        ..BotCapabilities::default()
                    },
                )
                .await
                .unwrap();
        }
        registry
            .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
            .await;
        registry
            .save_created_by("driver-bot", "alice", true)
            .await
            .unwrap();

        let proposal_store = Arc::new(MemoryProposalCoreService::default());
        let proposals = Arc::new(RecordingGroupProposalService::default());
        let services = Services::builder()
            .registry(registry)
            .group(Arc::new(GroupStore::new()))
            .proposal(proposal_store.clone())
            .group_proposals(proposals.clone())
            .build_for_test();
        Self {
            services,
            proposals,
            proposal_store,
            _temp_dir: temp_dir,
        }
    }

    fn app(&self) -> axum::Router {
        self.app_with_state(HttpAppState::new(self.services.clone()))
    }

    fn app_with_user_identity(&self, user_identity: Arc<dyn UserIdentityPort>) -> axum::Router {
        self.app_with_state(
            HttpAppState::new(self.services.clone()).with_user_identity(user_identity),
        )
    }

    fn app_with_state(&self, state: HttpAppState) -> axum::Router {
        build_router(
            state
                .with_group_request_config(
                    Some("http://bcs.example.test".to_string()),
                    "127.0.0.1".to_string(),
                    21000,
                    3,
                    5,
                    10,
                ),
        )
    }

    async fn with_preview_proposal(self, token: &str) -> Self {
        self.proposal_store.store(active_proposal(token)).await;
        self
    }

    async fn with_expired_proposal(self, token: &str) -> Self {
        let mut proposal = active_proposal(token);
        proposal.reason = "Expired topic".to_string();
        proposal.created_at = proposal
            .created_at
            .saturating_sub(GroupChatProposal::EXPIRY_MS + 1);
        self.proposal_store.store(proposal).await;
        self
    }
}

fn active_proposal(token: &str) -> GroupChatProposal {
    GroupChatProposal {
        token: token.to_string(),
        driver_bot: "driver-bot".to_string(),
        participants: vec!["target-bot".to_string(), "driver-bot".to_string()],
        reason: "Need help".to_string(),
        proposed_by: "driver-bot".to_string(),
        member_intros: "**Target** (成员)\n**Driver** (Driver)".to_string(),
        confirm_url: format!("http://bcs.example.test/groups/{token}/confirm"),
        created_at: now_ms(),
    }
}

fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis() as u64)
        .unwrap_or(0)
}
