//! Session service implementation: SessionManagementService over a SessionRepoPort.

pub mod application;
pub mod noop;

pub use application::{SessionManagementServiceImpl, SessionManagementWithRuntimeCleanup};
pub use noop::NoopSessionManagementService;
