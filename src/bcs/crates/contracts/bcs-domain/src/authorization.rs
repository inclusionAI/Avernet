//! Authorization domain types for BCS A2A authz.

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Capability source extracted from a bot Agent Card or added manually.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CapabilitySource {
    AgentCard,
    Manual,
    System,
}

/// Capability lifecycle state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CapabilityStatus {
    Active,
    Inactive,
}

/// A bot capability exposed by the runtime or configured manually.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Capability {
    pub capability_id: String,
    pub bot_id: String,
    pub env: String,
    pub tool: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub operation: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub specifier_schema: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    pub source: CapabilitySource,
    pub status: CapabilityStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub raw_metadata: Option<Value>,
    pub created_at: i64,
    pub updated_at: i64,
}

/// Rule effect in a permission profile or rules grant.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuleEffect {
    Allow,
    Deny,
}

/// A single allow/deny rule.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Rule {
    pub tool: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub operation: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub specifier: Option<String>,
    pub effect: RuleEffect,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub raw_metadata: Option<Value>,
}

/// Permission profile lifecycle state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PermissionProfileStatus {
    Active,
    Deleted,
}

/// A permission profile assembled by the bot owner from capabilities.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PermissionProfile {
    pub permission_profile_id: String,
    pub bot_id: String,
    pub env: String,
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(default)]
    pub rules_template: Vec<Rule>,
    pub revision: i64,
    pub digest: String,
    pub is_default: bool,
    pub status: PermissionProfileStatus,
    pub created_by: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_by: Option<String>,
    pub created_at: i64,
    pub updated_at: i64,
}

/// Grant kind stored inside EdgeGrant and forwarded in AuthzContext.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GrantKind {
    PermissionProfile,
    Rules,
}

/// Grant source recorded in runtime AuthzContext.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GrantSource {
    EdgeGrant,
    PublicDefault,
    CollaborationDefault,
}

/// Grant lifecycle state for authorization facts.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GrantStatus {
    Pending,
    Approved,
    Rejected,
    Revoked,
    Expired,
}

/// A unified grant reference carried in AuthzContext.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuthzGrantRef {
    pub kind: GrantKind,
    pub ref_id: String,
    pub revision: i64,
    pub digest: String,
    pub source: GrantSource,
}

/// Short-lived runtime context class used for authz decisions.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AuthzContextType {
    Direct,
    PublicChat,
    Collaboration,
}

/// Detailed runtime context for the current A2A message.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum AuthzRuntimeContext {
    Direct,
    PublicChat,
    Collaboration {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        group_id: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        session_id: Option<String>,
    },
}

/// Authz decision result used by logs and policy evaluation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Decision {
    Allow,
    Deny,
}

/// A persisted EdgeGrant fact.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EdgeGrant {
    pub edge_id: String,
    pub from_id: String,
    pub to_id: String,
    pub env: String,
    pub grant_kind: GrantKind,
    pub grant_ref_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rules: Option<Vec<Rule>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rules_revision: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rules_digest: Option<String>,
    pub status: GrantStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    pub requested_by: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approved_by: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub revoked_by: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<i64>,
    pub created_at: i64,
    pub updated_at: i64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approved_at: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub revoked_at: Option<i64>,
}

/// Resolved rules grant material returned to a target bot for local tool authorization.
/// It intentionally omits EdgeGrant internals such as edge_id.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RulesGrantMaterial {
    pub rules_grant_ref: String,
    pub from_id: String,
    pub to_id: String,
    pub env: String,
    #[serde(default)]
    pub rules: Vec<Rule>,
    pub revision: i64,
    pub digest: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<i64>,
}

/// Permission request kind.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PermissionRequestKind {
    Connect,
    PermissionProfile,
    Rules,
}

/// Permission request lifecycle state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PermissionRequestStatus {
    Pending,
    Approved,
    Rejected,
    Cancelled,
}

/// A request to create or extend an authorization edge.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PermissionRequest {
    pub request_id: String,
    pub env: String,
    pub from_id: String,
    pub to_id: String,
    pub request_kind: PermissionRequestKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub requested_ref_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub requested_rules: Option<Vec<Rule>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    pub status: PermissionRequestStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decision_reason: Option<String>,
    pub created_by: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decided_by: Option<String>,
    pub created_at: i64,
    pub updated_at: i64,
}

/// Persisted decision log for runtime authz.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AuthzDecisionLog {
    pub decision_id: String,
    pub env: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
    pub from_id: String,
    pub to_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub originator: Option<String>,
    pub context_type: AuthzContextType,
    pub decision: Decision,
    pub reason_code: String,
    pub grant_refs: Vec<AuthzGrantRef>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub context_json: Option<Value>,
    pub created_at: i64,
}

/// Short-lived runtime authz context injected into A2A messages.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AuthzContext {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
    pub from_id: String,
    pub to_id: String,
    pub env: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub originator: Option<String>,
    pub context: AuthzRuntimeContext,
    #[serde(default)]
    pub grants: Vec<AuthzGrantRef>,
    pub issued_at: i64,
    pub expires_at: i64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub signature: Option<String>,
}

// Compatibility aliases for existing BCS service-api exports.
pub type AuthzDecision = Decision;
pub type EdgeGrantStatus = GrantStatus;
pub type GrantRef = AuthzGrantRef;
pub type RuleDecision = Decision;
pub type RuntimeContext = AuthzRuntimeContext;
