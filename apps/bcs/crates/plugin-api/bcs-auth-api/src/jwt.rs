//! JWT-shape recognition. Legacy BCS tokens are dot-less UUIDs, so a simple
//! three-segment check routes JWT tokens to the agentpass path with no false
//! positives.

/// Recognize a JWT-shaped token by its three dot-separated segments.
pub fn is_jwt_format(token: &str) -> bool {
    token.split('.').count() == 3
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_real_jwt() {
        assert!(is_jwt_format("eyJraWQ.eyJhdWQ.NI1Swo"));
    }

    #[test]
    fn rejects_uuid() {
        assert!(!is_jwt_format("550e8400-e29b-41d4-a716-446655440000"));
    }

    #[test]
    fn rejects_wrong_segment_count() {
        assert!(!is_jwt_format("one.two"));
        assert!(!is_jwt_format("a.b.c.d"));
    }
}
