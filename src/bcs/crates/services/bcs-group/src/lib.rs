//! BCS group service.

pub mod application;
pub mod core;
mod noop;

pub use bcs_group_store::MySqlGroupStore;
pub use application::{GroupConfig, GroupManagement};
pub use core::{GroupBuilder, GroupCore, GroupStore, MemoryGroupRepo};
