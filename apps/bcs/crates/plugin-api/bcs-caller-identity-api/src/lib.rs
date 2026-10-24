//! CallerIdentityPlugin: the outbound identity seam (P1.5 port contract).
//!
//! Based on spec `2026-09-16-caller-identity-session-container`
//! §10.13 (core interface draft in §10.13.2). This is a Plugin API/boundary
//! port, extended by the v1.1 credential profiles in CONTEXT.md:
//! BCS calls external identity capability through it and the
//! composition root selects the implementation (public JWT or a deployment-specific
//! identity provider). Core, HTTP delivery and group orchestration never branch on
//! the concrete plugin.
//!
//! Boundary rules carried by the types:
//! - the plugin only *establishes, issues and verifies* identity material;
//!   it never decides message routing, MCP grants, group membership or
//!   whether a child may derive (BCS validates parent/membership/topology
//!   and then hands an approved binding to `issue_execution`);
//! - every input/output here is runtime-only trusted material: no serde
//!   derives, and secrets are only reachable via the explicit
//!   `expose_*` accessors of the shared runtime-only envelope types;
//! - BCS authorization windows only shrink. Credential expiry and execution
//!   attestation depend on the explicitly declared credential profile;
//! - errors are closed and never echo secrets.

use async_trait::async_trait;
use thiserror::Error;

pub use bcs_domain::caller_identity_material::*;

/// The run/attempt association a proof is bound to (spec §10.13.2:
/// BCS-approved run/root/parent/turn). Assembled by trusted BCS code from a
/// committed delivery — the model layer can never supply these values.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerRunBinding {
    pub run_id: String,
    pub root_run_id: String,
    pub parent_run_id: Option<String>,
    pub message_turn_id: Option<String>,
    /// The current execution attempt. Must be greater than zero; issuance
    /// and verification both reject `attempt == 0` (no auto-correction).
    pub attempt: u32,
}

/// The scope a proof is bound to. `group_id` is `None` for H2A DM roots and
/// carries the group for caller-group work.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerProofScope {
    pub env: String,
    pub target_bot_id: String,
    pub group_id: Option<String>,
    pub session_id: String,
}

/// Delegation bounds bound into the proof. A child proof may never exceed
/// the parent's bounds; BCS checks lineage, the plugin re-checks arithmetic.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CallerDelegationBounds {
    pub depth: u32,
    pub max_depth: u32,
}

/// Optional isolation constraint taken from a trusted ensure result
/// (spec §10.13: binding rules). When present, verification is exact:
/// a proof with no binding never satisfies a bound expectation and vice
/// versa — `None` is precise absence, not a wildcard.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CallerIsolationConstraint {
    pub binding_ref: String,
    pub generation: Option<u32>,
}

/// Input of [`CallerIdentityPlugin::establish_subject`]: the strict Human
/// caller ingress from the same authentication run that resolved the
/// display sender, plus the explicit caller-provided clock.
#[derive(Debug)]
pub struct EstablishCallerSubject {
    pub ingress: AuthenticatedHumanCallerIngress,
    /// Explicit caller-provided clock; the plugin is deterministic and
    /// never reads ambient time. Must not precede `authenticated_at_ms`.
    pub now_ms: i64,
}

/// A trusted, established caller subject (spec §10.13.2). The
/// `subject_proof` backs ensure/derivation only — it is not an MCP
/// credential and never an execution proof. Expires no later than the
/// ingress.
#[derive(Debug)]
pub struct EstablishedCallerSubject {
    pub subject_ref: CallerSubjectRef,
    pub subject_proof: CallerExecutionToken,
    pub expires_at_ms: i64,
}

