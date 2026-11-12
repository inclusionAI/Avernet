use async_trait::async_trait;
use bcs_app_a2a_bot::v1::A2aApiService;
use bcs_service_api::{
    ServiceError, ServiceResult,
    application::{
        a2a_bot::*,
        a2a_chat::*,
        principal::CallerContext,
        v1::{ApplicationError, BotRegistrationStatus, BotSelfService, BotSelfView, a2a::*},
    },
    types::{
        AgentBotRegistration,
        a2a_bot::{
            A2aBotRecord, A2aCardSource,
            a2a_management::{ManagementCode, ManagementError},
        },
    },
};
use serde_json::json;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicUsize, Ordering},
};

#[derive(Default)]
struct Management {
    resolutions: AtomicUsize,
    mutations: AtomicUsize,
}
fn card() -> ManagedA2aCard {
    ManagedA2aCard {
        provider_bot_ref: "remote".into(),
        provider_name: "orders".into(),
        agent_name: "Agent".into(),
        bot_id: "target".into(),
        agent_card_url: None,
        agent_card: json!({"name":"Agent","unknown":[1,2]}),
    }
}
#[async_trait]
impl A2aBotManagementService for Management {
    async fn register_agent(&self, cmd: RegisterAgentCommand) -> ServiceResult<ManagedA2aCard> {
        assert_eq!(cmd.provider_admin_token, "admin");
        assert!(cmd.now_ms > 0);
        assert!(matches!(cmd.source, A2aCardSource::Inline(_)));
        assert!(cmd.owners.is_none());
        assert_eq!(cmd.visibility, A2aRegistrationVisibility::Private);
        self.mutations.fetch_add(1, Ordering::SeqCst);
        Ok(card())
    }
    async fn find_agents_by_provider_ref(
        &self,
        token: &str,
        reference: &str,
    ) -> ServiceResult<Vec<ManagedA2aCard>> {
        assert_eq!((token, reference), ("admin", "remote"));
        Ok(vec![card()])
    }
    async fn get_agent_by_id(&self, token: &str, bot: &str) -> ServiceResult<ManagedA2aCard> {
        assert_eq!((token, bot), ("admin", "target"));
        Ok(card())
    }
    async fn update_agent_by_id_with_visibility(
        &self,
        token: &str,
        bot: &str,
        source: Option<A2aCardSource>,
        visibility: Option<A2aRegistrationVisibility>,
    ) -> ServiceResult<ManagedA2aCard> {
        assert_eq!((token, bot), ("admin", "target"));
        assert!(source.is_none());
        assert_eq!(visibility, Some(A2aRegistrationVisibility::Public));
        self.mutations.fetch_add(1, Ordering::SeqCst);
        Ok(card())
    }
    async fn refresh_agent_by_id(&self, _: &str, _: &str) -> ServiceResult<ManagedA2aCard> {
        Err(ServiceError::A2aManagement(ManagementError::new(
            ManagementCode::CardUrlRequired,
            "agent_card_url",
            "URL_REQUIRED",
        )))
    }
    async fn register_named(
        &self,
        cmd: RegisterNamedA2aBotCommand,
    ) -> ServiceResult<NamedA2aRegistration> {
        assert_eq!(cmd.provider_name, "orders");
        assert_eq!(cmd.provider_admin_token, "admin");
        Ok(NamedA2aRegistration {
            card: NamedA2aCard {
                provider_bot_ref: cmd.provider_bot_ref,
                bot_id: "target".into(),
                provider_name: cmd.provider_name,
                bot_name: "Agent".into(),
                agent_card_url: None,
                agent_card: card().agent_card,
            },
            bot_runtime_token: Some("runtime".into()),
        })
    }
    async fn resolve_provider_ref_target(
        &self,
        provider: &str,
        reference: &str,
    ) -> ServiceResult<String> {
        self.resolutions.fetch_add(1, Ordering::SeqCst);
        assert_eq!((provider, reference), ("orders", "remote"));
        Ok("target".into())
    }
    async fn get(&self, _: &str, _: &str, _: &str) -> ServiceResult<Option<A2aBotRecord>> {
        panic!("legacy revision route")
    }
    async fn configure(&self, _: ConfigureA2aBotCommand) -> ServiceResult<A2aBotRecord> {
        panic!("legacy revision route")
    }
    async fn refresh(&self, _: &str, _: &str, _: &str, _: i64) -> ServiceResult<A2aBotRecord> {
        panic!("legacy revision route")
    }
    async fn refresh_due(&self, _: i64) -> ServiceResult<usize> {
        panic!("background scan")
    }
}
struct Identity;
#[async_trait]
impl BotSelfService for Identity {
    async fn get_me(&self, token: &str) -> Result<BotSelfView, ApplicationError> {
        self.get_me_with_actor(token, None).await
    }
    async fn get_me_with_actor(
        &self,
        token: &str,
        actor: Option<&str>,
    ) -> Result<BotSelfView, ApplicationError> {
        if token != "bot-token" || actor.is_some_and(|a| a != "caller") {
            return Err(ApplicationError::Unauthenticated);
        }
        Ok(BotSelfView {
            registration_status: BotRegistrationStatus::Registered,
            agent_code: "agent".into(),
            bot: Some(AgentBotRegistration {
                bot_id: "caller".into(),
                name: None,
                summary: None,
                provider_id: None,
                provider_bot_ref: None,
            }),
        })
    }
}
#[derive(Default)]
struct Runs {
    started: Mutex<Vec<AsyncA2aChatCommand>>,
    queries: Mutex<Vec<ChatRunQueryCommand>>,
}
fn run() -> A2aRunStatus {
    A2aRunStatus {
        run_id: "run".into(),
        status: "failed".into(),
        response: Some(json!({"state":"failed","content":"answer",
        "error_message":"private-provider diagnostic","bot_uuid":"private-target","provider_id":"private-provider",
        "session_id":"session","version":4,"expires_at_ms":100})),
    }
}
#[async_trait]
impl A2aChatRunService for Runs {
    async fn start_async_chat(
        &self,
        cmd: AsyncA2aChatCommand,
    ) -> ServiceResult<AsyncA2aChatAccepted> {
        assert!(matches!(&cmd.caller,CallerContext::Bot(bot) if bot.bot_uuid=="caller"));
        self.started.lock().unwrap().push(cmd.clone());
        Ok(AsyncA2aChatAccepted {
            delivery: Some(json!({"private":"never-exposed"})),
            run_id: cmd.run_id,
            bot_uuid: cmd.target_bot_id,
            session_id: "session".into(),
            status: "pending".into(),
            expires_at_ms: 100,
        })
    }
    async fn get_run(&self, cmd: ChatRunQueryCommand) -> ServiceResult<A2aRunStatus> {
        self.queries.lock().unwrap().push(cmd);
        Ok(run())
    }
    async fn cancel_run(&self, cmd: ChatRunCancelCommand) -> ServiceResult<A2aRunStatus> {
        assert!(matches!(&cmd.caller,CallerContext::Bot(bot) if bot.bot_uuid=="caller"));
        assert_eq!(cmd.run_id, "run");
        Ok(run())
    }
}
fn fixture() -> (A2aApiService, Arc<Management>, Arc<Runs>) {
    let management = Arc::new(Management::default());
    let runs = Arc::new(Runs::default());
    (
        A2aApiService::new(
            management.clone(),
            Arc::new(Identity),
            runs.clone(),
            3000,
            1000,
        ),
        management,
        runs,
    )
}
fn command() -> StartNamedA2aChat {
    StartNamedA2aChat {
        token: "bot-token".into(),
        actor: Some("caller".into()),
        provider_name: "orders".into(),
        provider_bot_ref: "remote".into(),
        message: "hello".into(),
        from: None,
        session_id: None,
        timeout_ms: Some(u64::MAX),
        client: Some("bcs-cli-test".into()),
        tags: vec![" a ".into(), " ".into()],
        response_mode: ChatResponseMode::AfterLastToolCall,
        caller_wait_mode: Some(" full ".into()),
        organization_code: Some(" org ".into()),
        provider_bypass_headers: vec![],
    }
}

