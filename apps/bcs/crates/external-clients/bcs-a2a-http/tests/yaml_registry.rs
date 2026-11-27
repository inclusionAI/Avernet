//! The YAML agent registry: load-time validation and lookup.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

use bcs_a2a_http::dist_agent::{DistAgentResolver, YamlAgentResolver};
use bcs_a2a_http::grpc_mesh::GrpcMeshConfig;
use bcs_dist_model::*;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::types::a2a_bot::{A2aBinding, A2aUpdateMode};

const TOOLS: &str = "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/tools/v1.md";

fn guard() -> OutboundUrlGuard {
    OutboundUrlGuard::new(false, true)
}

fn no_env(_: &str) -> Option<String> {
    None
}

/// The lookup authority of one test operation (plan T6): the resolver takes
/// the trusted per-operation authority, and the static registry keys its
/// lookup on the authority's approved binding.
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

fn binding(agent: &str) -> AgentBinding {
    AgentBinding {
        agent: AgentId(agent.into()),
        binding_version: BindingVersion("1".into()),
        credential_ref: CredentialRef("none".into()),
    }
}

/// One entry in the registration shape; `extra` is appended to the entry.
fn entry(id: &str, binding: &str, url: &str, extra: &str) -> String {
    format!(
        r#"
  - provider_bot_ref: {id}
    owners: ["410025"]
    visibility: protected
{extra}
    agent_card:
      name: Order assistant
      description: Order status.
      version: 1.0.0
      supportedInterfaces:
        - protocolBinding: {binding}
          protocolVersion: "1.0"
          url: {url}
      capabilities:
        streaming: true
        extensions:
          - uri: {TOOLS}
            required: false
          - uri: https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/source-agent/v2.md
            required: false
            params: {{ party: first_party }}
      defaultInputModes: [text/plain]
      defaultOutputModes: [text/plain]
      skills: []
      securitySchemes: {{}}
      securityRequirements: []
"#
    )
}

fn file(entries: &[String]) -> String {
    format!("agents:{}", entries.concat())
}

#[tokio::test]
async fn a_registered_agent_resolves_to_its_selected_interface() {
    let yaml = file(&[entry("order-agent-001", "JSONRPC", "https://agents.example.com/order/a2a", "")]);
    let registry = YamlAgentResolver::from_yaml(&yaml, &guard(), None, no_env).unwrap();
    assert_eq!(registry.len(), 1);
    let agent = registry.resolve(&authority_for_binding(&binding("order-agent-001"))).await.unwrap();
    assert_eq!(agent.interface.binding, A2aBinding::JsonRpc);
    assert_eq!(agent.interface.url, "https://agents.example.com/order/a2a");
    assert_eq!(agent.interface.update_mode, A2aUpdateMode::Stream);
    assert_eq!(agent.bearer, None, "an anonymous card gets no token");
    assert_eq!(agent.extensions, vec![TOOLS.to_owned()], "only extensions this client implements");
}

#[tokio::test]
async fn an_unknown_agent_is_not_registered() {
    let yaml = file(&[entry("order-agent-001", "HTTP+JSON", "https://agents.example.com/order", "")]);
    let registry = YamlAgentResolver::from_yaml(&yaml, &guard(), None, no_env).unwrap();
    assert_eq!(registry.resolve(&authority_for_binding(&binding("other"))).await.unwrap_err(), "agent_not_registered");
}

#[tokio::test]
async fn the_token_comes_from_the_named_environment_variable() {
    let yaml = file(&[entry(
        "order-agent-001",
        "JSONRPC",
        "https://agents.example.com/order/a2a",
        "    bearer_env: ORDER_AGENT_TOKEN",
    )]);
    let env = |name: &str| (name == "ORDER_AGENT_TOKEN").then(|| " secret-token ".to_owned());
    let registry = YamlAgentResolver::from_yaml(&yaml, &guard(), None, env).unwrap();
    let agent = registry.resolve(&authority_for_binding(&binding("order-agent-001"))).await.unwrap();
    assert_eq!(agent.bearer.as_deref(), Some("secret-token"));
    assert!(!format!("{registry:?}").contains("secret"), "the token is never printed");

    let error = YamlAgentResolver::from_yaml(&yaml, &guard(), None, no_env).unwrap_err();
    assert!(error.contains("agents[0] (order-agent-001)") && error.contains("bearer_env"), "{error}");
}

