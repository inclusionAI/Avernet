//! Unit tests for `BotJoinedMessageProducer`.
//! CONFORMANCE_WAIVED: MockRegistry is a test double, not a production impl.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{
    ActorKind, ActorStatus, BotCapabilities, BotDynamicStatus, Group, Participant, ParticipantRole,
    RegisteredBot, Skill, SystemMessageEvent,
};
use bcs_service_api::{AgentCredentials, BotRegistryCoreService, ServiceResult, SystemMessageProducerService};

use super::bot_joined::BotJoinedMessageProducer;

#[derive(Default)]
struct MockRegistry {
    bots: std::collections::HashMap<String, RegisteredBot>,
}

#[async_trait]
impl BotRegistryCoreService for MockRegistry {
    async fn register(&self, _bot_id: String, _capabilities: BotCapabilities) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_status(&self, _bot_id: &str, _status: BotDynamicStatus) -> bool {
        false
    }

    async fn get(&self, bot_id: &str) -> Option<RegisteredBot> {
        self.bots.get(bot_id).cloned()
    }

    async fn get_agent_credentials(&self, _bot_id: &str) -> Option<AgentCredentials> {
        None
    }

    async fn list_active(&self) -> Vec<RegisteredBot> {
        self.bots.values().cloned().collect()
    }

    async fn list_bots_by_creator(&self, _created_by: &str) -> Vec<RegisteredBot> {
        vec![]
    }

    async fn discover(&self, _query: &str) -> Vec<RegisteredBot> {
        vec![]
    }

    async fn find_by_skills(&self, _skills: &[&str]) -> Vec<RegisteredBot> {
        vec![]
    }

    async fn find_by_domains(&self, _domains: &[&str]) -> Vec<RegisteredBot> {
        vec![]
    }

    async fn find_by_scopes(&self, _scopes: &[&str]) -> Vec<RegisteredBot> {
        vec![]
    }

    async fn unregister(&self, _bot_id: &str) -> bool {
        false
    }

    async fn cleanup_expired(&self) {}

    async fn load_from_storage(&self, _bot_id: &str) -> Option<BotCapabilities> {
        None
    }

    async fn save_to_storage(
        &self,
        _bot_id: &str,
        _caps: &BotCapabilities,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn update_visibility(
        &self,
        _bot_id: &str,
        _visibility: &str,
    ) -> ServiceResult<()> {
        Ok(())
    }

    #[allow(deprecated)]
    async fn set_hidden(&self, _bot_id: &str, _hidden: bool) -> ServiceResult<()> {
        Ok(())
    }

    async fn has_been_onboarded(&self, _bot_id: &str) -> bool {
        false
    }

    async fn save_created_by(
        &self,
        _bot_id: &str,
        _created_by: &str,
        _overwrite: bool,
    ) -> ServiceResult<()> {
        Ok(())
    }

    async fn save_token(&self, _bot_id: &str, _token: &str) -> ServiceResult<()> {
        Ok(())
    }

    async fn load_token(&self, _bot_id: &str) -> Option<String> {
        None
    }

    async fn find_bot_by_token(&self, _token: &str) -> Option<String> {
        None
    }

    async fn register_streaming_connection(&self, _bot_id: String) -> Result<String, ()> {
        Err(())
    }

    async fn reconnect_streaming(
        &self,
        _existing_token: String,
    ) -> Result<(String, String), ()> {
        Err(())
    }

    async fn disconnect_streaming(&self, _bot_id: &str) {}

    async fn is_connected(&self, _bot_id: &str) -> bool {
        false
    }

    async fn send_frame(&self, _bot_id: &str, _frame: String) -> Result<(), ()> {
        Err(())
    }

    async fn list_connected(&self) -> Vec<String> {
        vec![]
    }

    async fn store_token_mapping(&self, _token: String, _bot_id: String) {}

    async fn register_http_connection(&self, _bot_id: String, _token: String) -> String {
        String::new()
    }
}

#[tokio::test]
async fn bot_joined_produces_context_injection_and_notification() {
    let driver = Participant::bot("driver-id", ParticipantRole::Driver);
    let consultant = Participant::bot("consultant-id", ParticipantRole::Consultant);
    let new_bot = Participant::bot("new-bot-id", ParticipantRole::Consultant);

    let group = Group::new("group-1", "driver-id", vec![driver, consultant, new_bot.clone()]);

    let mut registry = MockRegistry::default();
    registry.bots.insert(
        "new-bot-id".to_string(),
        RegisteredBot {
            bot_uuid: "new-bot-id".to_string(),
            capabilities: BotCapabilities {
                name: Some("NewBot".to_string()),
                skills: vec![Skill::new("coding")],
                ..Default::default()
            },
            dynamic_status: BotDynamicStatus::default(),
            env: None,
            created_by: None,
            actor_kind: ActorKind::Bot,
            status: ActorStatus::default(),
        },
    );

    let producer = BotJoinedMessageProducer::new(Arc::new(bcs_test_support::NoopGroupMessageHistoryService));
    let event = SystemMessageEvent::BotJoined {
        group_id: "group-1".to_string(),
        actor: new_bot,
    };

    let messages = producer.produce(&event, &group, &registry, &group.participants).await;

    assert_eq!(
        messages.len(),
        2,
        "expected 2 messages: context injection + notification"
    );

    // 1. Context injection to the newly joined bot.
    let injection = messages
        .iter()
        .find(|m| m.recipients == vec!["new-bot-id".to_string()]);
    assert!(
        injection.is_some(),
        "expected a context injection message for the new bot"
    );
    let injection = injection.unwrap();
    assert!(
        injection.message.contains("你加入了 BCS 协作群."),
        "context injection should contain '你加入了 BCS 协作群.'"
    );
    assert!(
        injection.message.contains("群 ID:"),
        "context injection should contain '群 ID:'"
    );
    assert!(
        injection.message.contains("参与者:"),
        "context injection should contain '参与者:'"
    );

    // 2. Short notification to other bots.
    let notification = messages
        .iter()
        .find(|m| m.recipients != vec!["new-bot-id".to_string()]);
    assert!(
        notification.is_some(),
        "expected a notification message for other bots"
    );
    let notification = notification.unwrap();
    assert!(
        notification.message.contains("NewBot(new-bot-id) 已加入协作群 - 能力集: {name: \"coding\"}"),
        "notification should contain formatted name/uuid and skills"
    );
    assert_eq!(injection.delivery_type, bcs_domain::DeliveryType::Inject);
    assert_eq!(notification.delivery_type, bcs_domain::DeliveryType::Inject);
    assert!(notification.recipients.contains(&"driver-id".to_string()));
    assert!(notification.recipients.contains(&"consultant-id".to_string()));
    assert!(!notification.recipients.contains(&"new-bot-id".to_string()));
}
