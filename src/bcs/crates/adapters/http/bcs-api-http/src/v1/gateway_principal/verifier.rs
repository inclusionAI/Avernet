use bcs_service_api::application::v1::{
    AuthenticatedAccessKeyIdentity, AuthenticatedAppIdentity, AuthenticatedBotIdentity,
    AuthenticatedCaller, AuthenticatedUserIdentity,
};
use jsonwebtoken::{Algorithm, DecodingKey, Validation, decode, decode_header};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};

use super::wire::{GatewayClaims, GatewayPrincipal};

pub struct GatewayPrincipalTrust {
    issuer: String,
    audience: String,
    key_id: String,
}

impl GatewayPrincipalTrust {
    pub fn new(
        issuer: impl Into<String>,
        audience: impl Into<String>,
        key_id: impl Into<String>,
    ) -> Result<Self, GatewayPrincipalVerifierBuildError> {
        Ok(Self {
            issuer: issuer.into(),
            audience: audience.into(),
            key_id: key_id.into(),
        })
    }
}

pub struct GatewayPrincipalTokenVerifier {
    decoding_key: DecodingKey,
    trust: GatewayPrincipalTrust,
}

impl GatewayPrincipalTokenVerifier {
    pub fn new(
        signing_key: &[u8],
        trust: GatewayPrincipalTrust,
    ) -> Result<Self, GatewayPrincipalVerifierBuildError> {
        Ok(Self {
            decoding_key: DecodingKey::from_secret(signing_key),
            trust,
        })
    }

    pub fn verify(
        &self,
        token: &str,
    ) -> Result<AuthenticatedCaller, GatewayPrincipalVerificationError> {
        let now = u64::try_from(OffsetDateTime::now_utc().unix_timestamp())
            .map_err(|_| GatewayPrincipalVerificationError::InvalidClaims)?;
        self.verify_at(token, now)
    }

    pub(super) fn verify_at(
        &self,
        token: &str,
        _now: u64,
    ) -> Result<AuthenticatedCaller, GatewayPrincipalVerificationError> {
        let header =
            decode_header(token).map_err(|_| GatewayPrincipalVerificationError::InvalidHeader)?;
        if header.alg != Algorithm::HS256 {
            return Err(GatewayPrincipalVerificationError::UnsupportedAlgorithm);
        }
        if header.typ.as_deref() != Some("JWT") {
            return Err(GatewayPrincipalVerificationError::InvalidTokenType);
        }
        if header.kid.as_deref() != Some(self.trust.key_id.as_str()) {
            return Err(GatewayPrincipalVerificationError::InvalidKeyId);
        }

        let mut validation = Validation::new(Algorithm::HS256);
        validation.set_audience(&[&self.trust.audience]);
        validation.set_issuer(&[&self.trust.issuer]);
        validation.set_required_spec_claims(&["exp", "iss", "aud"]);
        validation.validate_exp = false;

        let claims = decode::<GatewayClaims>(token, &self.decoding_key, &validation)
            .map_err(|error| match error.kind() {
                jsonwebtoken::errors::ErrorKind::InvalidSignature => {
                    GatewayPrincipalVerificationError::InvalidSignature
                }
                _ => GatewayPrincipalVerificationError::InvalidClaims,
            })?
            .claims;

        if claims.iss != self.trust.issuer
            || claims.aud != self.trust.audience
            || claims.iat >= claims.exp
        {
            return Err(GatewayPrincipalVerificationError::InvalidClaims);
        }

        project_principals(claims.principals)
    }
}

fn project_principals(
    principals: Vec<GatewayPrincipal>,
) -> Result<AuthenticatedCaller, GatewayPrincipalVerificationError> {
    let mut caller = AuthenticatedCaller {
        tenant: String::new(),
        user: None,
        bot: None,
        app: None,
        access_key: None,
    };

    for principal in principals {
        match principal {
            GatewayPrincipal::User { tenant, subject } => {
                caller.tenant = tenant;
                let _ = subject.tenant_id;
                caller.user = Some(AuthenticatedUserIdentity {
                    id: subject.id,
                    username: subject.username,
                    display_name: subject.display_name,
                    full_name: subject.full_name,
                });
            }
            GatewayPrincipal::Bot { tenant, bot } => {
                caller.tenant = tenant;
                let _ = bot.tenant;
                caller.bot = Some(AuthenticatedBotIdentity {
                    bot_uuid: bot.bot_uuid,
                    owner_id: bot.owner_id,
                    app_id: bot.app_id,
                    agent_code: bot.agent_code,
                });
            }
            GatewayPrincipal::App { tenant, app } => {
                caller.tenant = tenant;
                let _ = app.tenant;
                caller.app = Some(AuthenticatedAppIdentity {
                    app_id: app.app_id,
                    app_name: app.app_name,
                    owners: app.owners,
                    app_type: app.app_type,
                });
            }
            GatewayPrincipal::AccessKey { tenant, access_key } => {
                caller.tenant = tenant;
                let expire_at = OffsetDateTime::parse(&access_key.expire_at, &Rfc3339)
                    .map_err(|_| GatewayPrincipalVerificationError::InvalidPrincipalSet)?;
                caller.access_key = Some(AuthenticatedAccessKeyIdentity {
                    access_key: access_key.access_key,
                    expire_at,
                });
            }
        }
    }

    Ok(caller)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum GatewayPrincipalVerifierBuildError {
    #[error("Gateway Principal signing key is empty")]
    EmptySigningKey,
    #[error("Gateway Principal trust configuration is invalid")]
    InvalidTrustConfiguration,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum GatewayPrincipalVerificationError {
    #[error("Gateway Principal token is empty")]
    EmptyToken,
    #[error("Gateway Principal token header is invalid")]
    InvalidHeader,
    #[error("Gateway Principal token algorithm is unsupported")]
    UnsupportedAlgorithm,
    #[error("Gateway Principal token type is invalid")]
    InvalidTokenType,
    #[error("Gateway Principal token key id is invalid")]
    InvalidKeyId,
    #[error("Gateway Principal token signature is invalid")]
    InvalidSignature,
    #[error("Gateway Principal token claims are invalid")]
    InvalidClaims,
    #[error("Gateway Principal set is invalid")]
    InvalidPrincipalSet,
}
