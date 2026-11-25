//! Bot-owned AgentCard snapshots. Routes and credentials are derived, not stored.
use serde::{Deserialize, Serialize};
use crate::{ActorStatus, application::v1::UserVisibility};
use serde_json::Value;
pub use bcs_domain::a2a_names::{card_bot_name,valid_provider_name};
pub use bcs_domain::a2a::{A2aBinding, A2aExtensionDeclaration, A2aInterface, A2aUpdateMode, select_a2a_interface, http_interface_address_allowed};


/// Presence-aware AgentId input for the AgentCard management envelope.
///
/// `Omitted` preserves the current dedicated identity on updates, `Null`
/// maps to the SQL-null registration representation, and `Text` preserves
/// the supplied string exactly. Registration also accepts an empty string;
/// no normalization or identity-required rule is applied.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub enum A2aAgentIdentity {
    #[default]
    Omitted,
    Null,
    Text(String),
}

impl A2aAgentIdentity {
    pub fn from_present(value: Option<Option<String>>) -> Self {
        match value {
            None => Self::Omitted,
            Some(None) => Self::Null,
            Some(Some(value)) => Self::Text(value),
        }
    }
    pub fn as_sql_option(&self) -> Option<Option<String>> {
        match self {
            Self::Omitted => None,
            Self::Null => Some(None),
            Self::Text(value) => Some(Some(value.clone())),
        }
    }
}

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
pub struct A2aBotAvailability { pub bot_uuid:String, pub provider_id:String, pub provider_bot_ref:String, pub ready:bool, pub interfaces:Value, pub authentication:Value }
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct A2aBotRecord {
    #[serde(default)]
    pub catalog_labels: super::catalog_labels::CatalogLabels,
    pub bot_uuid:String,
    pub provider_id:String,
    pub provider_bot_ref:String,
    pub agent_card_url:Option<String>,
    pub agent_card:Value,
    pub visibility:A2aRegistrationVisibility,
    pub status:ActorStatus,
    pub user_visibility:UserVisibility,
    pub agent_id:Option<String>,
    pub revision:u64,
}
/// BCN visibility chosen at admission; independent of the remote AgentCard.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum A2aRegistrationVisibility {
    #[default]
    Protected,
    Public,
    Private,
}
impl A2aRegistrationVisibility {
    pub fn as_str(self) -> &'static str {
        match self { Self::Protected => "protected", Self::Public => "public", Self::Private => "private" }
    }
}

/// Validated declaration passed to atomic Bot creation, not a persistence format.
#[derive(Clone, Debug, PartialEq)]
pub struct A2aRegistrationCard {
    pub catalog_labels: super::catalog_labels::CatalogLabels,
    pub visibility: A2aRegistrationVisibility,
    pub agent_id: A2aAgentIdentity,
    pub agent_card_url:Option<String>,
    pub agent_card:Value,
    pub interface:A2aInterface,
    pub registered_at_ms:i64,
}

pub use bcs_domain::a2a_management;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn presence_aware_agent_identity_round_trips_present_and_sql_values() {
        let omitted = A2aAgentIdentity::from_present(None);
        assert_eq!(omitted, A2aAgentIdentity::Omitted);
        assert_eq!(omitted.as_sql_option(), None);

        let sql_null = A2aAgentIdentity::from_present(Some(None));
        assert_eq!(sql_null, A2aAgentIdentity::Null);
        assert_eq!(sql_null.as_sql_option(), Some(None));

        let identity = A2aAgentIdentity::from_present(Some(Some("agent-01".into())));
        assert_eq!(identity, A2aAgentIdentity::Text("agent-01".into()));
        assert_eq!(identity.as_sql_option(), Some(Some("agent-01".into())));
    }
}
