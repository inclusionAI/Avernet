//! Provider-independent assertions for distributed contract v1.
//!
//! These are real callable harnesses, not proof that a backend exists or passes.
//! Each future provider supplies isolated fixtures with the documented state and
//! calls these from conformance entries. Fault-injection/restart expansion is S1.
//!
//! The `distributed_harness` integration target self-tests these assertions with
//! scripted replies, including deliberate violations. It is not provider conformance.

pub mod admission;
pub mod dispatch;
pub mod group_round;
pub mod inbox;
pub mod node_history;
pub mod output;
pub mod runtime_safety;
pub mod services;
pub mod state;
pub mod task_feed;
pub mod workflow_revision;

pub type ContractResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;
