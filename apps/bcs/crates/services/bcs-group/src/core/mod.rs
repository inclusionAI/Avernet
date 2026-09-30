pub mod group_core;
pub mod service_spec_patch;

pub use bcs_group_store::{GroupBuilder, MemoryGroupRepo};
pub use group_core::GroupCore;
pub use service_spec_patch::{validate_service_spec_patch, ServiceSpecPatchError};

pub type GroupStore = GroupCore;
