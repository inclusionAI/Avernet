//! Internal Bot attributes application contract.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use super::ApplicationError;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum UserVisibility {
    Public,
    #[default]
    Protected,
    Private,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum FriendCheckInStrategy {
    Open,
    #[default]
    Approval,
    DeptFree,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct BotInternalAttributes {
    pub user_visibility: UserVisibility,
    #[serde(default)]
    pub friend_ext: Map<String, Value>,
    pub friend_check_in_strategy: FriendCheckInStrategy,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct PatchBotInternalAttributes {
    pub bot_id: String,
    pub user_visibility: Option<UserVisibility>,
    pub friend_ext: Option<Map<String, Value>>,
    pub friend_check_in_strategy: Option<FriendCheckInStrategy>,
}

impl PatchBotInternalAttributes {
    pub fn is_empty(&self) -> bool {
        self.user_visibility.is_none()
            && self.friend_ext.is_none()
            && self.friend_check_in_strategy.is_none()
    }
}

#[async_trait]
pub trait InternalBotAttributesService: Send + Sync {
    async fn get(&self, bot_id: String) -> Result<BotInternalAttributes, ApplicationError>;

    async fn patch(
        &self,
        command: PatchBotInternalAttributes,
    ) -> Result<BotInternalAttributes, ApplicationError>;
}
