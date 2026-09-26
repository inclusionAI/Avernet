use bcs_service_api::application::v1::{
    ApplicationError, BotFinalDelivery, ChatConfiguration, CollaborationConfiguration,
    CreateCollaborationGroup, CreateDirectMessageGroup, CreateGroupSpec, CreateParticipant,
    GroupDeliveryPolicy, GroupKindFilter, GroupPatch, GroupStrategy, GroupVisibility,
    HumanMentionNotifyMode, InlineGroupEventSubscriptionRequest, ManagerWorkerConfiguration,
    MembershipFilter, OpeningMessage, ParticipantMode, ParticipantRole, StateMachineConfiguration,
    StateMachineDefinition, StateMachineDefinitionContent, StateMachineParticipantBinding,
    MessageViewScope,
};
use serde::{Deserialize, Deserializer, de::Error as _};

fn default_limit() -> u64 {
    20
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MembershipQuery {
    Direct,
    SessionOnly,
}

impl Default for MembershipQuery {
    fn default() -> Self {
        Self::Direct
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum KindQuery {
    Normal,
    Dm,
    All,
}

impl Default for KindQuery {
    fn default() -> Self {
        Self::Normal
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListGroupsQuery {
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_limit")]
    pub limit: u64,
    pub q: Option<String>,
    pub visibility: Option<GroupVisibility>,
    pub view_bot_id: Option<String>,
    #[serde(default)]
    pub membership: MembershipQuery,
    #[serde(default)]
    pub kind: KindQuery,
    pub strategy: Option<GroupStrategy>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListPublicGroupsQuery {
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_limit")]
    pub limit: u64,
    pub q: Option<String>,
    pub strategy: Option<GroupStrategy>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeleteGroupQuery {
    #[serde(default)]
    pub acting_bot_id: Option<String>,
}

impl ListGroupsQuery {
    pub fn membership_filter(&self) -> MembershipFilter {
        match self.membership {
            MembershipQuery::Direct => MembershipFilter::Direct,
            MembershipQuery::SessionOnly => MembershipFilter::SessionOnly,
        }
    }

    pub fn kind_filter(&self) -> GroupKindFilter {
        match self.kind {
            KindQuery::Normal => GroupKindFilter::Normal,
            KindQuery::Dm => GroupKindFilter::Dm,
            KindQuery::All => GroupKindFilter::All,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ParticipantRequest {
    pub actor_id: String,
    pub role: ParticipantRole,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub message_view_scope: Option<MessageViewScope>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AddParticipantRequest {
    pub actor_id: String,
    #[serde(default)]
    pub message_view_scope: Option<MessageViewScope>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateParticipantRequest {
    #[serde(default)]
    pub mode: Option<ParticipantMode>,
    #[serde(default)]
    pub message_view_scope: Option<MessageViewScope>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeliveryPolicyRequest {
    pub bot_final_delivery: BotFinalDelivery,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DefinitionContentRequest {
    pub content_yaml: String,
}

pub(crate) fn deserialize_present_non_null<'de, D, T>(
    deserializer: D,
) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    T::deserialize(deserializer).map(Some)
}

fn deserialize_non_empty_vec<'de, D, T>(deserializer: D) -> Result<Vec<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    let values = Vec::<T>::deserialize(deserializer)?;
    if values.is_empty() {
        return Err(D::Error::custom("must contain at least one item"));
    }
    Ok(values)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ParticipantBindingRequest {
    pub binding: String,
    #[serde(deserialize_with = "deserialize_non_empty_vec")]
    pub actor_ids: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "strategy", rename_all = "snake_case", deny_unknown_fields)]
pub enum CollaborationRequest {
    Chat {
        #[serde(default)]
        delivery_policy: Option<DeliveryPolicyRequest>,
    },
    ManagerWorker {},
    StateMachine {
        definition: DefinitionContentRequest,
        participant_bindings: Vec<ParticipantBindingRequest>,
    },
}

impl From<CollaborationRequest> for CollaborationConfiguration {
    fn from(value: CollaborationRequest) -> Self {
        match value {
            CollaborationRequest::Chat { delivery_policy } => Self::Chat(ChatConfiguration {
                delivery_policy: GroupDeliveryPolicy {
                    bot_final_delivery: delivery_policy
                        .map(|policy| policy.bot_final_delivery)
                        .unwrap_or(BotFinalDelivery::SendToDriver),
                },
            }),
            CollaborationRequest::ManagerWorker {} => {
                Self::ManagerWorker(ManagerWorkerConfiguration::default())
            }
            CollaborationRequest::StateMachine {
                definition,
                participant_bindings,
            } => Self::StateMachine(StateMachineConfiguration {
                definition: StateMachineDefinition::Content(StateMachineDefinitionContent {
                    content_yaml: definition.content_yaml,
                }),
                participant_bindings: participant_bindings
                    .into_iter()
                    .map(|binding| StateMachineParticipantBinding {
                        binding: binding.binding,
                        actor_ids: binding.actor_ids,
                    })
                    .collect(),
            }),
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(tag = "group_kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum CreateGroupRequest {
    Normal {
        name: Option<String>,
        context: Option<String>,
        #[serde(default)]
        opening_message: Option<OpeningMessage>,
        driver_bot_uuid: String,
        participants: Vec<ParticipantRequest>,
        collaboration: CollaborationRequest,
        originator: Option<String>,
        #[serde(default)]
        event_subscriptions: Vec<InlineGroupEventSubscriptionRequest>,
    },
    Dm {
        target_actor_id: String,
        name: Option<String>,
        context: Option<String>,
        #[serde(default)]
        opening_message: Option<OpeningMessage>,
        #[serde(default)]
        event_subscriptions: Vec<InlineGroupEventSubscriptionRequest>,
    },
}

impl CreateGroupRequest {
    pub fn into_parts(
        self,
    ) -> Result<(CreateGroupSpec, Vec<InlineGroupEventSubscriptionRequest>), ApplicationError> {
        Ok(match self {
            CreateGroupRequest::Normal {
                name,
                context,
                opening_message,
                driver_bot_uuid,
                participants,
                collaboration,
                originator,
                event_subscriptions,
            } => (
                CreateGroupSpec::Collaboration(CreateCollaborationGroup {
                    name,
                    context,
                    opening_message,
                    driver_bot_uuid,
                    visibility: GroupVisibility::Private,
                    participants: participants
                        .into_iter()
                        .map(|participant| CreateParticipant {
                            actor_id: participant.actor_id,
                            role: participant.role,
                            tags: participant.tags,
                            message_view_scope: participant.message_view_scope,
                        })
                        .collect(),
                    collaboration: collaboration.into(),
                    originator,
                }),
                event_subscriptions,
            ),
            CreateGroupRequest::Dm {
                target_actor_id,
                name,
                context,
                opening_message,
                event_subscriptions,
            } => {
                if opening_message.is_some() {
                    return Err(ApplicationError::invalid(
                        "invalid_opening_message",
                        "opening_message is not supported for DM Groups",
                    ));
                }
                (
                    CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
                        name,
                        context,
                        target_actor_id,
                    }),
                    event_subscriptions,
                )
            }
        })
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateGroupRequest {
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub name: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub context: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_nullable")]
    pub opening_message: Option<Option<OpeningMessage>>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub visibility: Option<GroupVisibility>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub delivery_policy: Option<DeliveryPolicyRequest>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub human_mention_notify_mode: Option<HumanMentionNotifyMode>,
}

fn deserialize_present_nullable<'de, D, T>(deserializer: D) -> Result<Option<Option<T>>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer).map(Some)
}

impl From<UpdateGroupRequest> for GroupPatch {
    fn from(value: UpdateGroupRequest) -> Self {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opening_message_patch_preserves_omitted_null_and_value() {
        let omitted: UpdateGroupRequest =
            serde_json::from_value(serde_json::json!({ "name": "renamed" })).expect("omitted");
        assert_eq!(omitted.opening_message, None);

        let cleared: UpdateGroupRequest =
            serde_json::from_value(serde_json::json!({ "opening_message": null })).expect("null");
        assert_eq!(cleared.opening_message, Some(None));

        let configured: UpdateGroupRequest = serde_json::from_value(serde_json::json!({
            "opening_message": "Run {{bcs.run_id}}"
        }))
        .expect("value");
        assert_eq!(
            configured.opening_message,
            Some(Some(OpeningMessage::Text("Run {{bcs.run_id}}".to_string())))
        );
    }

    #[test]
    fn human_mention_notify_mode_parses_present_omitted_and_rejects_null_or_invalid() {
        let configured: UpdateGroupRequest = serde_json::from_value(serde_json::json!({
            "human_mention_notify_mode": "driver_bot_only"
        }))
        .expect("valid notify mode");
        assert_eq!(
            configured.human_mention_notify_mode,
            Some(HumanMentionNotifyMode::DriverBotOnly)
        );

        let omitted: UpdateGroupRequest = serde_json::from_value(serde_json::json!({
            "name": "renamed"
        }))
        .expect("omitted notify mode");
        assert_eq!(omitted.human_mention_notify_mode, None);

        assert!(serde_json::from_value::<UpdateGroupRequest>(serde_json::json!({
            "human_mention_notify_mode": null
        }))
        .is_err());
        assert!(serde_json::from_value::<UpdateGroupRequest>(serde_json::json!({
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
            let request: UpdateGroupRequest = serde_json::from_value(serde_json::json!({
                "human_mention_notify_mode": wire
            }))
            .expect("valid notify mode");
            let patch: GroupPatch = request.into();
            assert_eq!(patch.human_mention_notify_mode, Some(expected));
            assert!(!patch.is_empty());
        }

        let omitted: UpdateGroupRequest = serde_json::from_value(serde_json::json!({
            "name": "renamed"
        }))
        .expect("omitted notify mode");
        let omitted: GroupPatch = omitted.into();
        assert_eq!(omitted.human_mention_notify_mode, None);
        assert!(!omitted.is_empty());

        let notify_only = GroupPatch {
            human_mention_notify_mode: Some(HumanMentionNotifyMode::None),
            ..Default::default()
        };
        assert!(!notify_only.is_empty());
    }

    #[test]
    fn mode_only_group_patch_is_not_empty() {
        let patch = GroupPatch {
            human_mention_notify_mode: Some(HumanMentionNotifyMode::All),
            ..Default::default()
        };
        assert!(!patch.is_empty());
        assert!(GroupPatch::default().is_empty());
    }
}
