//! Edge-permission (08-12 A2A authz) pure domain types.
//!
//! Replaces the V1 friend graph (`bcs_friend*` + `bcs_actor_relations`
//! friendship edges) with a unified directed-edge authorization model:
//! `edge_grants` is the single source of truth for friend relationships.
//! See `docs/superpowers/specs/2026-08-18-friend-edge-permission-reform.md`.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::actor::ActorKind;

/// Kind of authorization carried by an edge.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GrantKind {
    /// Edge references a `PermissionProfile` via `grant_ref_id`.
    PermissionProfile,
    /// Edge carries inline `rules`.
    Rules,
}

/// Lifecycle status of an edge grant.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum EdgeStatus {
    /// Active authorization.
    #[default]
    Approved,
    /// Withdrawn; no longer authorizes.
    Revoked,
}

/// Originator activation policy for an edge.
///
/// Friend (default-profile) edges are uniformly `Any` (D7).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum OriginatorPolicyType {
    /// Active for any originator (friend edges).
    #[default]
    Any,
    /// Active only when originator == from_id.
    SameAsFrom,
    /// Active only for a specific originator set (`originator_policy_data`).
    Specific,
    /// Active only when originator is the bot owner.
    Owner,
}

/// A directed authorization edge (A→B): "BCS approved A to use B".
///
/// The same (A→B) pair may carry multiple edges (default + writer + rules).
/// A *friend* edge is a `PermissionProfile` edge whose `grant_ref_id` equals
/// `target`'s default profile id (D12).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EdgeGrant {
    pub edge_id: u64,
    pub env: String,
    pub from_id: String,
    pub to_id: String,
    pub grant_kind: GrantKind,
    /// `PermissionProfile` -> target's default (or other) profile id;
    /// `Rules` -> opaque rules ref.
    pub grant_ref_id: u64,
    /// Inline rules; `None` unless `GrantKind::Rules`.
    #[serde(default)]
    pub rules: Option<Value>,
    #[serde(default)]
    pub status: EdgeStatus,
    #[serde(default)]
    pub originator_policy_type: OriginatorPolicyType,
    #[serde(default)]
    pub originator_policy_data: Option<Value>,
}

/// Status of a permission profile.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ProfileStatus {
    #[default]
    Active,
    Deleted,
}

/// Provenance of a capability row.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum CapabilitySource {
    #[default]
    System,
    AgentCard,
    Manual,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum CapabilityStatus {
    #[default]
    Active,
    Inactive,
}

/// A tool/operation capability a bot exposes (catalog row in `capabilities`).
///
/// Async-collected from AgentCard/tool registry (`source = AgentCard`); NOT a
/// prerequisite for default/friend access (friend edges use a wildcard-allow
/// default profile regardless of capabilities). See spec §3.1.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Capability {
    pub capability_id: u64,
    pub bot_id: String,
    pub env: String,
    pub tool: String,
    #[serde(default)]
    pub operation: Option<String>,
    #[serde(default)]
    pub specifier_schema: Option<Value>,
    pub source: CapabilitySource,
    pub status: CapabilityStatus,
    #[serde(default)]
    pub raw_metadata: Option<Value>,
}

/// Effect of a single rule.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum RuleEffect {
    #[default]
    Allow,
    Deny,
}

/// A single permission rule.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Rule {
    pub tool: String,
    #[serde(default)]
    pub operation: Option<String>,
    #[serde(default)]
    pub specifier: Option<String>,
    pub effect: RuleEffect,
    #[serde(default)]
    pub description: Option<String>,
}

/// A packaged permission template (role: default/reader/writer/maintainer).
///
/// Every bot seeds exactly one `default` profile (wildcard-allow) at onboard.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PermissionProfile {
    pub permission_profile_id: u64,
    pub bot_id: String,
    pub env: String,
    pub name: String,
    #[serde(default)]
    pub description: Option<String>,
    pub rules_template: Value,
    #[serde(default)]
    pub revision: u64,
    pub digest: String,
    #[serde(default)]
    pub is_default: bool,
    pub status: ProfileStatus,
    pub created_by: String,
    #[serde(default)]
    pub updated_by: Option<String>,
}

/// Kind of a permission request.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RequestKind {
    /// Friend connect (default-profile edge).
    Connect,
    /// Apply a non-default permission profile.
    PermissionProfile,
    /// Apply inline rules.
    Rules,
    /// Revoke an existing edge.
    Revoke,
}

/// Status of a permission request.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum RequestStatus {
    #[default]
    Pending,
    Approved,
    Rejected,
    Cancelled,
}

/// A connect/apply/revoke request record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PermissionRequest {
    pub request_id: u64,
    /// Back-filled after approval creates the edge; `None` while pending.
    #[serde(default)]
    pub edge_id: Option<u64>,
    pub env: String,
    pub from_id: String,
    pub to_id: String,
    pub request_kind: RequestKind,
    #[serde(default)]
    pub requested_ref_id: Option<u64>,
    #[serde(default)]
    pub requested_rules: Option<Value>,
    #[serde(default)]
    pub message: Option<String>,
    pub status: RequestStatus,
    #[serde(default)]
    pub decision_reason: Option<String>,
    pub created_by: String,
    #[serde(default)]
    pub decided_by: Option<String>,
    #[serde(default)]
    pub decided_at: Option<u64>,
}

/// Provenance of an active grant at runtime.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GrantSource {
    EdgeGrant,
    PublicDefault,
    CollaborationDefault,
}

/// A slim runtime reference to a grant (injected into A2A `AuthzContext`).
///
/// Bots only consume the ref; they never see `EdgeGrant` internals.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuthzGrantRef {
    pub kind: GrantKind,
    pub ref_id: u64,
    #[serde(default)]
    pub revision: Option<u64>,
    #[serde(default)]
    pub digest: Option<String>,
    pub source: GrantSource,
}

