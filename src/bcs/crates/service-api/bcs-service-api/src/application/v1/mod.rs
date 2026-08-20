//! Versioned application contracts for BCN OpenAPI v1.
//!
//! These contracts are transport-independent. Delivery adapters translate
//! HTTP requests into these commands and never pass credentials or
//! request-supplied caller identities into domain services.

pub mod authorization;
pub mod bot;
pub mod error;
pub mod event_subscription;
pub mod friendship;
pub mod group;
pub mod group_session_connection;
pub mod identity;
pub mod invitation;
pub mod message;
pub mod principal;
pub mod session;
pub mod session_file;

pub use authorization::{
    Action, AuthorizationService, IdentityPolicy, ResourceRef, require_authenticated_user,
    require_human, select_principal,
};
pub use bot::*;
pub use error::*;
pub use event_subscription::*;
pub use friendship::*;
pub use group::*;
pub use group_session_connection::*;
pub use identity::{
    AuthenticatedAccessKeyIdentity, AuthenticatedAppIdentity, AuthenticatedBotIdentity,
    AuthenticatedCaller, AuthenticatedUserIdentity,
};
pub use invitation::*;
pub use message::*;
pub use principal::{AuthenticatedUser, BotPrincipal, HumanPrincipal, Principal};
pub use session::*;
pub use session_file::*;
