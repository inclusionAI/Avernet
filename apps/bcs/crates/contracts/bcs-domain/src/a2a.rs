//! Protocol, binding and update mode are independent delivery dimensions.
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum A2aBinding { HttpJson, Grpc }

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum A2aUpdateMode { Stream }

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct A2aInterface {
    pub url: String,
    pub tenant: Option<String>,
    pub protocol_version: String,
    pub binding: A2aBinding,
    pub update_mode: A2aUpdateMode,
}

/// Complete declaration passed to the outbound adapter for negotiation. Core
/// projects Card data without interpreting extension-specific parameters.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct A2aExtensionDeclaration {
    pub uri: String,
    #[serde(default)]
    pub required: Option<bool>,
    #[serde(default)]
    pub params: serde_json::Value,
}


/// Select from an already validated Card using current deployment policy.
/// No SDK, networking or credentials enter this domain policy; callers validate
/// a newly supplied Card at the boundary before persisting it.
pub fn select_a2a_interface(
    card:&serde_json::Value, allow:impl Fn(A2aBinding,&str)->bool,
)->Result<A2aInterface,&'static str> {
    let interfaces=card.get("supportedInterfaces").and_then(serde_json::Value::as_array).ok_or("missing supportedInterfaces")?;
    for interface in interfaces {
        if interface.get("protocolVersion").and_then(serde_json::Value::as_str)!=Some("1.0") {continue;}
        let binding=match interface.get("protocolBinding").and_then(serde_json::Value::as_str) {
            Some("HTTP+JSON")=>A2aBinding::HttpJson,Some("GRPC")=>A2aBinding::Grpc,_=>continue,
        };
        let url=interface.get("url").and_then(serde_json::Value::as_str).filter(|url|!url.trim().is_empty()).ok_or("missing interface URL")?;
        if allow(binding,url) {
            let tenant=match interface.get("tenant") {None|Some(serde_json::Value::Null)=>None,
                Some(serde_json::Value::String(v))=>Some(v.clone()),_=>return Err("invalid tenant")};
            return Ok(A2aInterface {url:url.into(),tenant,protocol_version:"1.0".into(),binding,update_mode:A2aUpdateMode::Stream});
        }
    }
    Err("no supported A2A interface")
}
