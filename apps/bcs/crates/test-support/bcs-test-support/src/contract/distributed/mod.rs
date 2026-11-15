//! Provider-independent assertions for distributed contract v1.
//!
//! These are real callable harnesses, not proof that a backend exists or passes.
//! Each future provider supplies isolated fixtures with the documented state and
//! calls these from conformance entries. Fault-injection/restart expansion is S1.

pub mod dispatch;
pub mod inbox;
pub mod services;
pub mod state;
pub mod task_feed;

pub type ContractResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;
