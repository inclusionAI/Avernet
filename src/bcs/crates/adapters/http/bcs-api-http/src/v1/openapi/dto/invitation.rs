use bcs_service_api::application::v1::{
    AcceptInvitation, CreateGroupInvitation, CreateSessionInvitation, Principal,
};
use serde::Deserialize;

/// Request body for creating an invitation on either a Group or Session target.
///
/// `expires_in_seconds` is optional; servers apply a default lifetime when
/// omitted. The same shape is reused for both create paths because the contract
/// (`CreateInvitationRequest`) is identical.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateInvitationRequest {
    #[serde(default)]
    pub expires_in_seconds: Option<u64>,
}

impl CreateInvitationRequest {
    pub fn into_group_command(
        self,
        principal: Principal,
        group_id: String,
    ) -> CreateGroupInvitation {
        CreateGroupInvitation {
            principal,
            group_id,
            expires_in_seconds: self.expires_in_seconds,
        }
    }

    pub fn into_session_command(
        self,
        principal: Principal,
        session_id: String,
    ) -> CreateSessionInvitation {
        CreateSessionInvitation {
            principal,
            session_id,
            expires_in_seconds: self.expires_in_seconds,
        }
    }
}

/// Request body for accepting an invitation token.
///
/// `bot_uuid` is omitted when a Bot Principal accepts for itself and set when a
/// Human Principal accepts on behalf of a Bot it owns.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AcceptInvitationRequest {
    #[serde(default)]
    pub bot_uuid: Option<String>,
}

impl AcceptInvitationRequest {
    pub fn into_command(self, principal: Principal, token: String) -> AcceptInvitation {
        AcceptInvitation {
            principal,
            token,
            bot_uuid: self.bot_uuid,
        }
    }
}
