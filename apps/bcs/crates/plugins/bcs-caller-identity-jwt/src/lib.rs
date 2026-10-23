//! Default JWT implementation of `CallerIdentityPlugin` (spec §10.13.4).
//!
//! Trust profile, frozen by conformance in `tests/jwt_profile_conformance.rs`:
//! - mature JOSE encoding with an explicit asymmetric whitelist: EdDSA only;
//!   verification rejects any other declared algorithm before signature work.
//! - verifiers hold public keys only; `kid` selects from the configured
//!   trusted set, never from the token's own claims about where keys live.
//! - NumericDate is seconds on the wire; external deadlines are milliseconds.
//!   Conversions floor down so a proof never outlives its earliest deadline.
//! - JWS is readable, not encrypted: claims carry a scoped pseudonymous
//!   `caller` value, never a raw staff number, external identity or ingress credential.
//! - the pseudonym subject is a stable, env-separated HMAC over the verified
//!   principal, keyed by an independent key that is never derived from the
//!   signing keys; `jti` stays one-time and is never a container key.
//! - issuance and verification read only the explicit `now_ms` provided by
//!   the caller;时间段 comparisons are deterministic.
//!
//! Sample trust model (spec §10.13.0/B9): Gateway trusts the authenticated
//! BCS context and routes; it does not re-verify these tokens per task.
//! Receiving parties that do verify must be configured for this issuer and
//! profile — a valid JWT is never auto-trusted by anyone else.
//!
//! This implementation never decides routing, MCP grants or derivation
//! eligibility; those stay with BCS application/core checks (§10.13.6).

use std::collections::HashMap;

use async_trait::async_trait;
use hmac::{Hmac, Mac};
use jsonwebtoken::{decode, decode_header, encode, Algorithm, DecodingKey, EncodingKey, Header, Validation};
use serde::{Deserialize, Serialize};
use sha2::Sha256;

use bcs_caller_identity_api::{
    CallerExecutionPurpose, CallerExecutionToken, CallerProofFormat, CallerSubjectKind,
    CallerSubjectRef, CallerSubjectRefType,
};
use bcs_caller_identity_api::{
    CallerIdentityError, CallerIdentityPlugin, EstablishCallerSubject, EstablishedCallerSubject,
    IssueCallerExecution, IssueCallerExecutionSource, ParentExecutionProof, VerifyCallerExecution, VerifiedCallerExecution,
};

/// Minimum bytes for the independent pseudonym key (HMAC-SHA256 input).
pub const MIN_PSEUDONYM_KEY_BYTES: usize = 16;

const TOKEN_TYPE_EXECUTION: &str = "caller_execution";
const TOKEN_TYPE_SUBJECT: &str = "caller_subject";
const PURPOSE_SUBJECT_ESTABLISHMENT: &str = "subject_establishment";
const PURPOSE_EXECUTION: &str = "execution";
const PURPOSE_DELEGATION_ONLY: &str = "delegation_only";
/// Domain separation for the stable pseudonym derivation.
const PSEUDONYM_CONTEXT: &[u8] = b"bcs.caller.pseudonym.v1";
/// Audience of the subject-establishment proof. Distinct from any
/// `bot_runtime:*` execution audience, so a subject proof can never be
/// confused with an execution proof by audience alone.
const SUBJECT_AUDIENCE: &str = "bcs-caller-subject";

/// One trusted verification key: a key id plus its Ed25519 public PEM.
/// Deployed material comes from configuration; nothing is fetched by kid.
#[derive(Debug, Clone)]
pub struct TrustedPublicKey {
    pub kid: String,
    pub public_pem: String,
}

/// Configuration of the default JWT profile (spec §10.13.7). All values are
/// required; there is no working default when any of them is missing.
///
/// `pseudonym_key` must be independent key material — never derived from
/// `signing_key_pem` — and grants no signature capability.
pub struct JwtCallerIdentityConfig {
    pub issuer: String,
    /// Key id placed in issued JWT headers and located in `public_keys`
    /// for verification.
    pub signing_kid: String,
    /// EdDSA private key PEM (PKCS#8). Never logged; never persisted.
    pub signing_key_pem: String,
    /// Trusted verification set. A signing-only deployment still must list
    /// the verification keys it accepts; an absent kid never verifies.
    pub public_keys: Vec<TrustedPublicKey>,
    /// Independent pseudonym-derivation secret (>= [`MIN_PSEUDONYM_KEY_BYTES`]).
    pub pseudonym_key: Vec<u8>,
    /// Longest subject/proof lifetime in seconds (spec example: 300).
    pub max_ttl_seconds: u64,
}

