use bcs_service_api::application::v1::{
    AuthenticatedCaller, CreateBotFriendRequest, FriendRequestDirection, FriendRequestStatus,
};
use serde::Deserialize;

fn default_limit() -> u64 {
    20
}

/// Request body for sending a friend request from the path Bot to a target Bot.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateFriendRequestRequest {
    pub to_bot_uuid: String,
}

impl CreateFriendRequestRequest {
    pub fn into_command(
        self,
        caller: AuthenticatedCaller,
        bot_uuid: String,
    ) -> CreateBotFriendRequest {
        CreateBotFriendRequest {
            caller,
            bot_uuid,
            to_bot_uuid: self.to_bot_uuid,
        }
    }
}

/// Query parameters for listing a Bot's friendships.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListFriendshipsQuery {
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_limit")]
    pub limit: u64,
}

/// Query parameters for listing friend requests sent by or received by a Bot.
///
/// `direction` defaults to `received` when omitted (mirrors
/// `FriendRequestDirection::default()`). `status` is optional.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListFriendRequestsQuery {
    #[serde(default)]
    pub offset: u64,
    #[serde(default = "default_limit")]
    pub limit: u64,
    #[serde(default)]
    pub direction: Option<FriendRequestDirection>,
    #[serde(default)]
    pub status: Option<FriendRequestStatus>,
}