#[tokio::test]
async fn conformance_authentication_precedes_resolution_and_rejects_actor_substitution() {
    let (service, management, runs) = fixture();
    let mut invalid = command();
    invalid.token = "invalid".into();
    assert!(matches!(
        service.chat(invalid).await,
        Err(ApplicationError::Unauthenticated)
    ));
    let mut invalid = command();
    invalid.actor = Some("foreign".into());
    assert!(matches!(
        service.chat(invalid).await,
        Err(ApplicationError::Unauthenticated)
    ));
    assert_eq!(management.resolutions.load(Ordering::SeqCst), 0);
    assert!(runs.started.lock().unwrap().is_empty());
    assert!(matches!(
        service.cancel_run("invalid", None, "run").await,
        Err(ApplicationError::Unauthenticated)
    ));
}

#[tokio::test]
async fn conformance_chat_uses_verified_caller_and_retains_lifecycle_limits_and_safe_projection() {
    let (service, management, runs) = fixture();
    let accepted = service.chat(command()).await.unwrap();
    assert_eq!(accepted["provider_bot_ref"], "remote");
    assert!(accepted.get("delivery").is_none());
    assert!(accepted.get("bot_uuid").is_none());
    assert_eq!(management.resolutions.load(Ordering::SeqCst), 1);
    let started = runs.started.lock().unwrap()[0].clone();
    assert_eq!(started.timeout_ms, 86_400_000);
    assert!(started.session_key.starts_with("bcs-cli:caller:"));
    assert_eq!(started.tags, vec!["a"]);
    assert_eq!(started.from_actor_id.as_deref(), Some("user"));
    assert!(started.run_channel_from.is_none());
    assert!(started.authenticated_staff_id.is_none());
    assert_eq!(started.organization_code.as_deref(), Some("org"));
    let result = service
        .get_run(QueryA2aRun {
            token: "bot-token".into(),
            actor: Some("caller".into()),
            run_id: "run".into(),
            wait_ms: u64::MAX,
            since_version: 3,
        })
        .await
        .unwrap();
    assert_eq!(runs.queries.lock().unwrap()[0].wait_ms, 1000);
    assert_eq!(runs.queries.lock().unwrap()[0].since_version, 3);
    assert_eq!(result["response"]["content"], "answer");
    assert!(!result.to_string().contains("private-"));
    assert_eq!(
        result["error_message"],
        "Run failed; provide run_id to the platform operator"
    );
    let cancelled = service
        .cancel_run("bot-token", Some("caller"), "run")
        .await
        .unwrap();
    assert!(!cancelled.to_string().contains("private-"));
    assert_eq!(cancelled["version"], 4);
}

