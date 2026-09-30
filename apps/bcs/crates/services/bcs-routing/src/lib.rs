//! BCS routing service.

pub mod core;
pub mod security;

pub use core::{MessageRouter, RouteSelector, RouteSelectorError, StructuredRouteRequest};
