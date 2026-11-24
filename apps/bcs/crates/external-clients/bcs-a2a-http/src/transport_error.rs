//! Keeps one fact the generic transport messages drop: whether the connection
//! was established at all.
//!
//! A request that failed before a connection existed (DNS failure, connection
//! refused, connect timeout) certainly did not reach the peer, so a caller may
//! try again without risking a duplicate. Every other failure stays ambiguous.
//!
//! The distributed Connector's agent client (`dist_agent`) sorts a failed
//! send into three kinds; this module supplies the evidence for the first:
//! - `NotSent`: the error carries `NOT_CONNECTED`. The agent never saw the
//!   request, so resending it is safe.
//! - `Rejected`: the agent answered and refused the request itself (selected
//!   HTTP statuses / gRPC codes from the `A2A HTTP <status>` and
//!   `A2A gRPC <code>` markers, or JSON-RPC request-refusal codes). Nothing
//!   was created; resending the same request would be refused again.
//! - `OutcomeUnknown`: everything else, e.g. a timeout or a reset after the
//!   connection was up, a 5xx, a broken stream. The peer may have received
//!   the request and started work, so it must not be resent blindly.
//!
//! Losing this fact would turn every connect failure into `OutcomeUnknown`:
//! safe, but a send that provably never left would then be left uncertain.

/// Message of an error raised before any connection was established. Like the
/// `A2A HTTP <status>` and `A2A gRPC <code>` markers, it is generated here and
/// never copied from the peer.
pub(crate) const NOT_CONNECTED: &str = "A2A connection not established";

/// The error of a failed HTTP send: `NOT_CONNECTED` when no connection was
/// established (reqwest's `is_connect`), otherwise the caller's generic
/// message, which classifies as `OutcomeUnknown`.
pub(crate) fn send_failure(error: &reqwest::Error, otherwise: &'static str) -> a2a::A2AError {
    a2a::A2AError::internal(if error.is_connect() { NOT_CONNECTED } else { otherwise })
}

/// Whether a gRPC status reports a refused connection: nothing was sent.
///
/// Only a refused connection is recognised. gRPC here always dials the local
/// mesh sidecar on a literal loopback address, so there is no DNS step, and a
/// connect timeout to it is left ambiguous on purpose: it is the safe side.
pub(crate) fn grpc_not_connected(status: &tonic::Status) -> bool {
    if status.code() != tonic::Code::Unavailable {
        return false;
    }
    let mut source = std::error::Error::source(status);
    while let Some(error) = source {
        if let Some(io) = error.downcast_ref::<std::io::Error>() {
            if io.kind() == std::io::ErrorKind::ConnectionRefused {
                return true;
            }
        }
        source = error.source();
    }
    false
}
