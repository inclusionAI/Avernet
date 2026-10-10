//! BCS bridge application: commands, registration and the plugin interfaces a
//! `bcs-bridge` binary is assembled from (see [`app::BridgeApp`]).
//!
//! Plugin interfaces:
//! - engines: [`bcs_bridge_core::engine::EngineFactory`];
//! - endpoint defaults: [`profile::DeployProfile`];
//! - gateway webhook credentials: [`credentials::CredentialSource`];
//! - Bot identity at registration: [`identity::IdentityResolver`].
pub mod app;
mod cli;
mod config;
pub mod credentials;
pub mod identity;
#[cfg(unix)]
mod instance;
pub mod profile;
pub mod registration;
pub mod utils;

pub use app::{BridgeApp, BridgeAppBuilder, CliDefaults};
pub use bcs_bridge_core;
