//! Deployment-owned MOSN routing; a Card cannot supply routing headers.
use serde::{Deserialize, Serialize};
use tonic::{metadata::{MetadataMap, MetadataValue}, transport::Endpoint};
use bcs_service_api::{ServiceError, ServiceResult, types::a2a_bot::A2aInterface};
use crate::grpc_client::GrpcClient;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GrpcMeshConfig { pub endpoint: String, pub routes: Vec<GrpcMeshRoute> }

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GrpcMeshRoute {
    /// Exact selected interface URL; never dialed by this adapter.
    pub interface_url: String,
    pub target_app: String,
    pub service_name: String,
    #[serde(default)]
    pub vip_address: Option<String>,
}

fn invalid(message: &str) -> ServiceError {
    ServiceError::InvalidOperation { message: format!("A2A gRPC mesh: {message}"), request_id: None }
}

impl GrpcMeshConfig {
    pub fn validate(&self) -> Result<(), String> {
        let endpoint = reqwest::Url::parse(&self.endpoint).map_err(|_| "invalid MOSN endpoint")?;
        // Literal loopback prevents DNS rebinding. Local MOSN is the only dial
        // target; Card endpoints cannot bypass service discovery or receive credentials.
        let host = endpoint.host_str().unwrap_or("").trim_matches(['[', ']']);
        if endpoint.scheme() != "http" || !host.parse::<std::net::IpAddr>().is_ok_and(|ip| ip.is_loopback())
            || endpoint.port().is_none() || endpoint.path() != "/" || endpoint.query().is_some()
            || endpoint.fragment().is_some() || !endpoint.username().is_empty() || endpoint.password().is_some() {
            return Err("MOSN endpoint must be a literal loopback HTTP origin with a port".into());
        }
        if self.routes.is_empty() || self.routes.len() > 128 { return Err("configure 1..128 explicit MOSN routes".into()); }
        let mut urls = std::collections::HashSet::new();
        for route in &self.routes {
            let url = reqwest::Url::parse(&route.interface_url).map_err(|_| "invalid interface URL")?;
            if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none()
                || !url.username().is_empty() || url.password().is_some() || url.query().is_some() || url.fragment().is_some()
                || !urls.insert(&route.interface_url) { return Err("invalid or duplicate gRPC interface URL".into()); }
            for value in [&route.target_app, &route.service_name].into_iter().chain(route.vip_address.iter()) {
                if value.is_empty() || value.len() > 1024 || !value.is_ascii()
                    || value.bytes().any(|byte|byte.is_ascii_whitespace()) || MetadataValue::try_from(value.as_str()).is_err() {
                    return Err("invalid MOSN routing metadata".into());
                }
            }
        }
        Ok(())
    }

    pub fn supports(&self, url: &str) -> bool { self.routes.iter().any(|route| route.interface_url == url) }

    pub fn fingerprint(&self, url: &str) -> ServiceResult<String> {
        use sha2::Digest;
        let route=self.routes.iter().find(|route|route.interface_url==url).ok_or_else(||invalid("route unavailable"))?;
        Ok(hex::encode(sha2::Sha256::digest(serde_json::to_vec(route)?)))
    }

    pub fn client(&self, interface: &A2aInterface, bearer: &str, extensions: &str) -> ServiceResult<GrpcClient> {
        self.validate().map_err(|_| invalid("invalid deployment configuration"))?;
        let route = self.routes.iter().find(|route| route.interface_url == interface.url)
            .ok_or_else(|| invalid("no configured route for selected interface"))?;
        let mut headers = MetadataMap::new();
        let mut credential = MetadataValue::try_from(format!("Bearer {bearer}"))
            .map_err(|_| invalid("invalid credential"))?;
        credential.set_sensitive(true); headers.insert("authorization", credential);
        headers.insert("tri-type", MetadataValue::from_static("rpc"));
        headers.insert("mesh_vip_only", MetadataValue::from_static("false"));
        headers.insert("mesh_vip_enforce", MetadataValue::from_static("false"));
        headers.insert("sofa_head_target_app", MetadataValue::try_from(route.target_app.as_str()).map_err(|_| invalid("invalid target app"))?);
        headers.insert("sofa_head_target_service", MetadataValue::try_from(route.service_name.as_str()).map_err(|_| invalid("invalid service name"))?);
        if let Some(vip) = &route.vip_address {
            headers.insert("mesh_vip_address", MetadataValue::try_from(vip.as_str()).map_err(|_| invalid("invalid VIP"))?);
        }
        if !extensions.is_empty() {
            headers.insert("a2a-extensions", MetadataValue::try_from(extensions).map_err(|_| invalid("invalid extensions"))?);
        }
        let channel = Endpoint::from_shared(self.endpoint.clone()).map_err(|_| invalid("invalid MOSN endpoint"))?
            .connect_timeout(std::time::Duration::from_secs(5)).connect_lazy();
        Ok(GrpcClient::new(channel, headers, interface.tenant.clone()))
    }
}
