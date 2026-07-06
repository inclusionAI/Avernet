//! BCS system-message crate.
//!
//! Implements the `SystemMessageDispatcherService` and `SystemMessageService`
//! traits declared in `bcs-service-api`, plus placeholder producers for
//! each `SystemMessageEventKind`.

pub mod dispatcher;
pub mod producers;
pub mod service_impl;

pub use dispatcher::{SystemMessageDispatcherBuilder, SystemMessageDispatcherImpl};
pub use producers::session_context::SessionContextMessageProducer;
pub use service_impl::SystemMessageServiceImpl;

#[cfg(test)]
mod dispatcher_test;