pub mod friend_core;
pub mod friend_request_core;
pub mod friendship_writeback;

pub use bcs_friend_store::{MemoryFriendRepo, MemoryFriendRequestRepo};
pub use friend_core::FriendCore;
pub use friend_request_core::FriendRequestCore;
pub use friendship_writeback::FriendshipWritebackService;

pub type FriendStore = FriendCore;
pub type FriendRequestStore = FriendRequestCore;
