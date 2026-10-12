//! Trusted team-manager service credentials (plan Task 13, spec §6.1,
//! Gate 0 record in spec §1.3).
//!
//! Pure HS256 boundary — mirroring `group_session.rs` — with a DEDICATED
//! purpose ([`TEAM_MANAGER_CREDENTIAL_PURPOSE`]): a group-session,
//! session, or register token signed with the same key never verifies
//! here (purpose/claim-shape isolation), and vice versa.
//!
//! Signing issues credentials for the trusted platform services; only
//! the verification path is security-relevant to BCS. Verification yields
//! the transport-neutral `VerifiedTeamManagerService` (service id, env,
//! and the verified Bot/team/operation allow scopes) through the
//! [`TeamManagerCredentialVerifierPort`] port — the credential string never
//! enters audit rows, business logs, or persisted commands, and neither
//! the signing key nor the credential value is ever included in an error
//! message.

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

use bcs_service_api::port::TeamManagerCredentialVerifierPort;
use bcs_service_api::types::error::{AuthorityError, ServiceError, ServiceResult};
use bcs_service_api::types::team_manager_sync::{
    TeamManagerOperation, VerifiedTeamManagerService,
};

type HmacSha256 = Hmac<Sha256>;

/// The dedicated purpose claim of every team-manager service credential
/// (isolated from the register/session and group-session purposes).
pub const TEAM_MANAGER_CREDENTIAL_PURPOSE: &str = "team_manager_sync";

const ISSUER: &str = "bcn";
const AUDIENCE: &str = "bcn-team-manager-sync";
/// Bound on the compact credential length; a longer input never reaches
/// HMAC verification.
const MAX_CREDENTIAL_LEN: usize = 4_096;
/// Bound on individual claim strings (service id, env, scope entries).
const MAX_CLAIM_LEN: usize = 256;
/// Bound on scope-list lengths inside one credential.
const MAX_SCOPE_LIST_LEN: usize = 1_000;

#[derive(Debug, thiserror::Error)]
pub enum TeamManagerCredentialSignError {
    #[error("team-manager credential signing key is empty")]
    EmptySigningKey,
    #[error("team-manager credential scopes are invalid: {0}")]
    InvalidScopes(String),
    #[error("team-manager credential encoding failed")]
    Encoding,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct JwtHeader {
    alg: String,
    typ: String,
}

/// The service identity + scopes signed into one credential.
///
/// `allowed_*` are allow-lists: `None` means unrestricted within `env`,
/// `Some` lists the exact allowed values. `allowed_operations` entries
/// are the operation KINDS (`"sync"` / `"move"`) per the Gate 0
/// credential vocabulary — a move credential admits any move target that
/// the team scopes permit.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TeamManagerServiceScopes {
    /// Verified platform service id (the value recorded in the `service`
    /// audit actor).
    pub service_id: String,
    /// Binding environment of the credential.
    pub env: String,
    /// Bot ids this credential may synchronize; `None` = unrestricted
    /// within `env`.
    pub allowed_bots: Option<Vec<String>>,
    /// URL teams this credential may manage; `None` = unrestricted
    /// within `env`.
    pub allowed_teams: Option<Vec<String>>,
    /// Operation kinds this credential may run; `None` = unrestricted.
    pub allowed_operations: Option<Vec<String>>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct TeamManagerWireClaims {
    iss: String,
    aud: String,
    purpose: String,
    sub: String,
    env: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    allowed_bots: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    allowed_teams: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    allowed_operations: Option<Vec<String>>,
    iat: u64,
    exp: u64,
}

/// Pure HS256 verifier of trusted team-manager service credentials. Holds
/// only the HMAC key; no Debug impl, so the key can never leak through a
/// debug log line.
pub struct TeamManagerJwtVerifier {
    secret: Vec<u8>,
}

impl TeamManagerJwtVerifier {
    /// Build the verifier from injected signing-key material. Blank
    /// material is a configuration error (fail closed at startup), never
    /// a verifier that accepts unsigned traffic.
    pub fn new(signing_key: &str) -> Result<Self, String> {
        if signing_key.trim().is_empty() {
            return Err(
                "team-manager credential signing key is empty; refusing to build a \
                 verifier that would accept unsigned sync traffic"
                    .to_string(),
            );
        }
        Ok(Self {
            secret: signing_key.as_bytes().to_vec(),
        })
    }