/// The identity material BCS selected from its short-term ledger for one
/// issuance. The direction is strict: a caller child exchanges the exact
/// parent execution token, while an Owner/Workflow/manager-worker child uses
/// an already established Human origin handle. None of these fields can be
/// populated from a Provider message body or echo.
#[derive(Debug, Clone, Copy)]
pub enum IssueCallerExecutionSource<'source> {
    /// BCS Material Ledger chose an established Human origin handle. The
    /// trusted subject ref is already in [`IssueCallerExecution`]; this
    /// variant must not be treated as a Provider-returned execution token.
    HumanOriginHandle,
    /// An owned parent-proof handle, used when BCS owns the token in a
    /// short-term Material Ledger slot. The token itself is never cloned or
    /// returned by a Provider. Retained for compatibility with release
    /// callers; new BCS code uses [`IssueCallerExecutionSource::ParentExecutionTokenRef`].
    ParentExecutionToken {
        proof: ParentExecutionProof<'source>,
    },
    /// The exact BCS-selected parent proof for the real parent run/attempt.
    /// This borrow keeps the API user-visible signature non-generic: the
    /// optional token lives in an argument object constructed from trusted
    /// BCS facts, never from a Provider response body.
    ParentExecutionTokenRef {
        token: &'source CallerExecutionToken,
        parent_run_id: &'source str,
        parent_attempt: u32,
    },
}

/// Borrowed, runtime-only parent proof material selected by BCS.
///
/// A `None` token is a distinct owner-lineage/fail-open bit: it is only
/// produced for tokens with `entropy_owner=true`. It never appears in a
/// common identity envelope, is not user-visible, and cannot be constructed
/// by a generic request.
#[derive(Debug, Clone, Copy)]
pub enum ParentExecutionProof<'source> {
    /// BCS supplies a runtime-only root token as an identity handle.
    Value { token: &'source CallerExecutionToken },
}

#[derive(Debug)]
pub struct IssueCallerExecution<'source> {
    /// Verified employee number for Provider routing and internal exchange; never a JWT claim.
    pub staff_no: CallerOpaqueRef,
    /// Authoritative target Bot registration, never inferred from bot_id.
    pub agent_code: Option<String>,
    /// Actual sandbox readback, not the optional technical isolation binding.
    /// Identity relay plugins may require it; local JWT supports Providers without it.
    pub sandbox_container_id: Option<CallerOpaqueRef>,
    pub subject: CallerSubjectRef,
    pub run: CallerRunBinding,
    pub scope: CallerProofScope,
    pub delegation: CallerDelegationBounds,
    pub purpose: CallerExecutionPurpose,
    /// Earliest BCS authorization deadline. Issuance must complete within it.
    /// ExecutionBound proofs also expire within it; IdentityRelay credentials
    /// retain upstream expiry and do not extend BCS authorization.
    pub not_after_ms: i64,
    /// Optional isolation binding from ensure; absent means the plugin must
    /// not embed a binding constraint.
    pub isolation: Option<CallerIsolationConstraint>,
    /// The BCS-owned identity graph chosen by the Material Ledger. The
    /// direction is strict: parent execution material must be exchanged and
    /// never fabricated from a task body or Provider echo.
    pub source: IssueCallerExecutionSource<'source>,
    /// Explicit caller-provided clock; the plugin is deterministic and
    /// never reads ambient time.
    pub now_ms: i64,
}

/// Exact expectations that [`CallerIdentityPlugin::verify_execution`]
/// matches. Nothing that the caller expects but the proof does not carry is
/// accepted, and nothing extra in the proof silently satisfies expectations.
#[derive(Debug)]
pub struct ExpectedCallerExecution {
    pub scope: CallerProofScope,
    pub run: CallerRunBinding,
    pub purpose: CallerExecutionPurpose,
    pub isolation: Option<CallerIsolationConstraint>,
}

/// Input of [`CallerIdentityPlugin::verify_execution`].
#[derive(Debug)]
pub struct VerifyCallerExecution {
    pub token: CallerExecutionToken,
    pub expected: ExpectedCallerExecution,
    /// Explicit caller-provided clock for deterministic verification.
    pub now_ms: i64,
}

/// A verified execution proof: the resolved subject, the confirmed purpose
/// and the effective (never extended) expiry. Claims that were not verified
/// against explicit expectations are not surfaced as trusted data.
#[derive(Debug)]
pub struct VerifiedCallerExecution {
    pub subject_ref: CallerSubjectRef,
    pub purpose: CallerExecutionPurpose,
    pub expires_at_ms: i64,
}

