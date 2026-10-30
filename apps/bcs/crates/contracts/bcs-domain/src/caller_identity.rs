//! Caller identity contract types (P0 freeze).
//!
//! Frozen against spec `2026-09-16-caller-identity-session-container`
//! §10.2/§10.3/§10.7. Everything here is **non-identity control-plane data**
//! that may be persisted or exchanged with authorized configuration sources:
//! Bot caller switches, Provider technical capabilities, BCS-computed
//! effective status, and non-identity execution-run lineage metadata.
//!
//! What must NEVER appear in this module (spec §4.1, §10.1, §12):
//! Human login material (staff_no, cookies, bearer/refresh tokens), external
//! identity plaintext, MCP grants or credentials, `caller_context` payloads,
//! caller execution tokens, redeemable subject refs, or any
//! run_id-to-human mapping. Runtime-only identity envelopes live in
//! `crate::caller_identity_material` (reexported by the Service API) instead.
//!
//! Unconfigured is expressed by `Option::None`, not by a magic revision.
//! V1 isolation only has the `caller_container` *requirement*;
//! `container_bound` is the Gateway's actual guarantee (see §10.6) and is not
//! a configurable value here.

use serde::{Deserialize, Serialize};

/// Minimum isolation a Bot requires for caller execution. V1 has exactly one
/// level; declaring the set now keeps future levels an explicit contract
/// change instead of a silent string.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CallerMinimumIsolation {
    CallerContainer,
}

/// Authoritative-source Bot caller switch (spec §10.7.2). `enabled` is the
/// only business switch; the retired `mode=none|supported|required` family is
/// not a second writable knob.
///
/// Wire shape is strict: `enabled` is required (no serde default — old
/// clients must not silently flip it), unknown fields are rejected. The Rust
/// [`Default`] is "off" and is only the fallback meaning for a Bot that was
/// never configured, never an implicit disable applied on top of a stored
/// snapshot.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallerIdentityConfig {
    pub enabled: bool,
    #[serde(default = "default_minimum_isolation")]
    pub minimum_isolation: CallerMinimumIsolation,
}

impl Default for CallerIdentityConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            minimum_isolation: CallerMinimumIsolation::CallerContainer,
        }
    }
}

fn default_minimum_isolation() -> CallerMinimumIsolation {
    CallerMinimumIsolation::CallerContainer
}

/// Full caller configuration snapshot from an authoritative source
/// (TeamClaw: Backend; other integrations: their authorized
/// registration/update path). `source_revision` is a per-Bot monotonically
/// increasing version, not an event timestamp; transport dedup keys (e.g.
/// `event_id`) stay in their own protocols and never replace this comparison.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallerIdentitySnapshot {
    pub source_revision: u64,
    pub caller_identity: CallerIdentityConfig,
}

/// Pure result of comparing an incoming snapshot against the current
/// authoritative one (spec §10.7.2 response `sync_status`). This is a local
/// comparison result, not a wire acknowledgment of a successful write.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallerConfigRevisionDecision {
    /// No current configuration, or an incoming strictly higher revision:
    /// apply atomically (including explicit disable).
    Apply,
    /// Same revision with identical content: idempotent, no write.
    Unchanged,
    /// Lower revision: never overwrites newer stored data.
    Stale,
}

/// Same revision with different content. Carries only the non-sensitive
/// revision; adapters map it to a version-conflict error and never echo the
/// conflicting payloads.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CallerConfigRevisionConflict {
    pub source_revision: u64,
}

impl CallerConfigRevisionConflict {
    pub fn new(source_revision: u64) -> Self {
        Self { source_revision }
    }
}

impl CallerIdentitySnapshot {
    /// Pure revision/content comparison (no I/O, never mutates `current`).
    pub fn compare_revision(
        &self,
        current: Option<&Self>,
    ) -> Result<CallerConfigRevisionDecision, CallerConfigRevisionConflict> {
        match current {
            None => Ok(CallerConfigRevisionDecision::Apply),
            Some(current) if self.source_revision > current.source_revision => {
                Ok(CallerConfigRevisionDecision::Apply)
            }
            Some(current) if self.source_revision == current.source_revision => {
                if self.caller_identity == current.caller_identity {
                    Ok(CallerConfigRevisionDecision::Unchanged)
                } else {
                    Err(CallerConfigRevisionConflict::new(self.source_revision))
                }
            }
            Some(_) => Ok(CallerConfigRevisionDecision::Stale),
        }
    }
}

