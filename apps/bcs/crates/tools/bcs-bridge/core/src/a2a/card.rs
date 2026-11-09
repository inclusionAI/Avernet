use a2a::{AgentCard, AgentCapabilities, AgentInterface, AgentSkill, HttpAuthSecurityScheme, SecurityScheme};
use std::collections::HashMap;
use super::config::A2aConfig;

pub(super) fn card(config: &A2aConfig, provider: &str, bot: &str) -> AgentCard {
    let endpoint = config.endpoint(provider, bot);
    AgentCard {
        name: bot.into(),
        description: "Coding agent served by bcs-bridge".into(),
        version: env!("CARGO_PKG_VERSION").into(),
        supported_interfaces: vec![
            AgentInterface::new(endpoint.clone(), "HTTP+JSON"),
            AgentInterface::new(endpoint, "JSONRPC"),
        ],
        capabilities: AgentCapabilities {
            streaming: Some(true), push_notifications: Some(false),
            extended_agent_card: Some(false), extensions: None,
        },
        default_input_modes: vec!["text/plain".into()],
        default_output_modes: vec!["text/plain".into()],
        skills: vec![AgentSkill {
            id: "coding".into(), name: "Coding assistant".into(),
            description: "Run a text coding request in the configured workspace".into(),
            tags: vec!["coding".into()], examples: None, input_modes: None,
            output_modes: None, security_requirements: None,
        }],
        security_schemes: Some(HashMap::from([("bearer".into(), SecurityScheme::HttpAuth(HttpAuthSecurityScheme {
            scheme: "Bearer".into(), bearer_format: None, description: None,
        }))])),
        security_requirements: Some(vec![HashMap::from([("bearer".into(), vec![])])]),
        provider: None, documentation_url: None, icon_url: None, signatures: None,
    }
}