/// Runtime authorization context injected into A2A messages.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuthzContext {
    pub task_id: String,
    pub run_id: String,
    pub from_id: String,
    pub to_id: String,
    pub env: String,
    pub originator: String,
    pub context: Value,
    pub grants: Vec<AuthzGrantRef>,
    #[serde(default)]
    pub signature: Option<Vec<u8>>,
}

/// Bot-level config consumed by connect/admission decisions.
///
/// Read from `bcs_bots`: a narrow projection of the columns ConnectService
/// (T13) and AdmissionService (T14) need to decide an add/connect/admit.
/// `visibility` and `status` are the existing decision fields; `created_by`
/// is included so T13's ownership check can read it here without a second
/// query (NULL for legacy bots — auto-claim per the ownership rules).
/// `user_visibility`, `friend_check_in_strategy`, and `friend_ext` are read
/// from the bot's internal attributes payload and reused for friend-add gating.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotActorConfig {
    pub bot_id: String,
    pub env: String,
    /// `public` | `protected` | `private`.
    pub visibility: String,
    /// `online` | `hidden` — `hidden` ⇒ admission rejects (spec §4.3).
    pub status: String,
    /// Owner of the bot; `None` for legacy bots (auto-claim, spec §3.2).
    #[serde(default)]
    pub created_by: Option<String>,
    /// `public` | `protected` | `private` from bot internal attributes.
    #[serde(default = "default_user_visibility")]
    pub user_visibility: String,
    /// `OPEN` | `APPROVAL` | `DEPT_FREE` from bot internal attributes.
    #[serde(default = "default_friend_check_in_strategy")]
    pub friend_check_in_strategy: String,
    /// Free-form friend gating metadata from bot internal attributes.
    #[serde(default)]
    pub friend_ext: Map<String, Value>,
}

fn default_user_visibility() -> String {
    "protected".to_string()
}

fn default_friend_check_in_strategy() -> String {
    "APPROVAL".to_string()
}

/// Reason code for an admission decision.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum AdmissionReason {
    /// Active edge grant matched.
    #[default]
    Ok,
    /// No edge, but bot is public -> runtime public_default.
    PublicDefault,
    /// No edge and bot not public.
    NoEdge,
    /// Target bot hidden (status=hidden).
    BotHidden,
    /// Target bot not found.
    BotNotFound,
}

/// Result of `GET /bots/{id}/admission`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AdmissionResult {
    pub allowed: bool,
    pub grants: Vec<AuthzGrantRef>,
    pub reason_code: AdmissionReason,
    #[serde(default)]
    pub public_default: bool,
}

/// One entry in a friend list (human or bot peer).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FriendListEntry {
    pub actor_id: String,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub summary: Option<String>,
    #[serde(default)]
    pub is_online: bool,
    pub kind: ActorKind,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn edge_grant_roundtrip() {
        let g = EdgeGrant {
            edge_id: "eg_1".into(), env: "prod".into(),
            from_id: "human_88001".into(), to_id: "20260421_x:85020".into(),
            grant_kind: GrantKind::PermissionProfile,
            grant_ref_id: "pp_20260421_x:85020_default".into(),
            rules: None, status: EdgeStatus::Approved,
            originator_policy_type: OriginatorPolicyType::Any,
            originator_policy_data: None,
        };
        let s = serde_json::to_string(&g).unwrap();
        let back: EdgeGrant = serde_json::from_str(&s).unwrap();
        assert_eq!(g, back);
        assert_eq!(back.status, EdgeStatus::Approved);
        let def: EdgeGrant = serde_json::from_str(
            r#"{"edge_id":"e","env":"prod","from_id":"a","to_id":"b","grant_kind":"permission_profile","grant_ref_id":"r"}"#,
        ).unwrap();
        assert_eq!(def.status, EdgeStatus::Approved);
        assert_eq!(def.originator_policy_type, OriginatorPolicyType::Any);
    }

    #[test]
    fn permission_request_pending_has_no_edge() {
        let r = PermissionRequest {
            request_id: "req_1".into(), edge_id: None, env: "prod".into(),
            from_id: "human_88001".into(), to_id: "b".into(),
            request_kind: RequestKind::Connect, requested_ref_id: None,
            requested_rules: None, message: None, status: RequestStatus::Pending,
            decision_reason: None, created_by: "human_88001".into(), decided_by: None,
            decided_at: None,
        };
        let s = serde_json::to_string(&r).unwrap();
        let back: PermissionRequest = serde_json::from_str(&s).unwrap();
        assert!(back.edge_id.is_none());
        assert_eq!(back.status, RequestStatus::Pending);
    }

    #[test]
    fn enums_serialize_lowercase_snake() {
        assert_eq!(serde_json::to_string(&GrantKind::PermissionProfile).unwrap(), "\"permission_profile\"");
        assert_eq!(serde_json::to_string(&EdgeStatus::Revoked).unwrap(), "\"revoked\"");
        assert_eq!(serde_json::to_string(&AdmissionReason::PublicDefault).unwrap(), "\"public_default\"");
    }

    #[test]
    fn rule_template_wildcard_allow_parses() {
        let rules: Vec<Rule> = serde_json::from_str(
            r#"[{"tool":"*","specifier":"*","effect":"allow"}]"#,
        )
        .unwrap();
        assert_eq!(rules.len(), 1);
        assert_eq!(rules[0].tool, "*");
        assert_eq!(rules[0].specifier.as_deref(), Some("*"));
        assert_eq!(rules[0].effect, RuleEffect::Allow);
    }
}