/// Closed configuration validation errors. `kid` values may appear in errors;
/// key bytes and PEM material never do.
#[derive(Debug, thiserror::Error)]
pub enum JwtCallerIdentityConfigError {
    #[error("caller identity config: issuer must not be empty")]
    EmptyIssuer,
    #[error("caller identity config: signing key id must not be empty")]
    EmptySigningKid,
    #[error("caller identity config: invalid EdDSA signing key")]
    InvalidSigningKey,
    #[error("caller identity config: invalid EdDSA public key (kid {kid:?})")]
    InvalidPublicKey { kid: String },
    #[error("caller identity config: a key id must not be empty")]
    EmptyKid,
    #[error("caller identity config: trusted verification key set must not be empty")]
    EmptyTrustSet,
    #[error("caller identity config: duplicate trusted key id (kid {kid:?})")]
    DuplicateKid { kid: String },
    #[error("caller identity config: at least {MIN_PSEUDONYM_KEY_BYTES} pseudonym key bytes are required")]
    WeakPseudonymKey,
    #[error("caller identity config: max ttl seconds must be positive")]
    InvalidMaxTtl,
}

/// The default JWT `CallerIdentityPlugin`.
pub struct JwtCallerIdentityPlugin {
    issuer: String,
    signing_kid: String,
    signing_key: EncodingKey,
    verify_keys: HashMap<String, DecodingKey>,
    pseudonym_key: Vec<u8>,
    max_ttl_ms: u64,
}

#[derive(Serialize, Deserialize)]
struct CallerJwtClaims {
    iss: String,
    aud: String,
    /// Cross-JWT confusion guard: `caller_execution` or `caller_subject`.
    token_type: String,
    jti: String,
    /// Scoped pseudonymous subject — never raw Human identity material.
    caller: String,
    /// `subject_establishment` | `execution` | `delegation_only`.
    purpose: String,
    env: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    run_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    root_run_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    parent_run_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    message_turn_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    target_bot_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    group_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    session_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    attempt: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    delegation_depth: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    max_delegation_depth: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    binding_ref: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    binding_generation: Option<u32>,
    /// NumericDate seconds.
    iat: u64,
    /// NumericDate seconds; floored from the earliest millisecond deadline.
    exp: u64,
}

fn floor_to_seconds(ms: i64) -> u64 {
    (ms.max(0) / 1000) as u64
}

fn ms_from_seconds(seconds: u64) -> i64 {
    seconds.saturating_mul(1000) as i64
}

fn parse_purpose(value: &str) -> Option<CallerExecutionPurpose> {
    match value {
        PURPOSE_SUBJECT_ESTABLISHMENT => Some(CallerExecutionPurpose::SubjectEstablishment),
        PURPOSE_EXECUTION => Some(CallerExecutionPurpose::Execution),
        PURPOSE_DELEGATION_ONLY => Some(CallerExecutionPurpose::DelegationOnly),
        _ => None,
    }
}

impl JwtCallerIdentityPlugin {
    /// Validate the whole configuration and build the plugin. Fails closed:
    /// an unready caller identity capability never falls back to a
    /// permissive default.
    pub fn new(config: JwtCallerIdentityConfig) -> Result<Self, JwtCallerIdentityConfigError> {
        if config.issuer.is_empty() {
            return Err(JwtCallerIdentityConfigError::EmptyIssuer);
        }
        if config.signing_kid.is_empty() {
            return Err(JwtCallerIdentityConfigError::EmptySigningKid);
        }
        if config.pseudonym_key.len() < MIN_PSEUDONYM_KEY_BYTES {
            return Err(JwtCallerIdentityConfigError::WeakPseudonymKey);
        }
        if config.max_ttl_seconds == 0 {
            return Err(JwtCallerIdentityConfigError::InvalidMaxTtl);
        }
        let signing_key = EncodingKey::from_ed_pem(config.signing_key_pem.as_bytes())
            .map_err(|_| JwtCallerIdentityConfigError::InvalidSigningKey)?;
        if config.public_keys.is_empty() {
            return Err(JwtCallerIdentityConfigError::EmptyTrustSet);
        }
        let mut verify_keys = HashMap::with_capacity(config.public_keys.len());
        for key in &config.public_keys {
            if key.kid.is_empty() {
                return Err(JwtCallerIdentityConfigError::EmptyKid);
            }
            if verify_keys.contains_key(&key.kid) {
                return Err(JwtCallerIdentityConfigError::DuplicateKid { kid: key.kid.clone() });
            }
            let decoding = DecodingKey::from_ed_pem(key.public_pem.as_bytes()).map_err(|_| {
                JwtCallerIdentityConfigError::InvalidPublicKey { kid: key.kid.clone() }
            })?;
            verify_keys.insert(key.kid.clone(), decoding);
        }
        Ok(Self {
            issuer: config.issuer,
            signing_kid: config.signing_kid,
            signing_key,
            verify_keys,
            pseudonym_key: config.pseudonym_key,
            max_ttl_ms: config.max_ttl_seconds.saturating_mul(1000),
        })
    }

