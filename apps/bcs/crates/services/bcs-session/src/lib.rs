//! Session service implementation: SessionManagementService over a SessionRepoPort.

pub mod application;
pub mod launch;
pub mod noop;

pub use application::{SessionManagementServiceImpl, SessionManagementWithRuntimeCleanup};
pub use launch::SessionLaunchApplication;
pub use noop::{NoopSessionLaunchService, NoopSessionManagementService};
