//! Deterministic strict-FSM round reducer (plan T5 / Stage 2).
//!
//! The crate contains the concrete implementation of the shared
//! [`FsmValidatorCoreService`] publication gate and a pure reducer that folds
//! [`RunnerInput`] batches into dispatch/cancel/complete intents. The reducer
//! performs no IO, reads no clock and generates no random identities: `now`
//! and every input enter through the [`EvaluationContext`] and the input
//! slice, so the same state plus the same ordered inputs always reproduce the
//! same [`Decision`].
//!
//! No Worker/T6/T7 wiring lives here; consumers own admission, dispatch
//! delivery, stop persistence and publication.

pub mod reducer;
pub mod validator;

pub use reducer::FsmReducerRunner;
pub use validator::{d0_capabilities, FsmValidator};
