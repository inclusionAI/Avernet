//! Deployment supplies policy-checked frozen Card/endpoint and credential-bound HTTP client.
//! No discovery, credential lookup or automatic transport fallback occurs here.
use std::sync::Arc;
use bcn_runtime_model::*;
use bcn_runtime_api::port::AgentConnector;
use bcn_agent_connectors::{a2a::A2aConnector,bcn_sse::{ProviderConfig,ProviderConnector}};
use bcs_a2a_http::card::SelectedCard;
use bcs_network_tracing::ClientWithDefaults;
pub enum ConnectorSelection {
    A2a { card:SelectedCard, http:ClientWithDefaults<reqwest::Client> },
    Provider { config:ProviderConfig, http:ClientWithDefaults<reqwest::Client> },
    Local(Arc<dyn AgentConnector>),
}
pub fn connector(binding:FrozenTargetBinding,selection:ConnectorSelection)->ConnectorResult<(FrozenTargetBinding,Arc<dyn AgentConnector>)>{
    let client:Arc<dyn AgentConnector>=match selection {
        ConnectorSelection::A2a{card,http}=>Arc::new(A2aConnector::new(binding.clone(),card,http)?),
        ConnectorSelection::Provider{config,http}=>Arc::new(ProviderConnector::new(binding.clone(),config,http)?),
        ConnectorSelection::Local(client)=>client,
    };
    if client.capabilities()!=binding.capabilities{return Err(ConnectorError::NotSent("binding capabilities disagree".into()))}
    Ok((binding,client))
}

/// Rebuild only from the verified retained artifact. Credentials are resolved
/// separately; redirects and retry-time route discovery are never enabled.
pub async fn from_manifest(manifest:&crate::manifest::TransportManifest,credentials:&dyn bcn_runtime_api::port::CredentialResolver,allowed_urls:&[String])
    ->RuntimeResult<Vec<(FrozenTargetBinding,Arc<dyn AgentConnector>)>> {
    manifest.validate()?;
    let mut clients=Vec::new();
    for entry in &manifest.entries {
        let mut headers=reqwest::header::HeaderMap::new();
        if let Some(reference)=&entry.binding.credential_ref {
            let secret=credentials.resolve(&entry.binding.target.scope,reference).await?;
            let token=std::str::from_utf8(secret.expose()).map_err(|_|RuntimeError::InvalidInput("invalid transport credential".into()))?;
            let mut value=reqwest::header::HeaderValue::from_str(&format!("Bearer {token}")).map_err(|_|RuntimeError::InvalidInput("invalid transport credential".into()))?;
            value.set_sensitive(true);headers.insert(reqwest::header::AUTHORIZATION,value);
        }
        let http=ClientWithDefaults::new(reqwest::Client::builder().redirect(reqwest::redirect::Policy::none()).build().map_err(|_|RuntimeError::Unavailable("HTTP client initialization".into()))?,headers);
        let selection=match &entry.route {
            crate::manifest::TransportRoute::A2a{card}=>{
                let card=bcs_a2a_http::card::select_card(card.clone(),&[],entry.binding.credential_ref.is_some(),|url|allowed_urls.iter().any(|allowed|allowed==url))
                    .map_err(|_|RuntimeError::InvalidInput("frozen Card or route rejected".into()))?;
                ConnectorSelection::A2a{card,http}
            },
            crate::manifest::TransportRoute::Provider{endpoint,provider_id,provider_bot_ref,group_id,timeout_ms}=>{
                if !allowed_urls.contains(endpoint) {return Err(RuntimeError::AccessDenied)}
                let bot=serde_json::from_value(serde_json::json!({"provider_id":provider_id,"provider_bot_ref":provider_bot_ref}))
                    .map_err(|_|RuntimeError::InvalidInput("Provider identity".into()))?;
                ConnectorSelection::Provider{config:ProviderConfig{endpoint:endpoint.clone(),bot,sender:None,group_id:group_id.clone(),timeout_ms:*timeout_ms},http}
            }
        };
        clients.push(connector(entry.binding.clone(),selection).map_err(|_|RuntimeError::InvalidInput("frozen connector configuration".into()))?);
    }
    Ok(clients)
}
