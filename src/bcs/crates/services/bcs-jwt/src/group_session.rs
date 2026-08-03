use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use bcs_service_api::application::v1::GROUP_SESSION_WS_TOKEN_TTL_SECONDS;
use bcs_service_api::port::{
    GroupSessionTokenClaims, GroupSessionTokenError, GroupSessionTokenPort,
    GroupSessionTokenScope, IssuedGroupSessionToken, GROUP_SESSION_TOKEN_MAX_COMPACT_LEN,
};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use time::OffsetDateTime;

type HmacSha256 = Hmac<Sha256>;

const ISSUER: &str = "bcn";
const AUDIENCE: &str = "bcn-group-session-ws";
const PURPOSE: &str = "group_session_ws";
const MAX_CLAIM_LEN: usize = 256;

#[derive(Debug, thiserror::Error)]
pub enum GroupSessionJwtBuildError {
    #[error("group-session JWT signing key is empty")]
    EmptySigningKey,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct JwtHeader {
    alg: String,
    typ: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct GroupSessionWireClaims {
    iss: String,
    aud: String,
    purpose: String,
    sub: String,
    tenant: String,
    uid: String,
    gid: String,
    sid: String,
    iat: u64,
    exp: u64,
}

pub struct GroupSessionJwtService {
    secret: Vec<u8>,
}

impl GroupSessionJwtService {
    pub fn new(secret: &str) -> Result<Self, GroupSessionJwtBuildError> {
        if secret.trim().is_empty() {
            return Err(GroupSessionJwtBuildError::EmptySigningKey);
        }
        Ok(Self {
            secret: secret.as_bytes().to_vec(),
        })
    }

    pub fn issue_at(
        &self,
        scope: GroupSessionTokenScope,
        ttl_seconds: u64,
        now: u64,
    ) -> Result<IssuedGroupSessionToken, GroupSessionTokenError> {
        if ttl_seconds != GROUP_SESSION_WS_TOKEN_TTL_SECONDS || !valid_scope(&scope) {
            return Err(GroupSessionTokenError::Invalid);
        }
        let exp = now
            .checked_add(ttl_seconds)
            .ok_or(GroupSessionTokenError::Invalid)?;
        let claims = GroupSessionWireClaims {
            iss: ISSUER.into(),
            aud: AUDIENCE.into(),
            purpose: PURPOSE.into(),
            sub: scope.user_id.clone(),
            tenant: scope.tenant,
            uid: scope.user_id,
            gid: scope.group_id,
            sid: scope.session_id,
            iat: now,
            exp,
        };
        let header = JwtHeader {
            alg: "HS256".into(),
            typ: "JWT".into(),
        };
        let header_json = serde_json::to_vec(&header)
            .map_err(|_| GroupSessionTokenError::Internal("header encoding failed".into()))?;
        let claims_json = serde_json::to_vec(&claims)
            .map_err(|_| GroupSessionTokenError::Internal("claims encoding failed".into()))?;
        let header_b64 = URL_SAFE_NO_PAD.encode(header_json);
        let claims_b64 = URL_SAFE_NO_PAD.encode(claims_json);
        let signing_input = format!("{header_b64}.{claims_b64}");
        let signature = self.sign(signing_input.as_bytes())?;
        let token = format!("{signing_input}.{}", URL_SAFE_NO_PAD.encode(signature));
        if token.len() > GROUP_SESSION_TOKEN_MAX_COMPACT_LEN {
            return Err(GroupSessionTokenError::Invalid);
        }
        let exp_i64 = i64::try_from(exp).map_err(|_| GroupSessionTokenError::Invalid)?;
        let expires_at = OffsetDateTime::from_unix_timestamp(exp_i64)
            .map_err(|_| GroupSessionTokenError::Invalid)?;
        Ok(IssuedGroupSessionToken { token, expires_at })
    }

    pub fn verify_at(
        &self,
        token: &str,
        now: u64,
    ) -> Result<GroupSessionTokenClaims, GroupSessionTokenError> {
        if token.is_empty() || token.len() > GROUP_SESSION_TOKEN_MAX_COMPACT_LEN {
            return Err(GroupSessionTokenError::Invalid);
        }
        let mut parts = token.split('.');
        let header_b64 = parts.next().ok_or(GroupSessionTokenError::Invalid)?;
        let claims_b64 = parts.next().ok_or(GroupSessionTokenError::Invalid)?;
        let signature_b64 = parts.next().ok_or(GroupSessionTokenError::Invalid)?;
        if parts.next().is_some()
            || header_b64.is_empty()
            || claims_b64.is_empty()
            || signature_b64.is_empty()
        {
            return Err(GroupSessionTokenError::Invalid);
        }

        let header_bytes = URL_SAFE_NO_PAD
            .decode(header_b64)
            .map_err(|_| GroupSessionTokenError::Invalid)?;
        let header: JwtHeader = serde_json::from_slice(&header_bytes)
            .map_err(|_| GroupSessionTokenError::Invalid)?;
        if header.alg != "HS256" || header.typ != "JWT" {
            return Err(GroupSessionTokenError::Invalid);
        }

        let signature = URL_SAFE_NO_PAD
            .decode(signature_b64)
            .map_err(|_| GroupSessionTokenError::Invalid)?;
        let signing_input = format!("{header_b64}.{claims_b64}");
        let mut mac = HmacSha256::new_from_slice(&self.secret)
            .map_err(|_| GroupSessionTokenError::Internal("HMAC initialization failed".into()))?;
        mac.update(signing_input.as_bytes());
        mac.verify_slice(&signature)
            .map_err(|_| GroupSessionTokenError::Invalid)?;

        let claims_bytes = URL_SAFE_NO_PAD
            .decode(claims_b64)
            .map_err(|_| GroupSessionTokenError::Invalid)?;
        let claims: GroupSessionWireClaims = serde_json::from_slice(&claims_bytes)
            .map_err(|_| GroupSessionTokenError::Invalid)?;
        if claims.iss != ISSUER
            || claims.aud != AUDIENCE
            || claims.purpose != PURPOSE
            || claims.sub != claims.uid
        {
            return Err(GroupSessionTokenError::Invalid);
        }
        let scope = GroupSessionTokenScope {
            tenant: claims.tenant,
            user_id: claims.uid,
            group_id: claims.gid,
            session_id: claims.sid,
        };
        if !valid_scope(&scope)
            || claims.iat >= claims.exp
            || claims.exp - claims.iat != GROUP_SESSION_WS_TOKEN_TTL_SECONDS
            || claims.iat > now
        {
            return Err(GroupSessionTokenError::Invalid);
        }
        if claims.exp <= now {
            return Err(GroupSessionTokenError::Expired);
        }
        Ok(GroupSessionTokenClaims {
            scope,
            issued_at: claims.iat,
            expires_at: claims.exp,
        })
    }

    fn sign(&self, input: &[u8]) -> Result<Vec<u8>, GroupSessionTokenError> {
        let mut mac = HmacSha256::new_from_slice(&self.secret)
            .map_err(|_| GroupSessionTokenError::Internal("HMAC initialization failed".into()))?;
        mac.update(input);
        Ok(mac.finalize().into_bytes().to_vec())
    }
}

impl GroupSessionTokenPort for GroupSessionJwtService {
    fn issue(
        &self,
        scope: GroupSessionTokenScope,
        ttl_seconds: u64,
    ) -> Result<IssuedGroupSessionToken, GroupSessionTokenError> {
        self.issue_at(scope, ttl_seconds, crate::now_secs())
    }

    fn verify(&self, token: &str) -> Result<GroupSessionTokenClaims, GroupSessionTokenError> {
        self.verify_at(token, crate::now_secs())
    }
}

fn valid_scope(scope: &GroupSessionTokenScope) -> bool {
    [&scope.tenant, &scope.user_id, &scope.group_id, &scope.session_id]
        .into_iter()
        .all(|value| !value.trim().is_empty() && value.chars().count() <= MAX_CLAIM_LEN)
}
