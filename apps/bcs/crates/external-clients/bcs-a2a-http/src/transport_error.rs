//! Keeps one fact the generic transport messages drop: whether the connection
//! was established at all.
//!
//! A request that failed before a connection existed (DNS failure, connection
//! refused, connect timeout) certainly did not reach the peer, so a caller may
//! try again without risking a duplicate. Every other failure stays ambiguous.

/// Message of an error raised before any connection was established. Like the
/// `A2A HTTP <status>` and `A2A gRPC <code>` markers, it is generated here and
/// never copied from the peer.
pub(crate) const NOT_CONNECTED: &str = "A2A connection not established";

/// The error of a failed HTTP send: `NOT_CONNECTED` when no connection was
/// established, otherwise the caller's generic message.
pub(crate) fn send_failure(error: &reqwest::Error, otherwise: &'static str) -> a2a::A2AError {
    a2a::A2AError::internal(if error.is_connect() { NOT_CONNECTED } else { otherwise })
}

/// Whether a gRPC status reports a refused connection: nothing was sent.
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
