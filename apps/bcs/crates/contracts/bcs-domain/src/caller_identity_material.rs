//! Runtime-only caller identity envelopes (P0 freeze).
//!
//! Frozen against spec `2026-09-16-caller-identity-session-container`
//! §10.1.1 (`TrustedHumanIngressContext`), §10.5 (`CallerSubjectRef`) and
//! §10.13. This module is the counterpart of
//! [`crate::caller_identity`], which holds the persistable
//! non-identity control-plane contracts; everything here is
//! **in-memory trusted material** and must never reach a
//! database row, queue payload, message body, log line or model context.
//!
//! Boundary rules locked by this module:
//! - **No serde for identity material.** Except the existing public `HumanActor` DTO,
//!   none of the types below derive `Serialize`/`Deserialize`
//!   on purpose; serializing trusted Human identity material is a review bug
//!   (`RequestAuthHeaders` in `principal.rs` documents the same rule).
//! - **Redacted Debug.** The staff principal and the opaque subject reference
//!   never appear in `Debug` output.
//! - **Explicit identity propagation.** Ingress supports `Clone` for trusted
//!   WS connection snapshots; clones preserve the original expiry. Message
//!   command clones deliberately strip ingress. Execution forwarding moves
//!   it explicitly; handoff claim-once is enforced per scope, not by `Clone`.
//! - **Authorization validity only shrinks.** Ingress/subject windows originate
//!   from authentication. External credential expiry is reported separately;
//!   container TTLs and credential lifetimes never extend BCS authorization.
//!
//! The existing access-level `bcs_service_api::application::principal::CallerContext`
//! (Public/Human/Bot/Integration/Admin) stays untouched: it answers "who is
//! calling this HTTP/WS use case", while the types here answer "whose identity
//! may this execution use" — two different questions on purpose, and they
//! keep separate names to stay unmergeable.

use std::fmt;

/// Authenticated human principal shared by request and execution contracts.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct HumanActor {
    pub actor_id: String,
    pub staff_no: String,
}

/// Where the trusted Human authentication happened for this request. An
/// ingress always carries exactly one origin; IAM/Bearer sources that lack an
/// explicit trusted adapter are rejected at the auth chain, not defaulted
/// here.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum HumanCallerAuthSource {
    HttpAuth,
    WsAuth,
    ChannelIngress,
}

/// Kind of the caller subject. V1 is Human-only: Bot "callers" are not a
/// thing (Bot work keeps ordinary workload identity) and adding one later is
/// an explicit contract change, not a string.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CallerSubjectKind {
    Human,
}

/// How the opaque subject reference was established (spec §10.5). A tag for
/// trusted consumers only — never serialized onto the wire and never a
/// substitute for verifiable subject/proof binding.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CallerSubjectRefType {
    /// Default in-process scoped pseudonym (JWT profile).
    BcsEphemeral,
    /// Opaque lineage reference owned by the selected identity plugin.
    PluginLineageRef,
    /// Opaque reference to an externally established subject (never the raw
    /// external identity value).
    ExternalSubjectRef,
}

/// Purpose of an identity request or proof (spec §7.5 rule 7, §10.13).
/// `DelegationOnly` proves lineage membership for controlled child
/// derivation and must never satisfy a check that requires `Execution`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CallerExecutionPurpose {
    /// Establish a trusted subject from an authenticated principal.
    SubjectEstablishment,
    /// Execute the bound target task as the Human caller.
    Execution,
    /// Carry caller-aware lineage only (Owner bots in approved group
    /// collaboration); no Human MCP execution rights.
    DelegationOnly,
}

/// The strict Human caller ingress produced by the same authentication run
/// that resolves the display sender (spec §10.1.1, §10.13): the Rust shape
/// of `TrustedHumanIngressContext`. Constructed only after the auth chain
/// has verified the principal. The constructor validates shape, not
/// authentication: trusted adapters must never construct it from a request
/// body, display sender, DB lease holder or Bot owner.
///
/// Pairs with the message admission layer: the admission checks use the
/// actor and the trusted window, then hand this envelope to the same-term
/// memory handoff (§7.5.2). Not persisted, not logged, not forwarded.
/// `Clone` copies the runtime-only envelope for connection-scoped holders
/// (WS upgrade snapshots); every clone carries the same redaction rules —
/// still no serde, still a redacted `Debug`.
#[derive(Clone, PartialEq, Eq)]
pub struct AuthenticatedHumanCallerIngress {
    actor: HumanActor,
    auth_source: HumanCallerAuthSource,
    authenticated_at_ms: i64,
    expires_at_ms: i64,
    env: String,
}

