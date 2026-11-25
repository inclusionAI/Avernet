//! A static agent registry read from a YAML file, and the `DistAgentResolver`
//! built on it.
//!
//! MVP stand-in until the long-term resolver is settled (what `AgentBinding`'s
//! `binding_version` and `credential_ref` mean, and which store resolves
//! credentials). Each entry mirrors the payload a Provider sends when its
//! agent registers with BCS, so the same declarations can later move to the
//! real registry unchanged:
//!
//! ```text
//! agents:
//!   - provider_bot_ref: order-agent-001   # stable agent id; AgentBinding.agent
//!     owners: ["410025"]                  # kept for parity, not used here
//!     visibility: protected               # private | protected | public
//!     bearer_env: ORDER_AGENT_TOKEN       # optional: env var holding the token
//!     agent_card:                         # the AgentCard, as registered
//!       name: Order assistant
//!       description: Order, delivery and after-sales status.
//!       version: 1.0.0
//!       supportedInterfaces:
//!         - protocolBinding: JSONRPC      # HTTP+JSON | JSONRPC | GRPC | a custom binding
//!           protocolVersion: "1.0"
//!           url: https://agents.example.com/order/a2a
//!       capabilities: { streaming: true }
//!       defaultInputModes: [text/plain]
//!       defaultOutputModes: [text/plain]
//!       skills: []
//! ```
//!
//! Rules:
//! - Lookup: `AgentBinding.agent` must equal a `provider_bot_ref`;
//!   `binding_version` and `credential_ref` are not interpreted yet.
//! - Every card is validated when the file is loaded, with the same rules as
//!   registration (`crate::card::select_card_with_bindings`): text modes, a
//!   supported interface, required extensions, authentication. A custom
//!   binding (for example one a deployment installs for its RPC framework)
//!   is projected by `bcs_a2a_binding_api::registry()`, so install the
//!   bindings before loading the file. Any invalid entry fails the load.
//! - No secret is stored in the file: `bearer_env` names an environment
//!   variable, read once at load. A card that requires a Bearer scheme needs
//!   it; an anonymous card should omit it.
//! - Extensions sent to the agent are the ones its card declares that this
//!   client implements (today our tools extension, `crate::tools::EXTENSION`).
//! - URLs still pass the outbound guard on every call (`A2aDistAgentClient`).

use super::{DistAgentResolver, ResolvedAgent};
use crate::grpc_mesh::GrpcMeshConfig;
use async_trait::async_trait;
use bcs_dist_model::AgentBinding;
use bcs_route_security::OutboundUrlGuard;
use bcs_service_api::types::a2a_bot::{A2aBinding, A2aInterface, A2aUpdateMode, http_interface_address_allowed};
use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::path::Path;

/// Extensions this client can take part in; a card may declare more.
const SUPPORTED_EXTENSIONS: &[&str] = &[crate::tools::EXTENSION];

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RegistryFile {
    agents: Vec<Entry>,
}

/// One agent, in the shape of a Provider registration plus `bearer_env`.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    provider_bot_ref: String,
    #[serde(default)]
    #[allow(dead_code, reason = "registration parity; not used to resolve")]
    owners: Vec<String>,
    #[serde(default)]
    #[allow(dead_code, reason = "registration parity; not used to resolve")]
    visibility: Option<Visibility>,
    #[serde(default)]
    bearer_env: Option<String>,
    agent_card: Value,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum Visibility {
    Private,
    Protected,
    Public,
}

/// Agents keyed by `provider_bot_ref`, validated and ready to call.
pub struct YamlAgentResolver {
    agents: HashMap<String, ResolvedAgent>,
}

impl std::fmt::Debug for YamlAgentResolver {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut ids: Vec<&String> = self.agents.keys().collect();
        ids.sort();
        f.debug_struct("YamlAgentResolver").field("agents", &ids).finish()
    }
}

impl YamlAgentResolver {
    /// Reads and validates the registry file. Environment variables named by
    /// `bearer_env` are read here, once. `grpc` must be the same mesh
    /// configuration the client uses, so a gRPC interface is accepted only
    /// when the mesh routes it.
    pub fn load(path: &Path, guard: &OutboundUrlGuard, grpc: Option<&GrpcMeshConfig>) -> Result<Self, String> {
        let text = std::fs::read_to_string(path).map_err(|_| "agent_registry_unreadable".to_owned())?;
        Self::from_yaml(&text, guard, grpc, |name| std::env::var(name).ok())
    }

