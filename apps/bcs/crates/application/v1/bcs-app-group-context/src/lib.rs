//! Transport-independent Group Context application semantics for the BCN V1 API.

mod permissions;
mod scope;
mod service;

pub use permissions::{actor_can_read, actor_can_write, permission_for};
pub use scope::{ResolvedScope, resolve_scope, resolve_scopes};
pub use service::{GroupContextApplicationServiceImpl, MAX_CONTENT_BYTES};
