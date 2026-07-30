use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// Context for a proposal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProposalContext {
    pub user_query: Option<String>,
    pub detected_gap: Option<String>,
    pub relevant_history: Vec<String>,
}

/// Response from proposal evaluation.
#[derive(Debug, Serialize, Deserialize)]
pub struct ProposalResponse {
    pub proposal_created: bool,
    /// Mode is deprecated - may not be present in new responses
    #[serde(default)]
    pub mode: String,
    pub driver_bot: String,
    pub participants: Vec<String>,
    pub member_intros: String,
    pub confirm_url: String,
    pub expires_in_seconds: u64,
    pub message: String,
}

/// Response from proposal confirmation.
#[derive(Debug, Serialize, Deserialize)]
pub struct ConfirmProposalResponse {
    pub created: bool,
    pub group_id: String,
    /// Mode is deprecated - may not be present in new responses
    #[serde(default)]
    pub mode: Option<String>,
    pub driver_bot: String,
    pub participants: Vec<String>,
    /// Chat page URL (present when botchat_url is configured on server).
    #[serde(default)]
    pub chat_url: Option<String>,
    /// Initial BCS session created for the group.
    #[serde(default)]
    pub session_id: Option<String>,
}

/// Participant info for group creation.
#[derive(Debug, Serialize, Deserialize)]
pub struct ParticipantInfo {
    pub bot_uuid: String,
    pub role: Option<String>,
}

/// Participant slot binding for state-machine group creation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ParticipantBindingInfo {
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub bot_ids: Vec<String>,
}

/// Request to create a group.
#[derive(Debug, Serialize, Deserialize)]
pub struct CreateGroupRequest {
    pub id: Option<String>,
    pub label: Option<String>,
    /// Mode is deprecated - kept for backward compatibility, defaults to "agent"
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub driver_bot: Option<String>,
    #[serde(default)]
    pub participants: Vec<ParticipantInfo>,
    /// Group-scoped state-machine participant slot bindings.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub participant_bindings: BTreeMap<String, ParticipantBindingInfo>,
    /// Target actor for `group_kind=dm` creation. Preferred over legacy
    /// `participants[0].bot_uuid` when creating Human↔Bot DMs.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_actor_id: Option<String>,
    /// Routing policy for the group (optional, defaults to Hybrid + SendToDriver).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub routing_policy: Option<serde_json::Value>,
    /// User-provided group context (optional description of collaboration goal/background).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<String>,
    /// Group topic (sets the group label as "Group: {topic}").
    #[serde(skip_serializing_if = "Option::is_none")]
    pub topic: Option<String>,
    /// Group kind: "normal" (default) or "dm" (1:1 direct message).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub group_kind: Option<String>,
    /// Service-as-a-Group configuration. None for regular groups.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub service_spec: Option<serde_json::Value>,
    /// Group strategy: "chat" (default), "manager_worker", or "state_machine".
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group_strategy: Option<String>,
    /// Actor (bot_uuid or human_xxx) that initiated group creation.
    /// Defaults to driver_bot when not specified (backward compatible).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub originator: Option<String>,
    /// Optional CollaborationDefinition YAML to persist and bind as the
    /// group's default state-machine definition.
    #[serde(
        default,
        alias = "definition_yaml",
        skip_serializing_if = "Option::is_none"
    )]
    pub collaboration_definition_yaml: Option<String>,
    /// Whether service invocation session creation should auto-start the
    /// bound state-machine definition. This is persisted in the runtime
    /// binding when `collaboration_definition_yaml` is provided.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub auto_start_on_service_invocation: Option<bool>,
    /// Whether group creation should immediately start the initial
    /// service-invocation run. Defaults to true for backward compatibility.
    /// Clients that must provision group-scoped runtime resources first can
    /// set this to false and explicitly start the returned `session_id`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub start_initial_run: Option<bool>,
    /// Group visibility: "public" or "private" (default). Public groups allow
    /// any actor to create sessions.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub visibility: Option<String>,
}

