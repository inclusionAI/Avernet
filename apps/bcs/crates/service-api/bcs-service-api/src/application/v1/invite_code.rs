use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{ApplicationError, AuthenticatedCaller};

/// Command for batch-initializing invite codes from an internal caller.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct InitInviteCodes {
    pub count: u64,
}

/// Invite-code batch initialization result.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InitInviteCodesResult {
    pub codes: Vec<String>,
}

/// Command for anonymously claiming one newly generated invite code.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ClaimPublicInviteCode;

/// Result returned when an anonymous caller claims an invite code.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClaimPublicInviteCodeResult {
    pub invite_code: String,
}

/// Command for binding the current human caller to an invite code.
#[derive(Debug, Clone)]
pub struct BindInviteCode {
    pub caller: AuthenticatedCaller,
    pub code: String,
}

/// Result of binding the current caller to an invite code.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BindInviteCodeResult {
    pub bound: bool,
    pub bound_at: u64,
}

/// Command for checking the current caller's invite-code binding state.
#[derive(Debug, Clone)]
pub struct GetMyInviteCodeBinding {
    pub caller: AuthenticatedCaller,
}

/// Projection of the current caller's invite-code binding state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InviteCodeBindingView {
    pub bound: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bound_at: Option<u64>,
}

/// Transport-independent invite-code use cases for BCN OpenAPI v1.
#[async_trait]
pub trait InviteCodeService: Send + Sync {
    async fn init_invite_codes(
        &self,
        command: InitInviteCodes,
    ) -> Result<InitInviteCodesResult, ApplicationError>;

    async fn claim_public_invite_code(
        &self,
        command: ClaimPublicInviteCode,
    ) -> Result<ClaimPublicInviteCodeResult, ApplicationError>;

    async fn bind_invite_code(
        &self,
        command: BindInviteCode,
    ) -> Result<BindInviteCodeResult, ApplicationError>;

    async fn get_my_invite_code_binding(
        &self,
        command: GetMyInviteCodeBinding,
    ) -> Result<InviteCodeBindingView, ApplicationError>;

    /// Gate helper for downstream platform use cases.
    ///
    /// Human callers must have already bound an invite code. Bot/App/AccessKey
    /// callers are allowed through unchanged so the gate does not interfere with
    /// existing automation and internal service traffic.
    async fn ensure_invite_code_access(
        &self,
        caller: &AuthenticatedCaller,
    ) -> Result<(), ApplicationError>;
}
