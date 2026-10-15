//! Narrow, fresh authorization projection; contains no Bot or Provider credentials.
#[derive(Debug, Clone)]
pub struct CollaborationActor {
    pub actor_id: String,
    pub owner: Option<String>,
    pub provider_id: Option<String>,
    pub provider_config: Option<String>,
    pub provider_disabled: bool,
}
