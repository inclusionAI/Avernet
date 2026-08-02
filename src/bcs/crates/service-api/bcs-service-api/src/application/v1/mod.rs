//! Versioned application contracts for BCN OpenAPI v1.
//!
//! These contracts are transport-independent. Delivery adapters translate
//! HTTP requests into these commands and never pass credentials or
//! request-supplied caller identities into domain services.

pub mod authorization;
pub mod bot;
pub mod error;
pub mod friendship;
pub mod group;
pub mod invitation;
pub mod message;
pub mod principal;
pub mod session;

pub use authorization::{Action, AuthorizationService, ResourceRef};
pub use bot::*;
pub use error::ApplicationError;
pub use friendship::*;
pub use group::*;
pub use invitation::*;
pub use message::*;
pub use principal::{AuthenticatedUser, BotPrincipal, HumanPrincipal, Principal};
pub use session::*;
