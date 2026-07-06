//! BCS friend service.

pub mod application;
pub mod core;

pub use bcs_friend_store::{
    DbFriendRequestStore, DbFriendStore, FriendSqlFlavor, MemoryFriendRepo, MemoryFriendRequestRepo,
};
pub use application::Friend;
pub use core::{FriendCore, FriendRequestCore, FriendRequestStore, FriendStore};