/// Response from group creation.
#[derive(Debug, Deserialize)]
pub struct CreateGroupResponse {
    /// Group ID. Supports both "id" (current) and "group_id" (legacy pre-prod) field names.
    #[serde(alias = "group_id")]
    pub id: String,
    /// Mode is deprecated - may not be present in new responses
    #[serde(default)]
    pub mode: Option<String>,
    pub driver_bot: String,
    pub participants: Vec<String>,
    /// Chat URL for the group (if botchat_url is configured on server)
    #[serde(default)]
    pub chat_url: Option<String>,
    /// Initial BCS session created for the group. The alias preserves
    /// compatibility with servers that expose the detail field name.
    #[serde(default, alias = "latest_running_session_id")]
    pub session_id: Option<String>,
    /// Group kind returned by the server ("normal" or "dm").
    #[serde(default)]
    pub group_kind: Option<String>,
    /// Canonical pair key for DM groups.
    #[serde(default)]
    pub dm_pair_key: Option<String>,
    /// Whether this request created a new group. For DM creation this is
    /// false when an existing canonical pair is reused.
    #[serde(default)]
    pub created: Option<bool>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn create_group_request_supports_dm_without_driver_bot() {
        let req: CreateGroupRequest = serde_json::from_value(serde_json::json!({
            "group_kind": "dm",
            "target_actor_id": "bot_1",
            "participants": []
        }))
        .expect("dm create request should not require driver_bot");

        assert_eq!(req.driver_bot, None);
        assert_eq!(req.target_actor_id.as_deref(), Some("bot_1"));
        assert_eq!(req.group_kind.as_deref(), Some("dm"));
    }

    #[test]
    fn create_group_response_accepts_legacy_payload() {
        let res: CreateGroupResponse = serde_json::from_value(serde_json::json!({
            "id": "group_1",
            "driver_bot": "bot_a",
            "participants": ["bot_a", "bot_b"]
        }))
        .expect("legacy create response should deserialize");

        assert_eq!(res.created, None);
        assert_eq!(res.group_kind, None);
        assert_eq!(res.session_id, None);
    }

    #[test]
    fn group_creation_responses_expose_initial_session_id() {
        let created: CreateGroupResponse = serde_json::from_value(serde_json::json!({
            "id": "group_1",
            "driver_bot": "bot_a",
            "participants": ["bot_a", "bot_b"],
            "latest_running_session_id": "group_1:initial"
        }))
        .expect("create response session alias should deserialize");
        assert_eq!(created.session_id.as_deref(), Some("group_1:initial"));

        let confirmed: ConfirmProposalResponse = serde_json::from_value(serde_json::json!({
            "created": true,
            "group_id": "group_2",
            "driver_bot": "bot_a",
            "participants": ["bot_a", "bot_b"],
            "session_id": "group_2:initial"
        }))
        .expect("confirm response session id should deserialize");
        assert_eq!(confirmed.session_id.as_deref(), Some("group_2:initial"));
    }

    #[test]
    fn create_group_response_exposes_dm_created_semantics() {
        let res: CreateGroupResponse = serde_json::from_value(serde_json::json!({
            "id": "dm_1",
            "driver_bot": "bot_a",
            "participants": ["human_1", "bot_a"],
            "group_kind": "dm",
            "dm_pair_key": "bot_a|human_1",
            "created": false
        }))
        .expect("dm create response should deserialize");

        assert_eq!(res.group_kind.as_deref(), Some("dm"));
        assert_eq!(res.dm_pair_key.as_deref(), Some("bot_a|human_1"));
        assert_eq!(res.created, Some(false));
    }
}

/// Request to evaluate a proposal.
#[derive(Debug, Serialize)]
pub struct EvaluateProposalRequest {
    pub topic: String,
    pub suggested_participants: Vec<String>,
    pub suggested_driver: Option<String>,
    pub context: Option<ProposalContext>,
}
