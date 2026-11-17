//! Outbound A2A adapter. SDK types stay at this delivery boundary.
pub mod text;
pub mod client;
pub mod jsonrpc;
pub mod grpc_client;
pub mod grpc_mesh;
mod binding_client;
mod transport_error;
#[cfg(feature = "dist-agent")]
pub mod dist_agent;
pub mod card;
pub mod projection;
pub mod card_fetch;
mod subscribe;
pub mod delivery;
mod client_json;
mod http_client;
pub mod tools;
pub mod structured_exec;
