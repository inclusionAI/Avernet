//! Local / fake implementations of [`SecurityGatewayPort`].
//!
//! Two flavors:
//! - [`NoopSecurityGateway`] — every call returns [`SecurityVerdict::Allow`].
//!   The default for open-source builds where no external AI security gateway
//!   is wired; keeps message delivery unconstrained while satisfying the port.
//! - [`FakeSecurityGateway`] — returns a preconfigured verdict, for tests that
//!   need to exercise the interceptor's allow / deny / unavailable branches.

use async_trait::async_trait;
use bcs_security_gateway_api::{
    SecurityCheckRequest, SecurityGatewayPort, SecurityVerdict,
};
use tracing::debug;

/// Always allows. Default safety net when the composition root hasn't wired a
/// real security gateway backend.
#[derive(Default, Clone, Copy)]
pub struct NoopSecurityGateway;

#[async_trait]
impl SecurityGatewayPort for NoopSecurityGateway {
    async fn check(&self, _request: SecurityCheckRequest) -> SecurityVerdict {
        debug!("NoopSecurityGateway::check called; allowing (no backend configured)");
        SecurityVerdict::Allow { task_id: None }
    }
}

/// Returns a fixed verdict regardless of the request. Test double.
#[derive(Clone)]
pub struct FakeSecurityGateway {
    verdict: SecurityVerdict,
}

impl FakeSecurityGateway {
    pub fn new(verdict: SecurityVerdict) -> Self {
        Self { verdict }
    }

    /// Convenience: a fake that always allows (no replacement task id).
    pub fn allow() -> Self {
        Self::new(SecurityVerdict::Allow { task_id: None })
    }

    /// Convenience: a fake that always denies with the given code/message.
    pub fn deny(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::new(SecurityVerdict::Deny {
            code: code.into(),
            message: message.into(),
        })
    }
}

#[async_trait]
impl SecurityGatewayPort for FakeSecurityGateway {
    async fn check(&self, _request: SecurityCheckRequest) -> SecurityVerdict {
        self.verdict.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_request() -> SecurityCheckRequest {
        SecurityCheckRequest {
            sender_bot_id: "bot_a".into(),
            receiver_bot_id: "bot_b".into(),
            sender_agent_code: Some("agent_a".into()),
            receiver_agent_code: Some("agent_b".into()),
            agent_token: None,
            message_content: "hi".into(),
            message_id: "msg_1".into(),
        }
    }

    #[tokio::test]
    async fn noop_always_allows() {
        let gw = NoopSecurityGateway;
        assert!(matches!(
            gw.check(sample_request()).await,
            SecurityVerdict::Allow { task_id: None }
        ));
    }

    #[tokio::test]
    async fn fake_returns_configured_verdict() {
        let gw = FakeSecurityGateway::deny("000_200_101", "黑名单拦截");
        match gw.check(sample_request()).await {
            SecurityVerdict::Deny { code, message } => {
                assert_eq!(code, "000_200_101");
                assert_eq!(message, "黑名单拦截");
            }
            _ => panic!("expected Deny"),
        }
    }
}
