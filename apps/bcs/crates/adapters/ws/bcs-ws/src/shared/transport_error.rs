/// Returns true when the error is a transport-level disconnection rather than
/// an application-level protocol error. These indicate the TCP connection was
/// broken (client crash, network loss, proxy termination) and should be
/// classified as `WsCloseReason::ClientClose`, not `WsCloseReason::ProtocolError`.
pub fn is_transport_close_error(error: &impl std::fmt::Display) -> bool {
    let s = error.to_string();
    s.contains("Broken pipe")
        || s.contains("broken pipe")
        || s.contains("Connection reset without closing handshake")
        || s.contains("Connection reset by peer")
        || s.contains("connection reset")
        || s.contains("os error 32")
        || s.contains("os error 54") // ECONNRESET on macOS
        || s.contains("os error 104") // ECONNRESET on Linux
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_broken_pipe() {
        assert!(is_transport_close_error(
            &"IO error: Broken pipe (os error 32)"
        ));
    }

    #[test]
    fn detects_connection_reset_without_handshake() {
        assert!(is_transport_close_error(
            &"WebSocket protocol error: Connection reset without closing handshake"
        ));
    }

    #[test]
    fn detects_connection_reset_by_peer() {
        assert!(is_transport_close_error(
            &"Connection reset by peer (os error 54)"
        ));
    }

    #[test]
    fn does_not_match_parse_error() {
        assert!(!is_transport_close_error(
            &"expected value at line 1 column 1"
        ));
    }

    #[test]
    fn does_not_match_empty() {
        assert!(!is_transport_close_error(&""));
    }
}