    /// Stable, env-separated pseudonymous subject for a verified principal.
    /// HMAC-SHA256 over a domain-separated context; the pseudonym key is
    /// independent key material and grants no signature capability.
    fn pseudonym(&self, env: &str, actor_id: &str) -> Result<String, CallerIdentityError> {
        let mut mac = Hmac::<Sha256>::new_from_slice(&self.pseudonym_key)
            .map_err(|_| CallerIdentityError::IdentityProviderUnavailable)?;
        mac.update(PSEUDONYM_CONTEXT);
        mac.update(&[0]);
        mac.update(env.as_bytes());
        mac.update(&[0]);
        mac.update(actor_id.as_bytes());
        let digest = mac.finalize().into_bytes();
        Ok(digest.iter().map(|b| format!("{b:02x}")).collect())
    }

    fn sign(&self, claims: &CallerJwtClaims) -> Result<String, CallerIdentityError> {
        let mut header = Header::new(Algorithm::EdDSA);
        header.kid = Some(self.signing_kid.clone());
        encode(&header, claims, &self.signing_key)
            .map_err(|_| CallerIdentityError::IdentityProviderUnavailable)
    }

    /// Authenticate and hydrate an execution token's claims without shared
    /// DB/cache state. BCS Material Ledger issue requests use this only to
    /// exchange a real parent token; verified claims never leave this plugin.
    fn decode_live_execution(
        &self,
        token: &CallerExecutionToken,
        now_ms: i64,
    ) -> Result<(CallerJwtClaims, i64), CallerIdentityError> {
        if token.is_expired(now_ms) {
            return Err(CallerIdentityError::TokenExpired);
        }
        let header = decode_header(token.expose_token()).map_err(|_| CallerIdentityError::TokenInvalid)?;
        if header.alg != Algorithm::EdDSA {
            return Err(CallerIdentityError::TokenInvalid);
        }
        let Some(kid) = header.kid else {
            return Err(CallerIdentityError::TokenInvalid);
        };
        let Some(key) = self.verify_keys.get(&kid) else {
            return Err(CallerIdentityError::TokenInvalid);
        };
        let mut validation = Validation::new(Algorithm::EdDSA);
        validation.validate_aud = false;
        validation.validate_exp = false;
        validation.leeway = 0;
        validation.required_spec_claims.clear();
        let claims = decode::<CallerJwtClaims>(token.expose_token(), key, &validation)
            .map_err(|_| CallerIdentityError::TokenInvalid)?
            .claims;
        if claims.iss != self.issuer || claims.token_type != TOKEN_TYPE_EXECUTION {
            return Err(CallerIdentityError::TokenInvalid);
        }
        if ms_from_seconds(claims.iat) > now_ms || now_ms >= ms_from_seconds(claims.exp) {
            return Err(CallerIdentityError::TokenExpired);
        }
        let expires_at_ms = ms_from_seconds(claims.exp).min(token.expires_at_ms());
        Ok((claims, expires_at_ms))
    }

