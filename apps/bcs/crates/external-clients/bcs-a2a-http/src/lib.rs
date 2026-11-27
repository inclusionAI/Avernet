//! Outbound A2A adapter. SDK types stay at this delivery boundary.
pub mod text;
pub mod client;
pub mod jsonrpc;
pub mod grpc_client;
pub mod grpc_mesh;
mod binding_client;
/// Keeps "no connection was established" (= certainly not sent) in errors.
mod transport_error;
/// The distributed Connector's agent client (`DistAgentClientPort`); only with
/// the `dist-agent` feature, which adds the `bcs-dist-model` dependency.
#[cfg(feature = "dist-agent")]
pub mod dist_agent;
pub mod card;
pub mod projection;
pub mod card_fetch;
mod subscribe;
mod test_response;
pub mod delivery;
pub mod digest;
mod client_json;
mod http_client;
pub mod tools;
pub mod structured_exec;
