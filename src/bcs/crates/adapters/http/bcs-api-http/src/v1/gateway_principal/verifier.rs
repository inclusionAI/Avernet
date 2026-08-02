use bcs_service_api::application::v1::{
    AuthenticatedAccessKeyIdentity, AuthenticatedAppIdentity, AuthenticatedBotIdentity,
    AuthenticatedCaller, AuthenticatedUserIdentity,
};
use jsonwebtoken::{Algorithm, DecodingKey, Validation, decode, decode_header};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};

use super::wire::{GatewayClaims, GatewayPrincipal};

const CLOCK_SKEW_SECONDS: u64 = 5;

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
        let issuer = issuer.into();
        let audience = audience.into();
        let key_id = key_id.into();
        if !is_non_blank(&issuer) || !is_non_blank(&audience) || !is_non_blank(&key_id) {
            return Err(GatewayPrincipalVerifierBuildError::InvalidTrustConfiguration);
        }
        Ok(Self {
            issuer,
            audience,
            key_id,
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
        if signing_key.is_empty() {
            return Err(GatewayPrincipalVerifierBuildError::EmptySigningKey);
        }
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
        now: u64,
    ) -> Result<AuthenticatedCaller, GatewayPrincipalVerificationError> {
        if token.is_empty() {
            return Err(GatewayPrincipalVerificationError::EmptyToken);
        }
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
        validation.leeway = 0;
        validation.validate_exp = false;

        let claims = decode::<GatewayClaims>(token, &self.decoding_key, &validation)
            .map_err(|error| match error.kind() {
                jsonwebtoken::errors::ErrorKind::InvalidSignature => {
                    GatewayPrincipalVerificationError::InvalidSignature
                }
                _ => GatewayPrincipalVerificationError::InvalidClaims,
            })?
            .claims;

        if claims.iss != self.trust.issuer || claims.aud != self.trust.audience {
            return Err(GatewayPrincipalVerificationError::InvalidClaims);
        }
        validate_times(claims.iat, claims.exp, now)?;

        project_principals(claims.principals)
    }
}

fn validate_times(iat: u64, exp: u64, now: u64) -> Result<(), GatewayPrincipalVerificationError> {
    let latest_allowed_iat = now.saturating_add(CLOCK_SKEW_SECONDS);
    let expiration_with_skew = exp.saturating_add(CLOCK_SKEW_SECONDS);
    if iat >= exp || iat > latest_allowed_iat || now >= expiration_with_skew {
        return Err(GatewayPrincipalVerificationError::InvalidClaims);
    }
    Ok(())
}

fn is_non_blank(value: &str) -> bool {
    !value.trim().is_empty()
}

fn project_principals(
    principals: Vec<GatewayPrincipal>,
) -> Result<AuthenticatedCaller, GatewayPrincipalVerificationError> {
    let mut tenant = None;
    let mut user = None;
    let mut bot_identity = None;
    let mut app_identity = None;
    let mut access_key_identity = None;

    for principal in principals {
        match principal {
            GatewayPrincipal::User {
                tenant: outer_tenant,
                subject,
            } => {
                validate_tenant(&mut tenant, &outer_tenant)?;
                if user.is_some()
                    || !is_non_blank(&subject.id)
                    || !is_non_blank(&subject.username)
                    || subject.tenant_id.as_ref().is_some_and(|nested_tenant| {
                        !is_non_blank(nested_tenant) || nested_tenant != &outer_tenant
                    })
                {
                    return Err(GatewayPrincipalVerificationError::InvalidPrincipalSet);
                }
                user = Some(AuthenticatedUserIdentity {
                    id: subject.id,
                    username: subject.username,
                    display_name: subject.display_name,
                    full_name: subject.full_name,
                });
            }
            GatewayPrincipal::Bot {
                tenant: outer_tenant,
                bot,
            } => {
                validate_tenant(&mut tenant, &outer_tenant)?;
                if bot_identity.is_some()
                    || bot.tenant != outer_tenant
                    || !is_non_blank(&bot.bot_uuid)
                    || !is_non_blank(&bot.owner_id)
                    || !is_non_blank(&bot.agent_code)
                {
                    return Err(GatewayPrincipalVerificationError::InvalidPrincipalSet);
                }
                bot_identity = Some(AuthenticatedBotIdentity {
                    bot_uuid: bot.bot_uuid,
                    owner_id: bot.owner_id,
                    app_id: bot.app_id,
                    agent_code: bot.agent_code,
                });
            }
            GatewayPrincipal::App {
                tenant: outer_tenant,
                app,
            } => {
                validate_tenant(&mut tenant, &outer_tenant)?;
                if app_identity.is_some() || app.tenant != outer_tenant {
                    return Err(GatewayPrincipalVerificationError::InvalidPrincipalSet);
                }
                app_identity = Some(AuthenticatedAppIdentity {
                    app_id: app.app_id,
                    app_name: app.app_name,
                    owners: app.owners,
                    app_type: app.app_type,
                });
            }
            GatewayPrincipal::AccessKey {
                tenant: outer_tenant,
                access_key,
            } => {
                validate_tenant(&mut tenant, &outer_tenant)?;
                if access_key_identity.is_some() || !is_non_blank(&access_key.access_key) {
                    return Err(GatewayPrincipalVerificationError::InvalidPrincipalSet);
                }
                let expire_at = OffsetDateTime::parse(&access_key.expire_at, &Rfc3339)
                    .map_err(|_| GatewayPrincipalVerificationError::InvalidPrincipalSet)?;
                access_key_identity = Some(AuthenticatedAccessKeyIdentity {
                    access_key: access_key.access_key,
                    expire_at,
                });
            }
        }
    }

    Ok(AuthenticatedCaller {
        tenant: tenant.ok_or(GatewayPrincipalVerificationError::InvalidPrincipalSet)?,
        user,
        bot: bot_identity,
        app: app_identity,
        access_key: access_key_identity,
    })
}

fn validate_tenant(
    normalized: &mut Option<String>,
    candidate: &str,
) -> Result<(), GatewayPrincipalVerificationError> {
    if !is_non_blank(candidate)
        || normalized
            .as_ref()
            .is_some_and(|expected| expected != candidate)
    {
        return Err(GatewayPrincipalVerificationError::InvalidPrincipalSet);
    }
    if normalized.is_none() {
        *normalized = Some(candidate.to_owned());
    }
    Ok(())
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