    #[allow(clippy::too_many_arguments)]
    fn validate_parent_source<'source>(
        &self,
        run: &bcs_caller_identity_api::CallerRunBinding,
        scope: &bcs_caller_identity_api::CallerProofScope,
        subject: &CallerSubjectRef,
        delegation: bcs_caller_identity_api::CallerDelegationBounds,
        token: &CallerExecutionToken,
        parent_run_id: &str,
        parent_attempt: u32,
        now_ms: i64,
    ) -> Result<i64, CallerIdentityError> {
        if run.parent_run_id.as_deref() != Some(parent_run_id)
            || run.root_run_id.is_empty()
            || parent_run_id.is_empty()
            || parent_attempt == 0
        {
            return Err(CallerIdentityError::TokenInvalid);
        }
        let (claims, source_expiry) = self.decode_live_execution(token, now_ms)?;
        if claims.run_id.as_deref() != Some(parent_run_id)
            || claims.root_run_id.as_deref() != Some(run.root_run_id.as_str())
            || claims.attempt != Some(parent_attempt)
            || claims.message_turn_id != run.message_turn_id
        {
            return Err(CallerIdentityError::TokenInvalid);
        }
        if claims.env != scope.env
            || claims.group_id != scope.group_id
            || claims.session_id.as_deref() != Some(scope.session_id.as_str())
        {
            return Err(CallerIdentityError::ScopeMismatch);
        }
        if parse_purpose(&claims.purpose) != Some(CallerExecutionPurpose::Execution) {
            return Err(CallerIdentityError::PurposeMismatch);
        }
        if claims.caller != subject.expose_ref() {
            return Err(CallerIdentityError::TokenInvalid);
        }
        let (Some(parent_depth), Some(parent_max_depth)) =
            (claims.delegation_depth, claims.max_delegation_depth) else {
            return Err(CallerIdentityError::TokenInvalid);
        };
        if Some(delegation.depth) != parent_depth.checked_add(1) || delegation.max_depth > parent_max_depth {
            return Err(CallerIdentityError::InvalidRequest);
        }
        let parent_source_expiry = source_expiry.min(token.expires_at_ms());
        if parent_source_expiry <= now_ms {
            return Err(CallerIdentityError::TokenExpired);
        }
        Ok(parent_source_expiry)
    }
}

fn execution_audience(target_bot_id: &str) -> String {
    format!("bot_runtime:{target_bot_id}")
}

#[async_trait]
impl CallerIdentityPlugin for JwtCallerIdentityPlugin {
    async fn establish_subject(
        &self,
        input: EstablishCallerSubject,
    ) -> Result<EstablishedCallerSubject, CallerIdentityError> {
        let ingress = input.ingress;
        let now_ms = input.now_ms;
        if now_ms <= 0 || now_ms < ingress.authenticated_at_ms() {
            return Err(CallerIdentityError::InvalidRequest);
        }
        if ingress.is_expired(now_ms) {
            return Err(CallerIdentityError::TokenExpired);
        }
        // Validity only shrinks: earliest of the trusted authentication
        // window and the profile TTL cap.
        let expiry = ingress
            .expires_at_ms()
            .min(now_ms.saturating_add(self.max_ttl_ms as i64));
        if (expiry / 1000) * 1000 <= now_ms {
            return Err(CallerIdentityError::TokenExpired);
        }
        let pseudonym = self.pseudonym(ingress.env(), &ingress.actor().actor_id)?;
        let claims = CallerJwtClaims {
            iss: self.issuer.clone(),
            aud: SUBJECT_AUDIENCE.to_string(),
            token_type: TOKEN_TYPE_SUBJECT.to_string(),
            jti: uuid::Uuid::new_v4().to_string(),
            caller: pseudonym.clone(),
            purpose: PURPOSE_SUBJECT_ESTABLISHMENT.to_string(),
            env: ingress.env().to_string(),
            run_id: None,
            root_run_id: None,
            parent_run_id: None,
            message_turn_id: None,
            target_bot_id: None,
            group_id: None,
            session_id: None,
            attempt: None,
            delegation_depth: None,
            max_delegation_depth: None,
            binding_ref: None,
            binding_generation: None,
            iat: floor_to_seconds(now_ms),
            exp: floor_to_seconds(expiry),
        };
        let proof = self.sign(&claims)?;
        let subject_ref = CallerSubjectRef::new(
            CallerSubjectKind::Human,
            CallerSubjectRefType::BcsEphemeral,
            pseudonym,
            expiry,
        )
        .map_err(|_| CallerIdentityError::InvalidRequest)?;
        let subject_proof =
            CallerExecutionToken::new(CallerProofFormat::Jwt, proof, expiry)
                .map_err(|_| CallerIdentityError::InvalidRequest)?;
        Ok(EstablishedCallerSubject {
            subject_ref,
            subject_proof,
            expires_at_ms: expiry,
        })
    }