#[test]
fn invalid_files_fail_the_load() {
    let ok = || entry("a", "JSONRPC", "https://agents.example.com/a", "");
    let cases = [
        (file(&[ok(), ok()]), "duplicate provider_bot_ref"),
        (file(&[entry("a", "JSONRPC", "https://agents.example.com/a", "    unexpected: 1")]), "YAML error at line"),
        (file(&[entry("a", "GRPC", "http://grpc.example.com:9000", "")]), "no interface this deployment can call"),
        (file(&[entry("a", "JSONRPC", "ftp://agents.example.com/a", "")]), "agents[0] (a)"),
        (file(&[ok().replace("[text/plain]", "[application/json]")]), "text/plain"),
        ("agents: [".to_owned(), "agent_registry_invalid"),
        (file(&[entry("' padded'", "JSONRPC", "https://agents.example.com/a", "")]), "agents[0]: provider_bot_ref"),
    ];
    for (yaml, expected) in cases {
        let error = YamlAgentResolver::from_yaml(&yaml, &guard(), None, no_env).unwrap_err();
        assert!(error.contains(expected), "expected {expected:?} in {error:?}");
    }
}

#[test]
fn a_grpc_interface_needs_a_mesh_route() {
    let url = "http://order-grpc.example.com:9000";
    let yaml = file(&[entry("a", "GRPC", url, "")]);
    let mesh: GrpcMeshConfig = serde_json::from_value(serde_json::json!({
        "endpoint": "http://127.0.0.1:12220/",
        "routes": [{"interface_url": url, "target_app": "order", "service_name": "order.A2A"}],
    }))
    .unwrap();
    let registry = YamlAgentResolver::from_yaml(&yaml, &guard(), Some(&mesh), no_env).unwrap();
    assert_eq!(registry.len(), 1);
}

#[tokio::test]
async fn the_registry_is_read_from_a_file() {
    let dir = std::env::temp_dir().join(format!("yaml-registry-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("agents.yaml");
    std::fs::write(&path, file(&[entry("a", "HTTP+JSON", "https://agents.example.com/a", "")])).unwrap();
    let registry = YamlAgentResolver::load(&path, &guard(), None).unwrap();
    assert!(registry.resolve(&authority_for_binding(&binding("a"))).await.is_ok());
    assert_eq!(
        YamlAgentResolver::load(&dir.join("missing.yaml"), &guard(), None).unwrap_err(),
        "agent_registry_unreadable"
    );
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn errors_never_quote_values_from_the_file() {
    let secret = "sk-live-XYZ";
    let cases = [
        file(&[entry("a", "JSONRPC", "https://agents.example.com/a", "").replace(r#"owners: ["410025"]"#, &format!("owners: {secret}"))]),
        file(&[entry("a", "JSONRPC", "https://agents.example.com/a", "").replace("visibility: protected", &format!("visibility: {secret}"))]),
        file(&[entry(&format!("' {secret}'"), "JSONRPC", "https://agents.example.com/a", "")]),
    ];
    for yaml in cases {
        let error = YamlAgentResolver::from_yaml(&yaml, &guard(), None, no_env).unwrap_err();
        assert!(!error.contains(secret), "{error}");
    }
}

#[tokio::test]
async fn a_non_streaming_agent_gets_no_streaming_only_extension() {
    let yaml = file(&[entry("a", "JSONRPC", "https://agents.example.com/a", "").replace("streaming: true", "streaming: false")]);
    let registry = YamlAgentResolver::from_yaml(&yaml, &guard(), None, no_env).unwrap();
    let agent = registry.resolve(&authority_for_binding(&binding("a"))).await.unwrap();
    assert_eq!(agent.interface.update_mode, A2aUpdateMode::Poll);
    assert!(agent.extensions.is_empty(), "the tools extension is streaming-only");
}