/// Why a trusted ingress cannot be built from the given inputs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HumanCallerIngressError {
    /// The authenticated principal carries no usable business actor id.
    EmptyActorId,
    /// A verified Human requires a non-blank staff principal.
    EmptyStaffNo,
    /// The environment scope is required; trusted material is never
    /// environment-agnostic.
    EmptyEnv,
    /// The validity window is absent, zero-length or inverted. Validity comes
    /// from the real authentication result; callers may not synthesize one.
    InvalidValidityWindow,
}

impl std::error::Error for HumanCallerIngressError {}

impl fmt::Display for HumanCallerIngressError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyActorId => write!(f, "human caller ingress requires an actor id"),
            Self::EmptyStaffNo => write!(f, "human caller ingress requires a staff number"),
            Self::EmptyEnv => write!(f, "human caller ingress requires an environment scope"),
            Self::InvalidValidityWindow => {
                write!(f, "human caller ingress requires a positive authentication validity window")
            }
        }
    }
}

impl AuthenticatedHumanCallerIngress {
    /// Build the envelope from a verified principal. The window must be the
    /// real one from the authentication result (`authenticated_at_ms > 0`,
    /// `expires_at_ms > authenticated_at_ms`); there is no way to construct
    /// an ingress without a validity window.
    pub fn new(
        actor: HumanActor,
        auth_source: HumanCallerAuthSource,
        authenticated_at_ms: i64,
        expires_at_ms: i64,
        env: impl Into<String>,
    ) -> Result<Self, HumanCallerIngressError> {
        if actor.actor_id.is_empty() {
            return Err(HumanCallerIngressError::EmptyActorId);
        }
        if actor.staff_no.trim().is_empty() {
            return Err(HumanCallerIngressError::EmptyStaffNo);
        }
        let env = env.into();
        if env.is_empty() {
            return Err(HumanCallerIngressError::EmptyEnv);
        }
        if authenticated_at_ms <= 0 || expires_at_ms <= authenticated_at_ms {
            return Err(HumanCallerIngressError::InvalidValidityWindow);
        }
        Ok(Self {
            actor,
            auth_source,
            authenticated_at_ms,
            expires_at_ms,
            env,
        })
    }

    /// The verified business actor. Trusted runtime consumers only; the
    /// staff number inside is principal material, never a persisted value.
    pub fn actor(&self) -> &HumanActor {
        &self.actor
    }

    pub fn auth_source(&self) -> HumanCallerAuthSource {
        self.auth_source
    }

    pub fn authenticated_at_ms(&self) -> i64 {
        self.authenticated_at_ms
    }

    /// Trusted validity from the authentication result. Re-check with
    /// [`Self::is_expired`] after any await before consuming the material.
    pub fn expires_at_ms(&self) -> i64 {
        self.expires_at_ms
    }

    pub fn env(&self) -> &str {
        &self.env
    }

    pub fn is_expired(&self, now_ms: i64) -> bool {
        now_ms >= self.expires_at_ms
    }
}

impl fmt::Debug for AuthenticatedHumanCallerIngress {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Both identity fields are redacted; only source, environment and
        // validity window remain visible for admission debugging.
        f.debug_struct("AuthenticatedHumanCallerIngress")
            // The business actor id is derived from the staff number, so it is
            // principal material as well and stays redacted with it.
            .field("actor_id", &"<redacted>")
            .field("staff_no", &"<redacted>")
            .field("auth_source", &self.auth_source)
            .field("authenticated_at_ms", &self.authenticated_at_ms)
            .field("expires_at_ms", &self.expires_at_ms)
            .field("env", &self.env)
            .finish()
    }
}

/// Opaque, short-lived reference to the Human caller subject established by
/// the identity plugin (spec §10.5, §10.13.4): a scope- and trust-domain
/// separated pseudonym, never a raw staff number or external identity value, never a
/// long-term container key, never a searchable identity. Used internally for
/// proof issuance/verification; Provider routing separately uses the trusted
/// employee number. It is not an MCP credential and
/// not an execution proof.
///
/// The reference value is deliberately only reachable through
/// [`Self::expose_ref`], so accidental log/display usage has to name the
/// boundary explicitly.
pub struct CallerSubjectRef {
    kind: CallerSubjectKind,
    ref_type: CallerSubjectRefType,
    ref_value: String,
    expires_at_ms: i64,
}

/// Why a subject reference cannot be built.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallerSubjectRefError {
    /// Opaque references are never empty; absence is `Option::None`.
    EmptyRef,
    /// An expiry is mandatory and must be a positive instant; nothing may
    /// extend it afterwards.
    InvalidExpiry,
}

impl std::error::Error for CallerSubjectRefError {}

impl fmt::Display for CallerSubjectRefError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyRef => write!(f, "caller subject reference must be a non-empty opaque value"),
            Self::InvalidExpiry => {
                write!(f, "caller subject reference requires a positive expiry instant")
            }
        }
    }
}

