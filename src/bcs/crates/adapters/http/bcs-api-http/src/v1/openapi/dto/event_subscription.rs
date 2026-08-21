use bcs_service_api::application::v1::{
    EventDeliveryStatus, EventPayload, EventSubscriptionDesiredStatus, EventSubscriptionScope,
    EventSubscriptionScopeType, EventSubscriptionStatus, PatchEventSinkInput,
    PatchEventSubscriptionRequest,
};
use serde::{Deserialize, Deserializer};

fn default_limit() -> u32 {
    20
}

fn deserialize_present<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    T::deserialize(deserializer).map(Some)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListEventSubscriptionsQuery {
    pub scope_type: Option<EventSubscriptionScopeType>,
    pub scope_id: Option<String>,
    pub status: Option<EventSubscriptionStatus>,
    pub cursor: Option<String>,
    #[serde(default = "default_limit")]
    pub limit: u32,
}

impl ListEventSubscriptionsQuery {
    pub fn scope(&self) -> Result<Option<EventSubscriptionScope>, &'static str> {
        match (self.scope_type, self.scope_id.clone()) {
            (None, None) => Ok(None),
            (Some(scope_type), Some(id)) => Ok(Some(EventSubscriptionScope { scope_type, id })),
            (Some(_), None) => Err("scope_type requires scope_id"),
            (None, Some(_)) => Err("scope_id requires scope_type"),
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PatchEventSubscriptionBody {
    #[serde(default, deserialize_with = "deserialize_present")]
    pub revision: Option<u64>,
    #[serde(default, deserialize_with = "deserialize_present")]
    pub name: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present")]
    pub event_filters: Option<Vec<String>>,
    #[serde(default, deserialize_with = "deserialize_present")]
    pub payload: Option<EventPayload>,
    #[serde(default, deserialize_with = "deserialize_present")]
    pub sink: Option<PatchEventSinkInput>,
    #[serde(default, deserialize_with = "deserialize_present")]
    pub status: Option<EventSubscriptionDesiredStatus>,
}

impl PatchEventSubscriptionBody {
    pub fn into_patch(self) -> PatchEventSubscriptionRequest {
        PatchEventSubscriptionRequest {
            name: self.name,
            event_filters: self.event_filters,
            payload: self.payload,
            sink: self.sink,
            status: self.status,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeleteEventSubscriptionQuery {
    pub revision: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListEventDeliveriesQuery {
    pub status: Option<EventDeliveryStatus>,
    pub cursor: Option<String>,
    #[serde(default = "default_limit")]
    pub limit: u32,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplayEventDeliveryBody {
    pub replay_request_id: String,
    pub expected_subscription_revision: u64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SkipEventDeliveryBody {
    pub reason: String,
}
