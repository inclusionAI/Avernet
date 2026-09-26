//! HTTP request DTOs for the legacy Group routes.

use super::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum GroupKindFilter {
    #[default]
    Normal,
    Dm,
    All,
}

#[derive(Debug, Deserialize)]
pub struct ListGroupsQuery {
    #[serde(default)]
    pub offset: Option<u64>,
    #[serde(default)]
    pub limit: Option<u64>,
    #[serde(default)]
    pub group_kind: GroupKindFilter,
    #[serde(default)]
    pub visibility: Option<String>,
    #[serde(default)]
    pub label: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ListBotGroupsQuery {
    #[serde(default)]
    pub offset: Option<u64>,
    #[serde(default)]
    pub limit: Option<u64>,
    #[serde(default)]
    pub group_kind: GroupKindFilter,
    #[serde(default)]
    pub q: Option<String>,
    /// Include groups where the actor participates only through a session.
    /// Defaults to true for backward compatibility.
    #[serde(default = "default_include_session_groups")]
    pub include_session_groups: bool,
}

pub(crate) fn default_include_session_groups() -> bool {
    true
}

pub(crate) fn formal_only_group_query(mut query: ListBotGroupsQuery) -> ListBotGroupsQuery {
    query.include_session_groups = false;
    query
}

#[cfg(test)]
mod list_my_groups_query_tests {
    use super::{GroupKindFilter, ListBotGroupsQuery, formal_only_group_query};

    #[test]
    fn my_groups_forces_session_only_groups_off() {
        let query = formal_only_group_query(ListBotGroupsQuery {
            offset: Some(4),
            limit: Some(5),
            group_kind: GroupKindFilter::default(),
            q: Some("topic".to_string()),
            include_session_groups: true,
        });

        assert!(!query.include_session_groups);
        assert_eq!(query.offset, Some(4));
        assert_eq!(query.limit, Some(5));
        assert_eq!(query.q.as_deref(), Some("topic"));
    }
}

#[derive(Debug, Deserialize)]
pub struct DeleteSessionQuery {
    pub bot_id: String,
}

#[derive(Debug, Deserialize)]
pub struct AddMemberRequest {
    pub bot_uuid: String,
}

#[derive(Debug, Deserialize)]
pub struct UpdateGroupStatusRequest {
    pub status: String,
    #[serde(default)]
    pub reason: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct UpdateLabelRequest {
    #[serde(default)]
    pub label: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LegacyGroupDeliveryPolicyRequest {
    pub bot_final_delivery: BotFinalDelivery,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LegacyUpdateGroupRequest {
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub name: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub context: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_nullable")]
    pub opening_message: Option<Option<bcs_service_api::types::OpeningMessage>>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub visibility: Option<GroupVisibility>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub delivery_policy: Option<LegacyGroupDeliveryPolicyRequest>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub human_mention_notify_mode: Option<bcs_service_api::HumanMentionNotifyMode>,
}

pub(crate) fn deserialize_present_nullable<'de, D, T>(deserializer: D) -> Result<Option<Option<T>>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer).map(Some)
}

pub(crate) fn deserialize_present_non_null<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    T::deserialize(deserializer).map(Some)
}

impl From<LegacyUpdateGroupRequest> for GroupPatch {
    fn from(value: LegacyUpdateGroupRequest) -> Self {
        Self {
            name: value.name,
            context: value.context,
            opening_message: value.opening_message,
            visibility: value.visibility,
            delivery_policy: value.delivery_policy.map(|policy| GroupDeliveryPolicy {
                bot_final_delivery: policy.bot_final_delivery,
            }),
            human_mention_notify_mode: value.human_mention_notify_mode,
        }
    }
}

#[derive(Debug, Deserialize, Serialize)]
pub struct UpdateRoutingPolicyRequest {
    #[serde(default)]
    pub mode: Option<RoutingMode>,
    #[serde(default)]
    pub default_bot_final_delivery: Option<DefaultDelivery>,
    #[serde(default)]
    pub sender_routes: Option<HashMap<String, Vec<String>>>,
}

#[derive(Debug, Deserialize)]
pub struct PutParticipantModeRequest {
    pub mode: ParticipantMode,
    #[serde(default)]
    pub message_view_scope: Option<MessageViewScope>,
}

#[derive(Debug, Deserialize)]
pub struct PatchGroupCollaborationDefinitionRequest {
    pub base_definition: CollaborationDefinitionRef,
    pub definition_yaml: String,
    #[serde(default)]
    pub participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
}

#[derive(Debug, Deserialize)]
pub struct UpgradeGroupCollaborationDefinitionRequest {
    pub base_definition: CollaborationDefinitionRef,
    pub target_definition: CollaborationDefinitionRef,
    #[serde(default)]
    pub participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
}

#[derive(Debug, Deserialize)]
pub struct UpdateVisibilityRequest {
    pub visibility: String,
}

#[derive(Debug, Deserialize)]
pub struct PatchGroupSettingsRequest {
    #[serde(default)]
    pub service_spec: Option<Option<ServiceSpec>>,
}

#[cfg(test)]
mod legacy_update_group_request_tests {
    use super::*;
    use bcs_service_api::HumanMentionNotifyMode;

    #[test]
    fn human_mention_notify_mode_parses_present_omitted_and_rejects_null_or_invalid() {
        let configured: LegacyUpdateGroupRequest = serde_json::from_value(serde_json::json!({
            "human_mention_notify_mode": "driver_bot_only"
        }))
        .expect("valid notify mode");
        assert_eq!(
            configured.human_mention_notify_mode,
            Some(HumanMentionNotifyMode::DriverBotOnly)
        );

        let omitted: LegacyUpdateGroupRequest = serde_json::from_value(serde_json::json!({
            "name": "renamed"
        }))
        .expect("omitted notify mode");
        assert_eq!(omitted.human_mention_notify_mode, None);

        assert!(serde_json::from_value::<LegacyUpdateGroupRequest>(serde_json::json!({
            "human_mention_notify_mode": null
        }))
        .is_err());
        assert!(serde_json::from_value::<LegacyUpdateGroupRequest>(serde_json::json!({
            "human_mention_notify_mode": "invalid"
        }))
        .is_err());
    }

    #[test]
    fn human_mention_notify_mode_forwards_every_variant_into_group_patch() {
        for (wire, expected) in [
            ("driver_bot_only", HumanMentionNotifyMode::DriverBotOnly),
            ("all", HumanMentionNotifyMode::All),
            ("none", HumanMentionNotifyMode::None),
        ] {
            let request: LegacyUpdateGroupRequest = serde_json::from_value(serde_json::json!({
                "human_mention_notify_mode": wire
            }))
            .expect("valid notify mode");
            let patch: GroupPatch = request.into();
            assert_eq!(patch.human_mention_notify_mode, Some(expected));
            assert!(!patch.is_empty());
        }

        let omitted: LegacyUpdateGroupRequest = serde_json::from_value(serde_json::json!({
            "name": "renamed"
        }))
        .expect("omitted notify mode");
        let patch: GroupPatch = omitted.into();
        assert_eq!(patch.human_mention_notify_mode, None);
        assert!(!patch.is_empty());
    }
}