    async fn issue_execution(
        &self,
        input: IssueCallerExecution<'_>,
    ) -> Result<CallerExecutionToken, CallerIdentityError> {
        let IssueCallerExecution {
            staff_no: _, agent_code: _, sandbox_container_id: _,
            subject,
            run,
            scope,
            delegation,
            purpose,
            not_after_ms,
            isolation,
            source,
            now_ms,
        } = input;
        // Structural guards: no auto-correction anywhere.
        if now_ms <= 0 || run.attempt == 0 {
            return Err(CallerIdentityError::InvalidRequest);
        }
        if run.run_id.is_empty()
            || run.root_run_id.is_empty()
            || scope.env.is_empty()
            || scope.target_bot_id.is_empty()
            || scope.session_id.is_empty()
        {
            return Err(CallerIdentityError::InvalidRequest);
        }
        if matches!(purpose, CallerExecutionPurpose::SubjectEstablishment) {
            return Err(CallerIdentityError::InvalidRequest);
        }
        if delegation.depth > delegation.max_depth {
            return Err(CallerIdentityError::InvalidRequest);
        }
        if let Some(binding) = &isolation {
            if binding.binding_ref.is_empty() {
                return Err(CallerIdentityError::InvalidRequest);
            }
        }
        if subject.is_expired(now_ms) {
            return Err(CallerIdentityError::TokenExpired);
        }
        if not_after_ms <= now_ms {
            return Err(CallerIdentityError::TokenExpired);
        }
        // BCS Material Ledger only: root human exchange, or exact parent
        // token exchange for the target chain.
        let source_expiry = match source {
            IssueCallerExecutionSource::HumanOriginHandle => subject.expires_at_ms(),
            IssueCallerExecutionSource::ParentExecutionToken {
                proof: ParentExecutionProof::Value { token: _source_only },
            } => return Err(CallerIdentityError::UpstreamEvidenceRequired),
            IssueCallerExecutionSource::ParentExecutionTokenRef {
                token,
                parent_run_id,
                parent_attempt,
            } => self
                .validate_parent_source(
                    &run,
                    &scope,
                    &subject,
                    delegation,
                    token,
                    parent_run_id,
                    parent_attempt,
                    now_ms,
                )
                .map_err(|_| CallerIdentityError::IdentityExchangeRejected)?,
        };
        // Earliest deadline wins; floors keep the wire NumericDate honest.
        let expiry = not_after_ms
            .min(source_expiry)
            .min(subject.expires_at_ms())
            .min(now_ms.saturating_add(self.max_ttl_ms as i64));
        if (expiry / 1000) * 1000 <= now_ms {
            return Err(CallerIdentityError::TokenExpired);
        }
        let claims = CallerJwtClaims {
            iss: self.issuer.clone(),
            aud: execution_audience(&scope.target_bot_id),
            token_type: TOKEN_TYPE_EXECUTION.to_string(),
            jti: uuid::Uuid::new_v4().to_string(),
            caller: subject.expose_ref().to_string(),
            purpose: match purpose {
                CallerExecutionPurpose::Execution => PURPOSE_EXECUTION,
                CallerExecutionPurpose::DelegationOnly => PURPOSE_DELEGATION_ONLY,
                CallerExecutionPurpose::SubjectEstablishment => {
                    return Err(CallerIdentityError::InvalidRequest)
                }
            }
            .to_string(),
            env: scope.env,
            run_id: Some(run.run_id),
            root_run_id: Some(run.root_run_id),
            parent_run_id: run.parent_run_id,
            message_turn_id: run.message_turn_id,
            target_bot_id: Some(scope.target_bot_id),
            group_id: scope.group_id,
            session_id: Some(scope.session_id),
            attempt: Some(run.attempt),
            delegation_depth: Some(delegation.depth),
            max_delegation_depth: Some(delegation.max_depth),
            binding_ref: isolation.as_ref().map(|b| b.binding_ref.clone()),
            binding_generation: isolation.as_ref().and_then(|b| b.generation),
            iat: floor_to_seconds(now_ms),
            exp: floor_to_seconds(expiry),
        };
        let token = self.sign(&claims)?;
        // The token's metadata validity must be the proof's real (floored)
        // validity, not the unaligned input deadline: a proof never outlives
        // its earliest deadline by rounding.
        let effective_ms = ms_from_seconds(floor_to_seconds(expiry));
        CallerExecutionToken::new(CallerProofFormat::Jwt, token, effective_ms)
            .map_err(|_| CallerIdentityError::InvalidRequest)
    }