    /// Issue one credential for `scopes` (used by the platform's credential
    /// issuer and tests; verification below is the security boundary).
    pub fn sign_service_credential(
        &self,
        scopes: &TeamManagerServiceScopes,
        issued_at: u64,
        ttl_seconds: u64,
    ) -> Result<String, TeamManagerCredentialSignError> {
        validate_scopes(scopes)?;
        let exp = issued_at
            .checked_add(ttl_seconds)
            .ok_or(TeamManagerCredentialSignError::InvalidScopes(
                "ttl overflows the credential clock".to_string(),
            ))?;
        if issued_at >= exp {
            return Err(TeamManagerCredentialSignError::InvalidScopes(
                "ttl must be positive".to_string(),
            ));
        }
        let claims = TeamManagerWireClaims {
            iss: ISSUER.into(),
            aud: AUDIENCE.into(),
            purpose: TEAM_MANAGER_CREDENTIAL_PURPOSE.into(),
            sub: scopes.service_id.clone(),
            env: scopes.env.clone(),
            allowed_bots: scopes.allowed_bots.clone(),
            allowed_teams: scopes.allowed_teams.clone(),
            allowed_operations: scopes.allowed_operations.clone(),
            iat: issued_at,
            exp,
        };
        let header = JwtHeader {
            alg: "HS256".into(),
            typ: "JWT".into(),
        };
        let header_json =
            serde_json::to_vec(&header).map_err(|_| TeamManagerCredentialSignError::Encoding)?;
        let claims_json =
            serde_json::to_vec(&claims).map_err(|_| TeamManagerCredentialSignError::Encoding)?;
        let header_b64 = URL_SAFE_NO_PAD.encode(header_json);
        let claims_b64 = URL_SAFE_NO_PAD.encode(claims_json);
        let signing_input = format!("{header_b64}.{claims_b64}");
        let mut mac = HmacSha256::new_from_slice(&self.secret)
            .map_err(|_| TeamManagerCredentialSignError::Encoding)?;
        mac.update(signing_input.as_bytes());
        let signature = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());
        let credential = format!("{signing_input}.{signature}");
        if credential.len() > MAX_CREDENTIAL_LEN {
            return Err(TeamManagerCredentialSignError::InvalidScopes(
                "scopes exceed the compact credential size bound".to_string(),
            ));
        }
        Ok(credential)
    }

    /// Verify one credential at `now` (the clock injectable for tests).
    /// Every failure branch is [`ServiceError::Authority`] with
    /// `AuthorityError::Forbidden` — fail-closed, and the fixed message
    /// never embeds the credential or the signing key.
    pub fn verify_service_credential_at(
        &self,
        credential: &str,
        now: u64,
    ) -> ServiceResult<VerifiedTeamManagerService> {
        if credential.is_empty() || credential.len() > MAX_CREDENTIAL_LEN {
            return Err(rejected("malformed team-manager credential"));
        }
        let mut parts = credential.split('.');
        let (Some(header_b64), Some(claims_b64), Some(signature_b64), None) = (
            parts.next(),
            parts.next(),
            parts.next(),
            parts.next(),
        ) else {
            return Err(rejected("malformed team-manager credential"));
        };

        let header_bytes = URL_SAFE_NO_PAD
            .decode(header_b64)
            .map_err(|_| rejected("malformed team-manager credential"))?;
        let header: JwtHeader = serde_json::from_slice(&header_bytes)
            .map_err(|_| rejected("malformed team-manager credential"))?;
        if header.alg != "HS256" || header.typ != "JWT" {
            return Err(rejected("unsupported team-manager credential header"));
        }

        let signature = URL_SAFE_NO_PAD
            .decode(signature_b64)
            .map_err(|_| rejected("malformed team-manager credential"))?;
        let signing_input = format!("{header_b64}.{claims_b64}");
        let mut mac = HmacSha256::new_from_slice(&self.secret)
            .map_err(|_| ServiceError::InternalError("HMAC init failed".to_string()))?;
        mac.update(signing_input.as_bytes());
        mac.verify_slice(&signature)
            .map_err(|_| rejected("invalid team-manager credential signature"))?;

        let claims_bytes = URL_SAFE_NO_PAD
            .decode(claims_b64)
            .map_err(|_| rejected("malformed team-manager credential claims"))?;
        let claims: TeamManagerWireClaims = serde_json::from_slice(&claims_bytes)
            .map_err(|_| rejected("team-manager credential claims rejected"))?;
        // Purpose isolation (Gate 0): only the dedicated team purpose is
        // accepted — register/session/group-session tokens fail here even
        // when signed with the same key.
        if claims.iss != ISSUER
            || claims.aud != AUDIENCE
            || claims.purpose != TEAM_MANAGER_CREDENTIAL_PURPOSE
        {
            return Err(rejected("credential purpose does not authorize team manager sync"));
        }
        if claims.iat >= claims.exp {
            return Err(rejected("team-manager credential lifetime is invalid"));
        }
        if claims.iat > now {
            return Err(rejected("team-manager credential is not valid yet"));
        }
        if claims.exp <= now {
            return Err(rejected("team-manager credential has expired"));
        }

        let service_id = non_blank("service id", &claims.sub)?;
        let env = non_blank("env", &claims.env)?;
        let allowed_bots = claims
            .allowed_bots
            .map(|list| scope_list("allowed bots", list))
            .transpose()?;
        let allowed_teams = claims
            .allowed_teams
            .map(|list| scope_list("allowed teams", list))
            .transpose()?;
        let allowed_operations = claims
            .allowed_operations
            .map(|list| parse_operations(list))
            .transpose()?;

        Ok(VerifiedTeamManagerService {
            service_id,
            env,
            allowed_bots,
            allowed_teams,
            allowed_operations,
        })
    }
}

