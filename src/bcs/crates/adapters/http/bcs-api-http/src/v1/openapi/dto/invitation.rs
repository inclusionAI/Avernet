use bcs_service_api::application::v1::{
    AcceptInvitation, CreateGroupInvitation, CreateSessionInvitation, Principal,
};
use serde::Deserialize;

/// Request body for creating an invitation on either a Group or Session target.
///
/// `expires_in_seconds` is optional; servers apply a default lifetime when
/// omitted. The contract declares `minimum: 1`, so `Some(0)` is rejected at
/// deserialization time (surfacing as a 400 `invalid_request` envelope via
/// axum's `JsonRejection`). The same shape is reused for both create paths
/// because the contract (`CreateInvitationRequest`) is identical.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateInvitationRequest {
    #[serde(default, deserialize_with = "deserialize_expires_in_seconds")]
    pub expires_in_seconds: Option<u64>,
}

/// Deserialize `expires_in_seconds` accepting `None` (omitted) and
/// `Some(n) where n >= 1`; `Some(0)` is rejected so a zero-length invitation
/// lifetime is never forwarded to the facade.
fn deserialize_expires_in_seconds<'de, D>(deserializer: D) -> Result<Option<u64>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value = Option::<u64>::deserialize(deserializer)?;
    if matches!(value, Some(0)) {
        Err(serde::de::Error::custom("expires_in_seconds must be >= 1"))
    } else {
        Ok(value)
    }
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
