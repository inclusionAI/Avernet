//! WebSocket handler for Gateway connections.

#[cfg(test)]
mod tests {
    use async_trait::async_trait;

    use crate::gateway::context::AuthValidator;

    struct MockAuthValidator {
        valid_token: Option<String>,
    }

    impl MockAuthValidator {
        fn new(valid_token: Option<String>) -> Self {
            Self { valid_token }
        }
    }

    #[async_trait]
    impl AuthValidator for MockAuthValidator {
        fn validate(&self, token: &str) -> bool {
            match &self.valid_token {
                Some(expected) => {
                    let expected_bytes = expected.as_bytes();
                    let token_bytes = token.as_bytes();

                    if expected_bytes.len() != token_bytes.len() {
                        return false;
                    }

                    let mut result = 0u8;
                    for (a, b) in expected_bytes.iter().zip(token_bytes.iter()) {
                        result |= a ^ b;
                    }

                    result == 0
                }
                None => true,
            }
        }
    }

    #[test]
    fn test_token_validation() {
        let validator = MockAuthValidator::new(Some("test-token".to_string()));

        assert!(validator.validate("test-token"));
        assert!(!validator.validate("wrong-token"));
        assert!(!validator.validate(""));
    }

    #[test]
    fn test_token_validation_no_config() {
        let validator = MockAuthValidator::new(None);

        // No token configured means all connections allowed
        assert!(validator.validate("any-token"));
    }
}
