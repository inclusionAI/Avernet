use serde::Deserialize;

#[derive(Deserialize)]
pub(super) struct GatewayClaims {
    pub iss: String,
    pub aud: String,
    pub iat: u64,
    pub exp: u64,
    pub principals: Vec<GatewayPrincipal>,
}

#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(super) enum GatewayPrincipal {
    User {
        tenant: String,
        subject: GatewayUser,
    },
    Bot {
        tenant: String,
        bot: GatewayBot,
    },
    App {
        tenant: String,
        app: GatewayApp,
    },
    AccessKey {
        tenant: String,
        access_key: GatewayAccessKey,
    },
}

#[derive(Deserialize)]
pub(super) struct GatewayUser {
    pub id: String,
    pub username: String,
    pub display_name: Option<String>,
    pub full_name: Option<String>,
    pub tenant_id: Option<String>,
}

#[derive(Deserialize)]
pub(super) struct GatewayBot {
    pub bot_uuid: String,
    pub owner_id: String,
    pub app_id: i64,
    pub agent_code: String,
    pub tenant: String,
}

#[derive(Deserialize)]
pub(super) struct GatewayApp {
    pub app_id: i64,
    pub app_name: String,
    pub owners: String,
    pub tenant: String,
    pub app_type: String,
}

#[derive(Deserialize)]
pub(super) struct GatewayAccessKey {
    pub access_key: String,
    pub expire_at: String,
}
