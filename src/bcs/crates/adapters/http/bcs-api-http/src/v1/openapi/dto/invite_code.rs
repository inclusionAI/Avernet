use bcs_service_api::application::v1::{
    AuthenticatedCaller, BindInviteCode, GetMyInviteCodeBinding,
};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BindInviteCodeRequest {
    pub code: String,
}

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
pub struct GetMyInviteCodeBindingRequest {}

impl GetMyInviteCodeBindingRequest {
    pub fn into_command(self, caller: AuthenticatedCaller) -> GetMyInviteCodeBinding {
        GetMyInviteCodeBinding { caller }
    }
}
