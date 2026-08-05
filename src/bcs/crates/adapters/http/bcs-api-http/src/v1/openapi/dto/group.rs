use bcs_service_api::application::v1::{
    BotFinalDelivery, ChatConfiguration, CollaborationConfiguration, CreateCollaborationGroup,
    CreateDirectMessageGroup, CreateGroupSpec, CreateParticipant, GroupDeliveryPolicy,
    GroupKindFilter, GroupPatch, GroupStrategy, GroupVisibility, ManagerWorkerConfiguration,
    MembershipFilter, ParticipantRole, StateMachineConfiguration, StateMachineDefinition,
    StateMachineDefinitionContent, StateMachineParticipantBinding,
};
use serde::{Deserialize, Deserializer, de::Error as _};

fn default_limit() -> u64 {
    20
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MembershipQuery {
    All,
    Direct,
    SessionOnly,
}

impl Default for MembershipQuery {
    fn default() -> Self {
        Self::All
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
    #[serde(default)]
    pub membership: MembershipQuery,
    #[serde(default)]
    pub kind: KindQuery,
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
            MembershipQuery::All => MembershipFilter::All,
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
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AddParticipantRequest {
    pub actor_id: String,
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

pub(crate) fn deserialize_present_non_null<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
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
        driver_bot_uuid: String,
        participants: Vec<ParticipantRequest>,
        collaboration: CollaborationRequest,
    },
    Dm {
        target_actor_id: String,
        name: Option<String>,
        context: Option<String>,
    },
}

impl From<CreateGroupRequest> for CreateGroupSpec {
    fn from(value: CreateGroupRequest) -> Self {
        match value {
            CreateGroupRequest::Normal {
                name,
                context,
                driver_bot_uuid,
                participants,
                collaboration,
            } => Self::Collaboration(CreateCollaborationGroup {
                name,
                context,
                driver_bot_uuid,
                visibility: GroupVisibility::Private,
                participants: participants
                    .into_iter()
                    .map(|participant| CreateParticipant {
                        actor_id: participant.actor_id,
                        role: participant.role,
                    })
                    .collect(),
                collaboration: collaboration.into(),
            }),
            CreateGroupRequest::Dm {
                target_actor_id,
                name,
                context,
            } => Self::DirectMessage(CreateDirectMessageGroup {
                name,
                context,
                target_actor_id,
            }),
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateGroupRequest {
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub name: Option<String>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub visibility: Option<GroupVisibility>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub delivery_policy: Option<DeliveryPolicyRequest>,
}

impl From<UpdateGroupRequest> for GroupPatch {
    fn from(value: UpdateGroupRequest) -> Self {
        Self {
            name: value.name,
            visibility: value.visibility,
            delivery_policy: value.delivery_policy.map(|policy| GroupDeliveryPolicy {
                bot_final_delivery: policy.bot_final_delivery,
            }),
        }
    }
}
