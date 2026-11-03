pub mod group_core;

pub use bcs_group_store::{GroupBuilder, MemoryGroupRepo};
pub use group_core::GroupCore;

pub type GroupStore = GroupCore;