    /// As `load`, from YAML text, with the environment lookup injected.
    /// The error names the entry and the reason, never a token or a URL.
    pub fn from_yaml(
        text: &str,
        guard: &OutboundUrlGuard,
        grpc: Option<&GrpcMeshConfig>,
        env: impl Fn(&str) -> Option<String>,
    ) -> Result<Self, String> {
        let file: RegistryFile =
            serde_yaml::from_str(text).map_err(|error| format!("agent_registry_invalid: {error}"))?;
        let mut agents = HashMap::new();
        for (index, entry) in file.agents.into_iter().enumerate() {
            let id = entry.provider_bot_ref.trim().to_owned();
            let failed = |reason: &str| format!("agent_registry_invalid: agents[{index}] ({id}): {reason}");
            if id.is_empty() || id != entry.provider_bot_ref {
                return Err(failed("provider_bot_ref must be a non-empty id without surrounding spaces"));
            }
            if agents.contains_key(&id) {
                return Err(failed("duplicate provider_bot_ref"));
            }
            let bearer = match &entry.bearer_env {
                None => None,
                Some(name) => {
                    let token = env(name).map(|value| value.trim().to_owned()).filter(|value| !value.is_empty());
                    Some(token.ok_or_else(|| failed("bearer_env names an unset or empty variable"))?)
                }
            };
            let agent = resolved(entry.agent_card, bearer, guard, grpc).map_err(|reason| failed(reason))?;
            agents.insert(id, agent);
        }
        Ok(Self { agents })
    }

    /// Number of registered agents.
    pub fn len(&self) -> usize {
        self.agents.len()
    }

    /// Whether the registry has no agent.
    pub fn is_empty(&self) -> bool {
        self.agents.is_empty()
    }
}

/// Validates one card like a registration and turns it into a callable agent.
fn resolved(
    card: Value,
    bearer: Option<String>,
    guard: &OutboundUrlGuard,
    grpc: Option<&GrpcMeshConfig>,
) -> Result<ResolvedAgent, &'static str> {
    let bindings: &[&str] = if grpc.is_some() {
        &["GRPC", "HTTP+JSON", "JSONRPC", "CUSTOM"]
    } else {
        &["HTTP+JSON", "JSONRPC", "CUSTOM"]
    };
    let selected = crate::card::select_card_with_bindings(card, SUPPORTED_EXTENSIONS, bearer.is_some(), bindings, |target| {
        match target.protocol_binding.as_str() {
            "CUSTOM" => bcs_a2a_binding_api::registry().fingerprint(&target.url).is_ok(),
            "GRPC" => grpc.is_some_and(|mesh| mesh.supports(&target.url)),
            _ => http_interface_address_allowed(&target.url) && guard.validate_configured_http_url(&target.url).is_ok(),
        }
    })
    .map_err(|error| {
        use crate::card::CardError;
        match error {
            CardError::InvalidShape => "agent_card is not a valid AgentCard",
            CardError::TextRequired => "agent_card must accept and produce text/plain",
            CardError::RequiredExtensionUnsupported(_) => "agent_card requires an extension this client does not support",
            CardError::AuthenticationUnsupported => "agent_card requires authentication bearer_env does not provide",
            CardError::InterfaceUnsupported => "agent_card has no interface this deployment can call",
            CardError::AddressRejected => "agent_card interface address is not allowed",
        }
    })?;
    let binding = match selected.interface.protocol_binding.as_str() {
        "CUSTOM" => A2aBinding::Custom,
        "GRPC" => A2aBinding::Grpc,
        "JSONRPC" => A2aBinding::JsonRpc,
        _ => A2aBinding::HttpJson,
    };
    let streaming = selected.raw.pointer("/capabilities/streaming").and_then(Value::as_bool) == Some(true);
    let extensions = selected
        .raw
        .pointer("/capabilities/extensions")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|extension| extension.get("uri").and_then(Value::as_str))
        .filter(|uri| SUPPORTED_EXTENSIONS.contains(uri))
        .map(str::to_owned)
        .collect();
    Ok(ResolvedAgent {
        interface: A2aInterface {
            url: selected.interface.url,
            tenant: selected.interface.tenant,
            protocol_version: selected.interface.protocol_version,
            binding,
            update_mode: if streaming { A2aUpdateMode::Stream } else { A2aUpdateMode::Poll },
        },
        bearer,
        extensions,
    })
}

#[async_trait]
impl DistAgentResolver for YamlAgentResolver {
    async fn resolve(&self, target: &AgentBinding) -> Result<ResolvedAgent, String> {
        self.agents
            .get(&target.agent.0)
            .cloned()
            .ok_or_else(|| "agent_not_registered".to_owned())
    }
}
