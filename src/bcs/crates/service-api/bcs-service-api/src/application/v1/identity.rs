use time::OffsetDateTime;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedCaller {
    /// Optional normalized tenant metadata. Gateway does not fabricate a
    /// tenant for a tenantless User Principal.
    pub tenant: Option<String>,
    pub user: Option<AuthenticatedUserIdentity>,
    pub bot: Option<AuthenticatedBotIdentity>,
    pub app: Option<AuthenticatedAppIdentity>,
    pub access_key: Option<AuthenticatedAccessKeyIdentity>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedUserIdentity {
    pub id: String,
    pub username: String,
    pub display_name: Option<String>,
    pub full_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedBotIdentity {
    pub bot_uuid: String,
    pub owner_id: String,
    pub app_id: i64,
    pub agent_code: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedAppIdentity {
    pub app_id: i64,
    pub app_name: String,
    pub owners: String,
    pub app_type: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedAccessKeyIdentity {
    pub access_key: String,
    pub expire_at: OffsetDateTime,
}
