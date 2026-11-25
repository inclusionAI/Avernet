//! Distributed coordination data contracts, version 1.
//!
//! Declarations only: no storage, transport, identity generation or runtime policy.
//! Backend cursor encoding is opaque; stable business identities survive backend
//! replacement. Validation belongs to the admitting service and repository port.

pub mod dispatch;
pub mod ids;
pub mod input;
pub mod output;
pub mod runner;
pub mod session;
pub mod task;

pub use dispatch::*;
pub use ids::*;
pub use input::*;
pub use output::*;
pub use runner::*;
pub use session::*;
pub use task::*;
