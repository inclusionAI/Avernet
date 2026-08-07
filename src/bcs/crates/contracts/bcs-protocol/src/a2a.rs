use serde::{Deserialize, Serialize};
use serde_json::Value;

pub use bcs_domain::authorization::{
    AuthzContext as A2aAuthzContext, AuthzContextType as A2aAuthzContextType,
    AuthzGrantRef as A2aAuthzGrantRef, AuthzRuntimeContext as A2aAuthzRuntimeContext,
    Decision as A2aAuthzDecision, GrantKind as A2aAuthzGrantKind,
    GrantSource as A2aAuthzGrantSource, GrantStatus as A2aAuthzGrantStatus,
    PermissionProfile as A2aPermissionProfile,
    PermissionProfileStatus as A2aPermissionProfileStatus,
    PermissionRequest as A2aPermissionRequest, PermissionRequestKind as A2aPermissionRequestKind,
    PermissionRequestStatus as A2aPermissionRequestStatus, Rule as A2aRule,
    RuleEffect as A2aRuleEffect,
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct A2aRunStatus {
    pub run_id: String,
    pub status: String,
    pub response: Option<Value>,
}