/// The port adapter in front of the system clock.
impl TeamManagerCredentialVerifierPort for TeamManagerJwtVerifier {
    fn verify(&self, credential: &str) -> ServiceResult<VerifiedTeamManagerService> {
        self.verify_service_credential_at(credential, crate::now_secs())
    }
}

fn rejected(reason: &'static str) -> ServiceError {
    ServiceError::Authority(AuthorityError::Forbidden(reason.to_string()))
}

fn non_blank(field: &'static str, value: &str) -> ServiceResult<String> {
    if value.trim().is_empty() {
        return Err(rejected("team-manager credential claim is blank"));
    }
    if value.chars().count() > MAX_CLAIM_LEN {
        return Err(rejected("team-manager credential claim exceeds its bound"));
    }
    let _ = field;
    Ok(value.to_string())
}

fn scope_list(field: &'static str, list: Vec<String>) -> ServiceResult<Vec<String>> {
    if list.len() > MAX_SCOPE_LIST_LEN {
        return Err(rejected("team-manager credential scope list exceeds its bound"));
    }
    for entry in &list {
        // Blank entries are rejected: a scope list never carries a
        // catch-all or a defaulted value.
        non_blank(field, entry)?;
    }
    Ok(list)
}

/// Parse operation KINDS ("sync"/"move") out of the credential scopes. A
/// `move` scope is stored as a kind-tagged operation whose target stays
/// unrestricted: targets are bounded by the team scopes, never by this
/// enum variant (see `VerifiedTeamManagerService::authorize_sync`).
fn parse_operations(list: Vec<String>) -> ServiceResult<Vec<TeamManagerOperation>> {
    let parsed = scope_list("allowed operations", list)?
        .into_iter()
        .map(|kind| match kind.as_str() {
            "sync" => Ok(TeamManagerOperation::Sync),
            "move" => Ok(TeamManagerOperation::Move {
                new_team_id: String::new(),
            }),
            _ => Err(rejected("unknown team operation scope value")),
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(parsed)
}

fn validate_scopes(scopes: &TeamManagerServiceScopes) -> Result<(), TeamManagerCredentialSignError> {
    let invalid = |detail: String| {
        Err(TeamManagerCredentialSignError::InvalidScopes(detail))
    };
    if scopes.service_id.trim().is_empty() {
        return invalid("service_id must be non-blank".into());
    }
    if scopes.env.trim().is_empty() {
        return invalid("env must be non-blank".into());
    }
    for (name, list) in [
        ("allowed_bots", &scopes.allowed_bots),
        ("allowed_teams", &scopes.allowed_teams),
        ("allowed_operations", &scopes.allowed_operations),
    ] {
        if let Some(list) = list {
            if list.is_empty() || list.len() > MAX_SCOPE_LIST_LEN {
                return invalid(format!("{name} must carry 1..={MAX_SCOPE_LIST_LEN} entries"));
            }
            if list.iter().any(|value| value.trim().is_empty()) {
                return invalid(format!("{name} must not contain blank entries"));
            }
        }
    }
    if let Some(operations) = &scopes.allowed_operations
        && operations
            .iter()
            .any(|kind| kind != "sync" && kind != "move")
    {
        return invalid("allowed_operations entries must be sync or move".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blank_signing_key_fails_closed() {
        assert!(TeamManagerJwtVerifier::new("   ").is_err());
        assert!(TeamManagerJwtVerifier::new("real signing key").is_ok());
    }

    #[test]
    fn scopes_are_validated_at_signing() {
        let verifier =
            TeamManagerJwtVerifier::new("real signing key").expect("verifier builds");
        let mut blank_env = scopes();
        blank_env.env = "  ".to_string();
        assert!(verifier.sign_service_credential(&blank_env, 0, 60).is_err());
        let mut bad_operation = scopes();
        bad_operation.allowed_operations = Some(vec!["wield".to_string()]);
        assert!(verifier.sign_service_credential(&bad_operation, 0, 60).is_err());
        let mut empty_list = scopes();
        empty_list.allowed_bots = Some(vec![]);
        assert!(verifier.sign_service_credential(&empty_list, 0, 60).is_err());
    }

    fn scopes() -> TeamManagerServiceScopes {
        TeamManagerServiceScopes {
            service_id: "team-sync-1".to_string(),
            env: "test-env".to_string(),
            allowed_bots: Some(vec!["bot-a".to_string()]),
            allowed_teams: None,
            allowed_operations: Some(vec!["move".to_string()]),
        }
    }
}