    async fn verify_execution(
        &self,
        input: VerifyCallerExecution,
    ) -> Result<VerifiedCallerExecution, CallerIdentityError> {
        let VerifyCallerExecution { token, expected, now_ms } = input;
        if now_ms <= 0 {
            return Err(CallerIdentityError::InvalidRequest);
        }
        // Header first: algorithm and kid shape the trust decision before
        // any signature work; unknown kids never trigger key discovery.
        let header = decode_header(token.expose_token()).map_err(|_| CallerIdentityError::TokenInvalid)?;
        if header.alg != Algorithm::EdDSA {
            return Err(CallerIdentityError::TokenInvalid);
        }
        let kid = header.kid.ok_or(CallerIdentityError::TokenInvalid)?;
        let key = self
            .verify_keys
            .get(&kid)
            .ok_or(CallerIdentityError::TokenInvalid)?;

        let mut validation = Validation::new(Algorithm::EdDSA);
        // Audience, issuer, type, purpose, scope, run relation, freshness and
        // the ms/s boundary are enforced exactly, with the caller's clock.
        validation.validate_aud = false;
        validation.validate_exp = false;
        validation.leeway = 0;
        validation.required_spec_claims.clear();

        let claims = decode::<CallerJwtClaims>(token.expose_token(), key, &validation)
            .map_err(|_| CallerIdentityError::TokenInvalid)?
            .claims;

        if claims.iss != self.issuer || claims.token_type != TOKEN_TYPE_EXECUTION {
            return Err(CallerIdentityError::TokenInvalid);
        }
        let purpose = parse_purpose(&claims.purpose).ok_or(CallerIdentityError::TokenInvalid)?;
        if purpose != expected.purpose {
            return Err(CallerIdentityError::PurposeMismatch);
        }
        if let CallerExecutionPurpose::SubjectEstablishment = purpose {
            // An execution token never carries this purpose.
            return Err(CallerIdentityError::TokenInvalid);
        }
        if claims.aud != execution_audience(&expected.scope.target_bot_id) {
            return Err(CallerIdentityError::AudienceMismatch);
        }
        if claims.env != expected.scope.env
            || claims.session_id.as_deref() != Some(expected.scope.session_id.as_str())
            || claims.group_id != expected.scope.group_id
        {
            return Err(CallerIdentityError::ScopeMismatch);
        }
        if claims.run_id.as_deref() != Some(expected.run.run_id.as_str())
            || claims.root_run_id.as_deref() != Some(expected.run.root_run_id.as_str())
            || claims.parent_run_id != expected.run.parent_run_id
            || claims.message_turn_id != expected.run.message_turn_id
            || claims.attempt != Some(expected.run.attempt)
        {
            return Err(CallerIdentityError::TokenInvalid);
        }
        // Binding semantics are exact: present/absent and value-perfect. An
        // absent claim is precise absence, not a wildcard.
        match (&claims.binding_ref, &expected.isolation) {
            (Some(bound), Some(want)) if bound == &want.binding_ref
                && claims.binding_generation == want.generation => {}
            (None, None) => {}
            _ => return Err(CallerIdentityError::IsolationUnsupported),
        }
        // Freshness and expiry on the wire's second grid, rejecting anything
        // from the future and anything at or past the boundary.
        if ms_from_seconds(claims.iat) > now_ms {
            return Err(CallerIdentityError::TokenInvalid);
        }
        let expires_at_ms = ms_from_seconds(claims.exp);
        if now_ms >= expires_at_ms {
            return Err(CallerIdentityError::TokenExpired);
        }
        let subject_ref = CallerSubjectRef::new(
            CallerSubjectKind::Human,
            CallerSubjectRefType::BcsEphemeral,
            claims.caller,
            expires_at_ms,
        )
        .map_err(|_| CallerIdentityError::TokenInvalid)?;
        Ok(VerifiedCallerExecution {
            subject_ref,
            purpose,
            expires_at_ms,
        })
    }
}
mod factory;