/// Secret-free plugin errors. Upstream rejection codes are validated machine
/// codes only; never include upstream messages, response bodies or credentials.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Error)]
pub enum CallerIdentityError {
    #[error("upstream identity rejected: {0}")]
    UpstreamRejected(CallerIdentityRejectionCode),
    #[error("credential does not attest execution constraints")]
    ExecutionVerificationUnsupported,
    /// The principal was not established by a trusted authentication chain.
    #[error("untrusted principal")]
    UntrustedPrincipal,
    /// The selected implementation requires upstream evidence (e.g. IAM
    /// material for internal exchange) that the trusted adapter did not
    /// provide.
    #[error("upstream identity evidence required")]
    UpstreamEvidenceRequired,
    /// The identity exchange itself rejected the request.
    #[error("identity exchange rejected")]
    IdentityExchangeRejected,
    /// The identity provider backing the plugin is unavailable; caller
    /// paths fail closed and never fall back to another implementation.
    #[error("identity provider unavailable")]
    IdentityProviderUnavailable,
    /// Malformed, tampered, wrong-type, unknown-key or wrong-execution
    /// proof material.
    #[error("caller token invalid")]
    TokenInvalid,
    #[error("caller token expired")]
    TokenExpired,
    #[error("caller token audience mismatch")]
    AudienceMismatch,
    #[error("caller token purpose mismatch")]
    PurposeMismatch,
    #[error("caller token scope mismatch")]
    ScopeMismatch,
    /// The proof's isolation binding does not satisfy the expectation, or
    /// the plugin cannot support the required binding shape.
    #[error("isolation binding unsupported or mismatched")]
    IsolationUnsupported,
    /// Structurally invalid issuance/verification input (empty ids,
    /// `attempt == 0`, inverted clocks or deadlines).
    #[error("invalid caller identity request")]
    InvalidRequest,
}

/// The caller identity seam (spec §10.13.2). Establish, issue, verify only:
/// the three concerns — authentication, proof issuance and MCP authorization
/// — are deliberately separate (§10.13.6); this trait is the middle one and
/// its implementations must not grow the other two.
#[async_trait]
pub trait CallerIdentityPlugin: Send + Sync + 'static {
    /// Establish a scoped, stable pseudonymous subject from a verified
    /// Human principal. Never accepts self-reported names; output material
    /// is runtime-only.
    async fn establish_subject(
        &self,
        input: EstablishCallerSubject,
    ) -> Result<EstablishedCallerSubject, CallerIdentityError>;

    /// Issue a credential for a BCS-approved target run/attempt. Each
    /// ExecutionBound issuance binds that execution with a fresh `jti`
    /// and bounded expiry.
    /// IdentityRelay issuance preserves upstream reuse and actual expiry;
    /// the credential is not proof of run/scope/purpose/isolation.
    async fn issue_execution(
        &self,
        input: IssueCallerExecution<'_>,
    ) -> Result<CallerExecutionToken, CallerIdentityError>;

    /// Verify a proof against explicit expectations, including exact
    /// binding semantics, at the caller-provided time. IdentityRelay plugins
    /// return ExecutionVerificationUnsupported, never fabricated facts.
    async fn verify_execution(
        &self,
        input: VerifyCallerExecution,
    ) -> Result<VerifiedCallerExecution, CallerIdentityError>;

    /// Declares guarantees without inferring trust from a wire label.
    fn credential_profile(&self) -> CallerCredentialProfile {
        CallerCredentialProfile::ExecutionBound
    }

    /// Issuer tag of this plugin's proofs, serialized as
    /// `extensions.caller_execution_token.type` on the protected send wire.
    /// Like `format`, it is a wire label only — trust comes from the trusted
    /// assembly, never from this string. Default matches the jwt profile;
    /// other plugins override it with their own declared wire label.
    fn proof_type(&self) -> &'static str {
        "jwt"
    }
}

mod factory;
pub use factory::{build_caller_identity_plugin, CallerIdentityPluginFactory};

mod profile;
pub use profile::{CallerCredentialProfile, CallerIdentityRejectionCode};
