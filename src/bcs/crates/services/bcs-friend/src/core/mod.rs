pub mod friend_core;
pub mod friend_request_core;

pub use bcs_friend_store::{MemoryFriendRepo, MemoryFriendRequestRepo};
pub use friend_core::FriendCore;
pub use friend_request_core::FriendRequestCore;

pub type FriendStore = FriendCore;
pub type FriendRequestStore = FriendRequestCore;
