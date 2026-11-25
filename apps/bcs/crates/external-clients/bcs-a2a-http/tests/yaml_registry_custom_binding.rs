//! A card whose interface uses a deployment-installed custom binding, in the
//! shape a Provider registers it (binding-specific fields under `extensions`,
//! no URL). Its own test binary: bindings are installed once per process.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

use bcs_a2a_binding_api::{Binding, Client, Registry};
use bcs_a2a_http::dist_agent::{DistAgentResolver, YamlAgentResolver};
use bcs_dist_model::*;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::types::a2a_bot::{A2aBinding, A2aUpdateMode};
use serde_json::Value;
use std::sync::Arc;
use std::time::Duration;

/// Routes by the `agentId` its declarations carry; never dials.
struct ExampleRpc;

impl Binding for ExampleRpc {
    fn name(&self) -> &str {
        "EXAMPLERPC"
    }
    fn resolve(&self, declaration: &Value, _streaming: bool) -> Result<String, String> {
        let app = declaration.pointer("/extensions/applicationName").and_then(Value::as_str).ok_or("NO_APP")?;
        let agent = declaration.pointer("/extensions/agentId").and_then(Value::as_str).ok_or("NO_AGENT")?;
        Ok(format!("{app}/{agent}"))
    }
    fn fingerprint(&self, key: &str) -> Result<String, String> {
        Ok(format!("v1:{key}"))
    }
    fn client(&self, _: &str, _: Option<&str>, _: Option<&str>, _: &str, _: Duration) -> Result<Arc<dyn Client>, String> {
        Err("NOT_IN_TESTS".into())
    }
}

/// The lookup authority of the custom-binding card test (plan T6).
fn authority_for_binding(binding: &AgentBinding) -> bcs_service_api::port::dist_agent::CallAuthority {
    bcs_service_api::port::dist_agent::CallAuthority {
        round: "registry-round".into(),
        node: NodeId("n2".into()),
        attempt: 1,
        binding: binding.clone(),
        operation: bcs_service_api::port::dist_agent::CallOperation::Start,
        remote_ref: None,
    }
}

const YAML: &str = r#"
agents:
  - provider_bot_ref: order-agent-001
    owners: ["410025"]
    visibility: protected
    agent_card:
      name: Order assistant
      description: Order, delivery and after-sales status.
      version: 1.0.0
      supportedInterfaces:
        - protocolBinding: EXAMPLERPC
          protocolVersion: "1.0"
          extensions:
            applicationName: agentbase
            agentId: order-001
      capabilities:
        streaming: true
      defaultInputModes: [text/plain]
      defaultOutputModes: [text/plain]
      skills: []
"#;

#[tokio::test]
async fn a_custom_binding_card_resolves_to_its_custom_route() {
    bcs_a2a_binding_api::install(Registry::new(vec![Arc::new(ExampleRpc)]).unwrap()).unwrap();
    let registry = YamlAgentResolver::from_yaml(YAML, &OutboundUrlGuard::new(false, true), None, |_| None).unwrap();
    let agent = registry
        .resolve(&authority_for_binding(&AgentBinding {
            agent: AgentId("order-agent-001".into()),
            binding_version: BindingVersion("1".into()),
            credential_ref: CredentialRef("none".into()),
        }))
        .await
        .unwrap();
    assert_eq!(agent.interface.binding, A2aBinding::Custom);
    assert!(agent.interface.url.starts_with("custom-a2a://route/"), "{}", agent.interface.url);
    assert_eq!(bcs_a2a_binding_api::registry().fingerprint(&agent.interface.url).unwrap(), "v1:agentbase/order-001");
    assert_eq!(agent.interface.update_mode, A2aUpdateMode::Stream);
}
