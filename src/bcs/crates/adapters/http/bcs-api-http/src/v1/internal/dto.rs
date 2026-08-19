use bcs_service_api::application::v1::{
    FriendCheckInStrategy, PatchBotInternalAttributes, UserVisibility,
};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value};

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PatchBotInternalAttributesRequest {
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub user_visibility: Option<UserVisibility>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub friend_ext: Option<Map<String, Value>>,
    #[serde(default, deserialize_with = "deserialize_present_non_null")]
    pub friend_check_in_strategy: Option<FriendCheckInStrategy>,
}

impl PatchBotInternalAttributesRequest {
    pub fn is_empty(&self) -> bool {
        self.user_visibility.is_none()
            && self.friend_ext.is_none()
            && self.friend_check_in_strategy.is_none()
    }

    pub fn into_command(self, bot_id: String) -> PatchBotInternalAttributes {
        PatchBotInternalAttributes {
            bot_id,
            user_visibility: self.user_visibility,
            friend_ext: self.friend_ext,
            friend_check_in_strategy: self.friend_check_in_strategy,
        }
    }
}

fn deserialize_present_non_null<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    T::deserialize(deserializer).map(Some)
}
