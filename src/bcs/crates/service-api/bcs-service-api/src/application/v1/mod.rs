//! Versioned application contracts for BCN OpenAPI v1.
//!
//! These contracts are transport-independent. Delivery adapters translate
//! HTTP requests into these commands and never pass credentials or
//! request-supplied caller identities into domain services.

pub mod authorization;
pub mod error;
pub mod group;
pub mod principal;

pub use authorization::{Action, AuthorizationService, ResourceRef};
pub use error::ApplicationError;
pub use group::*;
pub use principal::{AuthenticatedUser, BotPrincipal, HumanPrincipal, Principal};
