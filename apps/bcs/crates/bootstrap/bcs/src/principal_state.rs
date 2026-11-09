//! Signed identity is fixed by Gateway; current persisted state remains authoritative.
use std::sync::Arc;
use async_trait::async_trait;
use axum::http::HeaderMap;
use bcs_api_http::{CredentialKind, PrincipalVerifier, PrincipalVerificationError, VerifiedRequestIdentity};
use bcs_service_api::{core::BotRegistryCoreService, ServiceError};

/// The outer verifier of the ordinary V1 chain (V1 API auth plan, spec
/// §7.4): it wraps the final composite and, after its Bot state checks,
/// returns the whole `VerifiedRequestIdentity` — caller plus provenance —
/// unchanged. The legacy `LegacyBrowserBoundary` marker belongs only to the
/// separate native Human router stack and is not accepted here.
pub(crate) struct StatefulPrincipalVerifier {
    pub inner: Arc<dyn PrincipalVerifier>,
    pub registry: Arc<dyn BotRegistryCoreService>,
}

#[async_trait]
impl PrincipalVerifier for StatefulPrincipalVerifier {
    async fn verify(&self, headers: &HeaderMap) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        let identity = self.inner.verify(headers).await?;
        if identity.authentication_context.credential_kind == CredentialKind::LegacyBrowserBoundary {
            return Err(PrincipalVerificationError::Internal(
                "legacy browser boundary is not accepted by the V1 authentication chain".into(),
            ));
        }
        if let Some(bot) = &identity.caller.bot {
            self.registry.validate_signed_bot(&bot.bot_uuid).await.map_err(|error| match error {
                ServiceError::Unauthorized(_) | ServiceError::Forbidden(_) | ServiceError::BotNotFound(_) =>
                    PrincipalVerificationError::Invalid("Bot is unavailable".into()),
                _ => PrincipalVerificationError::Unavailable,
            })?;
        }
        Ok(identity)
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use bcs_api_http::{CredentialKind, VerifiedRequestIdentity};
    use bcs_service_api::application::v1::{
        AuthenticatedBotIdentity, AuthenticatedCaller, AuthenticatedUserIdentity,
    };

    use super::*;

    struct FixedVerifier {
        identity: VerifiedRequestIdentity,
    }

    #[async_trait]
    impl PrincipalVerifier for FixedVerifier {
        async fn verify(
            &self,
            _headers: &HeaderMap,
        ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
            Ok(self.identity.clone())
        }
    }

    fn bot_caller() -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: None,
            bot: Some(AuthenticatedBotIdentity {
                bot_uuid: "bot-1".into(),
                owner_id: "owner-1".into(),
                app_id: 7,
                agent_code: "agent-code".into(),
            }),
            app: None,
            access_key: None,
        }
    }

    fn human_caller() -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: Some("tenant-a".into()),
            user: Some(AuthenticatedUserIdentity {
                id: "staff-1".into(),
                username: "alice".into(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        }
    }

    fn stateful(
        identity: VerifiedRequestIdentity,
    ) -> StatefulPrincipalVerifier {
        StatefulPrincipalVerifier {
            inner: Arc::new(FixedVerifier { identity }),
            registry: Arc::new(bcs_test_support::NoopBotRegistryCoreService),
        }
    }

    #[tokio::test]
    async fn returns_the_inner_delivery_identity_unchanged_after_bot_state_checks() {
        // The whole delivery identity — caller plus provenance — must come
        // back exactly as the composite pinned it (V1 API auth plan §7.4).
        // Human callers skip the Bot state recheck by design; the Bot path
        // and its error mapping are asserted below.
        let identity = VerifiedRequestIdentity::pin(
            human_caller(),
            "gateway",
            CredentialKind::GatewayPrincipalHeader,
        );
        let verified = stateful(identity)
            .verify(&HeaderMap::new())
            .await
            .expect("stateful verify");
        assert_eq!(verified.authentication_context.source, "gateway");
        assert_eq!(
            verified.authentication_context.credential_kind,
            CredentialKind::GatewayPrincipalHeader
        );
        assert_eq!(verified.caller.user.as_ref().expect("user").id, "staff-1");
    }

    #[tokio::test]
    async fn bot_state_recheck_failure_keeps_the_unavailable_mapping() {
        // A signed Bot principal makes the registry state recheck run; the
        // Noop registry is "not configured", which maps to the existing
        // Unavailable (502 / bot_identity_unavailable) error class.
        let identity = VerifiedRequestIdentity::pin(
            bot_caller(),
            "gateway",
            CredentialKind::GatewayPrincipalHeader,
        );
        let outcome = stateful(identity).verify(&HeaderMap::new()).await;
        assert!(matches!(
            outcome,
            Err(PrincipalVerificationError::Unavailable)
        ));
    }

    #[tokio::test]
    async fn the_v1_outer_gate_rejects_legacy_browser_boundary_credentials() {
        // The legacy marker belongs only to the separately-wired native
        // Human router; the ordinary V1 chain treats it as an internal
        // wiring fault (interfaces §Delivery).
        let identity = VerifiedRequestIdentity::pin(
            AuthenticatedCaller { tenant: None, user: None, bot: None, app: None, access_key: None },
            "legacy-browser",
            CredentialKind::LegacyBrowserBoundary,
        );
        let outcome = stateful(identity).verify(&HeaderMap::new()).await;
        assert!(matches!(
            outcome,
            Err(PrincipalVerificationError::Internal(_))
        ));
    }
}