#[tokio::test]
async fn conformance_card_use_cases_preserve_sources_visibility_and_machine_errors() {
    let (service, management, _) = fixture();
    let create = || RegisterA2aAgent {
        token: "admin".into(),
        provider_bot_ref: "remote".into(),
        owners: None,
        summary: None,
        visibility: A2aRegistrationVisibility::Private,
        source: A2aCardInput {
            agent_card_url: None,
            agent_card: Some(card().agent_card),
        },
    };
    assert_eq!(
        service.register(create()).await.unwrap().agent_card["unknown"],
        json!([1, 2])
    );
    assert_eq!(service.find("admin", "remote").await.unwrap().len(), 1);
    assert_eq!(
        service.get("admin", "target").await.unwrap().bot_id,
        "target"
    );
    service
        .update(UpdateA2aAgent {
            token: "admin".into(),
            bot_id: "target".into(),
            source: A2aCardInput::default(),
            visibility: Some(A2aRegistrationVisibility::Public),
        })
        .await
        .unwrap();
    service
        .register_named(RegisterNamedA2aAgent {
            token: "admin".into(),
            provider_name: "orders".into(),
            provider_bot_ref: "remote".into(),
            owners: vec!["owner".into()],
            summary: None,
            source: A2aCardInput {
                agent_card_url: None,
                agent_card: Some(card().agent_card),
            },
        })
        .await
        .unwrap();
    assert!(
        matches!(service.refresh("admin","target").await,Err(ApplicationError::Conflict {code,..}) if code=="CARD_URL_REQUIRED")
    );
    let mut invalid = create();
    invalid.source.agent_card_url = Some("https://example.test/card".into());
    assert!(
        matches!(service.register(invalid).await,Err(ApplicationError::Unprocessable {code,..}) if code=="CARD_SOURCE_INVALID")
    );
    assert_eq!(management.mutations.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn conformance_invalid_session_does_not_dispatch_a_run() {
    let (service, _, runs) = fixture();
    for session in [" ".to_string(), "bad/path".into(), "x".repeat(129)] {
        let mut cmd = command();
        cmd.session_id = Some(session);
        assert!(matches!(
            service.chat(cmd).await,
            Err(ApplicationError::InvalidInput { .. })
        ));
    }
    assert!(runs.started.lock().unwrap().is_empty());
    let mut cmd = command();
    cmd.session_id = Some(" session:1._- ".into());
    cmd.timeout_ms = None;
    service.chat(cmd).await.unwrap();
    let started = runs.started.lock().unwrap()[0].clone();
    assert_eq!(started.session_key, "session:1._-");
    assert_eq!(started.timeout_ms, 3000);
}
