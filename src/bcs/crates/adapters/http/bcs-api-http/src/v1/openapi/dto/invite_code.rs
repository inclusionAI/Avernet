use bcs_service_api::application::v1::{
    AuthenticatedCaller, BindInviteCode, GetMyInviteCodeBinding,
};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BindInviteCodeRequest {
    pub code: String,
}

#[cfg_attr(not(test), allow(dead_code))]
impl BindInviteCodeRequest {
    pub fn into_command(self, caller: AuthenticatedCaller) -> BindInviteCode {
        BindInviteCode {
            caller,
            code: self.code,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InitInviteCodesRequest {
    pub count: u64,
}

#[derive(Debug, Deserialize, Default)]
#[serde(deny_unknown_fields)]
#[cfg_attr(not(test), allow(dead_code))]
pub struct GetMyInviteCodeBindingRequest {}

#[cfg_attr(not(test), allow(dead_code))]
impl GetMyInviteCodeBindingRequest {
    pub fn into_command(self, caller: AuthenticatedCaller) -> GetMyInviteCodeBinding {
        GetMyInviteCodeBinding { caller }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn caller() -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: Some("tenant-1".to_string()),
            user: Some(bcs_service_api::application::v1::AuthenticatedUserIdentity {
                id: "staff-1".to_string(),
                username: "staff-1".to_string(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        }
    }

    #[test]
    fn bind_invite_code_request_into_command_preserves_code_and_caller() {
        let request = BindInviteCodeRequest { code: "ABC123".to_string() };
        let command = request.into_command(caller());

        assert_eq!(command.code, "ABC123");
        assert_eq!(command.caller.tenant.as_deref(), Some("tenant-1"));
        assert_eq!(command.caller.user.as_ref().map(|user| user.id.as_str()), Some("staff-1"));
    }

    #[test]
    fn get_my_invite_code_binding_request_into_command_preserves_caller() {
        let request = GetMyInviteCodeBindingRequest {};
        let command = request.into_command(caller());

        assert_eq!(command.caller.tenant.as_deref(), Some("tenant-1"));
        assert_eq!(command.caller.user.as_ref().map(|user| user.username.as_str()), Some("staff-1"));
    }
}
