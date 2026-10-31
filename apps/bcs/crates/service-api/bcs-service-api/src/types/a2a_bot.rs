//! Bot-owned AgentCard snapshots. Routes and credentials are derived, not stored.
use serde::{Deserialize, Serialize};
use serde_json::Value;
pub use bcs_domain::a2a::{A2aBinding, A2aExtensionDeclaration, A2aInterface, A2aUpdateMode, select_a2a_interface};

/// Exactly one explicit source. Inline content has no network refresh URL.
#[derive(Clone, Debug)]
pub enum A2aCardSource { Url(String), Inline(Value) }
impl From<String> for A2aCardSource {fn from(value:String)->Self {Self::Url(value)}}
impl From<&str> for A2aCardSource {fn from(value:&str)->Self {Self::Url(value.into())}}
impl A2aCardSource {
    pub fn from_parts(url: Option<String>, card: Option<Value>) -> crate::ServiceResult<Option<Self>> {
        match (url,card) {
            (Some(url),None) if !url.trim().is_empty() && url==url.trim()=>Ok(Some(Self::Url(url))),
            (None,Some(card)) if card.is_object()=>Ok(Some(Self::Inline(card))),
            (None,None)=>Ok(None),
            _=>Err(crate::ServiceError::InvalidOperation {message:"provide exactly one of agent_card_url or agent_card object".into(),request_id:None}),
        }
    }
}
/// Small availability projection. No Card or credential value.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct A2aBotAvailability { pub bot_uuid:String, pub provider_id:String, pub provider_bot_ref:String, pub ready:bool, pub interfaces:Value }
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct A2aBotRecord {
    pub bot_uuid:String,
    pub provider_id:String,
    pub provider_bot_ref:String,
    pub agent_card_url:Option<String>,
    pub agent_card:Value,
    pub revision:u64,
}
/// Validated declaration passed to atomic Bot creation, not a persistence format.
#[derive(Clone, Debug, PartialEq)]
pub struct A2aRegistrationCard {
    pub agent_card_url:Option<String>,
    pub agent_card:Value,
    pub interface:A2aInterface,
    pub registered_at_ms:i64,
}