impl CallerSubjectRef {
    pub fn new(
        kind: CallerSubjectKind,
        ref_type: CallerSubjectRefType,
        ref_value: String,
        expires_at_ms: i64,
    ) -> Result<Self, CallerSubjectRefError> {
        if ref_value.is_empty() {
            return Err(CallerSubjectRefError::EmptyRef);
        }
        if expires_at_ms <= 0 {
            return Err(CallerSubjectRefError::InvalidExpiry);
        }
        Ok(Self {
            kind,
            ref_type,
            ref_value,
            expires_at_ms,
        })
    }

    pub fn kind(&self) -> CallerSubjectKind {
        self.kind
    }

    pub fn ref_type(&self) -> CallerSubjectRefType {
        self.ref_type
    }

    pub fn expires_at_ms(&self) -> i64 {
        self.expires_at_ms
    }

    pub fn is_expired(&self, now_ms: i64) -> bool {
        now_ms >= self.expires_at_ms
    }

    /// Runtime-only exposure for trusted envelope assembly (wire egress in
    /// the protected dispatch path). Not bound to `Display`, `Debug`,
    /// `Deref` or serde paths on purpose: persisting or logging the exposed
    /// string violates the caller identity boundary.
    pub fn expose_ref(&self) -> &str {
        &self.ref_value
    }
}

impl fmt::Debug for CallerSubjectRef {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CallerSubjectRef")
            .field("kind", &self.kind)
            .field("ref_type", &self.ref_type)
            .field("ref", &"<redacted>")
            .field("expires_at_ms", &self.expires_at_ms)
            .finish()
    }
}

/// Wire-format label of an identity proof (spec §10.13.3). A protocol encoding
/// tag only — trust comes from the plugin profile and trusted configuration,
/// never from the format string itself. `jwt` is the default output,
/// `opaque` is allowed for internal adapters wrapping the same contract.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CallerProofFormat {
    Jwt,
    Opaque,
}

/// The short-lived, runtime-only caller execution token (spec §10.5): an
/// opaque-to-business-layer credential. Execution-bound profiles attest run,
/// scope and purpose; identity-relay profiles do not. Its expiry is the actual
/// credential expiry, not a replacement for the independent BCS authorization
/// deadline. It is never a long-term container key or a container identity.
///
/// The secret value is reachable only through [`Self::expose_token`]; there
/// is no `Display`, `Deref` or serde path on purpose. Never persisted, never
/// logged, never forwarded Bot-to-Bot.
pub struct CallerExecutionToken {
    format: CallerProofFormat,
    token: String,
    expires_at_ms: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallerExecutionTokenError {
    EmptyToken,
    InvalidExpiry,
}

impl std::error::Error for CallerExecutionTokenError {}

impl fmt::Display for CallerExecutionTokenError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyToken => write!(f, "execution token must be a non-empty opaque value"),
            Self::InvalidExpiry => {
                write!(f, "execution token requires a positive expiry instant")
            }
        }
    }
}

impl CallerExecutionToken {
    pub fn new(
        format: CallerProofFormat,
        token: String,
        expires_at_ms: i64,
    ) -> Result<Self, CallerExecutionTokenError> {
        if token.is_empty() {
            return Err(CallerExecutionTokenError::EmptyToken);
        }
        if expires_at_ms <= 0 {
            return Err(CallerExecutionTokenError::InvalidExpiry);
        }
        Ok(Self {
            format,
            token,
            expires_at_ms,
        })
    }

    pub fn format(&self) -> CallerProofFormat {
        self.format
    }

    pub fn expires_at_ms(&self) -> i64 {
        self.expires_at_ms
    }

    pub fn is_expired(&self, now_ms: i64) -> bool {
        now_ms >= self.expires_at_ms
    }

    /// Runtime-only exposure for the protected egress. Persisting or logging
    /// the exposed string violates the caller identity boundary.
    pub fn expose_token(&self) -> &str {
        &self.token
    }
}

impl fmt::Debug for CallerExecutionToken {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("CallerExecutionToken")
            .field("format", &self.format)
            .field("token", &"<redacted>")
            .field("expires_at_ms", &self.expires_at_ms)
            .finish()
    }
}
/// Opaque runtime-only reference (subject/binding). Constructed by the
/// trusted identity/plugin boundary only.
#[derive(Clone)]
pub struct CallerOpaqueRef(String);

impl CallerOpaqueRef {
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    /// Trusted-consumer access for building the protected Gateway request.
    /// Not serializable, not debug-printable, not persistable.
    pub fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for CallerOpaqueRef {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("CallerOpaqueRef(<redacted>)")
    }
}
