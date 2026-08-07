//! In-memory authorization repository implementations.
//!
//! The store owns authorization-domain state for local execution and tests.

pub use bcs_service_api::port::repo::{
    AuthzDecisionLogRepoPort, CapabilityCatalogRepoPort, EdgeGrantRepoPort,
    PermissionProfileRepoPort, PermissionRequestRepoPort,
};

pub mod memory;

pub use memory::MemoryAuthorizationStore;