/// How execution tokens, when issued on the plugin credential path, may be
/// exposed. V1 allows runtime-only: never in Prompt, model context, Bot
/// business logic, logs, or persisted rows.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CallerTokenVisibility {
    RuntimeOnly,
}

/// Provider technical capability declaration (spec §10.7.3): persisted in the
/// Provider control plane (e.g. `bcs_providers.config.$.caller_capabilities`),
/// never copied into Bot configuration. The four original capability fields
/// remain required; container_routing defaults to the legacy ensure path.
/// There are no "default supported" values, and `downlink.enabled` or a
/// `provider_downlink` connection mode never implies caller support.
///
/// `supports_isolation_binding=false` only means the Gateway may omit the
/// optional `binding_ref`; it does not waive the isolation requirement.
/// `supports_observe_inject` gates caller-group history synchronization, not
/// H2A readiness.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderCallerCapabilities {
    /// Legacy providers keep ensure. Explicit caller_subject declares lazy
    /// Human×Bot container routing and session isolation without an ensure RPC.
    #[serde(default)]
    pub container_routing: CallerContainerRouting,
    pub supports_caller: bool,
    pub supports_isolation_binding: bool,
    pub supports_observe_inject: bool,
    pub token_visibility: CallerTokenVisibility,
}

/// Provider-owned container preparation/routing contract, not an identity grant.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CallerContainerRouting {
    #[default]
    Ensure,
    CallerSubject,
}

/// Why an enabled Bot stays blocked (spec §10.7.3/§10.13.7). Closed codes
/// only; storage/parse failures are never reported as one of these — they
/// propagate as read errors instead of implying "disabled".
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotCallerBlockedReason {
    /// Provider is absent, disabled, undeclared for caller, or does not
    /// declare `supports_caller=true`.
    ProviderCallerUnsupported,
    /// The declared capabilities cannot satisfy the Bot's minimum isolation
    /// requirements on this protocol version.
    IsolationNotSupported,
    /// Provider capabilities match but the BCS-side execution chain (caller
    /// identity plugin / gateway wiring / protected egress) is not ready.
    CallerExecutionUnavailable,
}

/// BCS-computed effective status (spec §10.2, §10.7.3). This is the
/// read-only merged view for admission decisions; it is never a writable
/// registration DTO. Providers cannot write it and Bots cannot raise it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotCallerEffectiveStatus {
    /// `caller_identity.enabled=false`: ordinary execution policy.
    Disabled,
    /// Enabled but new caller dispatch/child issuance must fail closed; never
    /// a silent Owner fallback for caller work.
    Blocked(BotCallerBlockedReason),
    /// Enabled and safely executable end to end on the current chain.
    Ready,
}

impl BotCallerEffectiveStatus {
    /// Pure merge of the three independent truth sources (§10.7.3):
    ///
    /// ```text
    /// bot enabled=false -> disabled
    /// bot enabled=true && provider cannot support caller -> blocked
    /// bot enabled=true && capabilities ok && BCS chain not ready -> blocked
    /// bot enabled=true && capabilities ok && chain ready -> ready
    /// ```
    ///
    /// H2A readiness deliberately ignores `supports_observe_inject` and
    /// `supports_isolation_binding`; caller-group readiness composes this
    /// with the synchronization capability separately.
    pub fn merge(
        config: &CallerIdentityConfig,
        provider_capabilities: Option<&ProviderCallerCapabilities>,
        caller_execution_ready: bool,
    ) -> Self {
        if !config.enabled {
            return Self::Disabled;
        }
        match provider_capabilities {
            None => Self::Blocked(BotCallerBlockedReason::ProviderCallerUnsupported),
            Some(caps) if !caps.supports_caller => {
                Self::Blocked(BotCallerBlockedReason::ProviderCallerUnsupported)
            }
            Some(_) if !caller_execution_ready => {
                Self::Blocked(BotCallerBlockedReason::CallerExecutionUnavailable)
            }
            Some(_) => Self::Ready,
        }
    }
}

// Removed (2026-10-20 residue sweep): `StandardDelegationPolicy`,
// `ExecutionRunRecord` and its enums — the `bcs_execution_runs` persisted
// row family was retired with the a2a_child removal (7ea603b8c); lineage
// semantics are runtime-only in `RootLineageIndex` now. Do not resurrect the
// persisted shape without a spec update first.