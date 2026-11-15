use async_trait::async_trait;
use bcs_app_a2a_bot::v1::A2aApiService;
use bcs_service_api::{
    ServiceError, ServiceResult,
    application::{
        a2a_bot::*,
        v1::{ApplicationError, a2a::*},
    },
    types::a2a_bot::{
        A2aBotRecord, A2aCardSource,
        a2a_management::{ManagementCode, ManagementError},
    },
};
use serde_json::json;
use std::sync::{
    Arc,
    atomic::{AtomicUsize, Ordering},
};

#[derive(Default)]
struct Management {
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
fn fixture() -> (A2aApiService, Arc<Management>) {
    let management = Arc::new(Management::default());
    (A2aApiService::new(management.clone()), management)
}

#[tokio::test]
async fn conformance_card_use_cases_preserve_sources_visibility_and_machine_errors() {
    let (service, management) = fixture();
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
