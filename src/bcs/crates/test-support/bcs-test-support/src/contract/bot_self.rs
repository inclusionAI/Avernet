//! Read-only Agent identity and registration contracts. Implementations seed
//! their own authoritative data and exercise failures in their conformance entry.

use bcs_service_api::application::v1::{BotRegistrationStatus, BotSelfService, BotSelfView};
use bcs_service_api::port::agent_identity::{AgentIdentityError, AgentIdentityPort, VerifiedAgentIdentity};
use bcs_service_api::types::AgentBotRegistration;
use bcs_service_api::BotRepoPort;

pub async fn agent_identity_port_contract_tests(
    port: &dyn AgentIdentityPort,
    credential: &str,
    expected: Result<VerifiedAgentIdentity, AgentIdentityError>,
) {
    let actual = port.verify(credential).await;
    if let Ok(identity) = &actual {
        assert!(!identity.agent_code.trim().is_empty());
    }
    assert_eq!(actual, expected);
}

pub async fn bot_self_service_contract_tests(
    service: &dyn BotSelfService, credential: &str, expected: BotSelfView,
) {
    let actual = service.get_me(credential).await.expect("self lookup");
    assert!(!actual.agent_code.trim().is_empty());
    assert_eq!(actual.bot.is_some(), actual.registration_status == BotRegistrationStatus::Registered);
    assert_eq!(actual, expected);
    assert_eq!(service.get_me("").await.unwrap_err().code(), "unauthenticated");
}

pub async fn agent_registration_lookup_contract_tests(repo: &dyn BotRepoPort) {
    let bot = repo.find_agent_registration("agent-001").await.expect("registration read");
    assert_eq!(bot, Some(AgentBotRegistration {
        bot_id: "bot-001".into(), name: Some("Poolab Assistant".into()),
        summary: Some("负责研发任务的 Agent".into()),
        provider_id: Some("provider-a".into()), provider_bot_ref: Some("bot-001".into()),
    }));
    assert!(repo.find_agent_registration("never-registered").await.expect("missing registration read").is_none());
}
