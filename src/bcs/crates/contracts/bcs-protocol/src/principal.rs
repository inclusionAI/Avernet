use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum CallerContext {
    Public,
    Human(HumanActor),
    Bot(BotActor),
    Integration(IntegrationClient),
    Admin(AdminActor),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HumanActor {
    pub actor_id: String,
    pub staff_no: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotActor {
    pub bot_uuid: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IntegrationClient {
    pub client_id: String,
    pub scopes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AdminActor {
    pub actor_id: String,
    pub scopes: Vec<String>,
}
