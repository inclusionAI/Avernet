//! `[group_a2a]`: opt-in configuration seams of the FSM-Group-as-A2A-Agent
//! surface (plan T9). Configuration surface only — see
//! `server::group_a2a_wiring` for the composition-root behavioral contract:
//!
//! - **Opt-in only**: `enabled` defaults to `false`. Nothing mounts and no
//!   existing route changes while the surface is closed.
//! - **Startup failure, not degradation**: enabling the surface REQUIRES the
//!   persistent coordination provider (P). A missing provider fails the
//!   process startup; runtime substitution by in-memory roles is forbidden.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GroupA2aConfig {
    /// Opt-in switch of the whole joint assembly. Default `false`: the
    /// REST/SSE group surface, listing, connector, worker and supervisor
    /// stay unmounted, and existing routes keep their exact behavior.
    #[serde(default)]
    pub enabled: bool,

    /// HMAC secret of the signed listing cursors (v2 protocol, plan T8).
    /// Multi-replica / shared-read deployments must configure the same
    /// value. UNSET STAYS FAIL-CLOSED: the listing surface issues and
    /// accepts no continuation cursor (store-level gate, unchanged).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cursor_secret: Option<String>,

    /// Dispatch Supervisor switch (plan T9 mandate). With the surface
    /// enabled, `true` runs the Supervisor on the shard-holding Worker:
    /// ordinary dispatches get their execution hint re-sent to the target
    /// pool; verification-mode dispatches only repair/wake the isolated
    /// verification queue. `false` is the crash-recovery control group used
    /// by the acceptance vectors: nothing re-sends hints, so a dead
    /// connector must surface as a missed deadline, never as a silent
    /// recovery.
    #[serde(default = "default_supervisor_enabled")]
    pub supervisor_enabled: bool,

    /// Ceiling of the verification lane's exponential backoff (the T16
    /// residual seam: wired here, validated by the connector port against
    /// its backoff minimum). Default mirrors the connector default.
    #[serde(default = "default_verification_backoff_max_ms")]
    pub verification_backoff_max_ms: u64,

    /// Shard ids of this deployment's coordination plane.
    #[serde(default = "default_shards")]
    pub shards: Vec<u32>,

    /// Public base URL advertised in the group Card and SSE cursors of the
    /// mounted surface. Defaults to the local default profile.
    #[serde(default = "default_public_url")]
    pub public_url: String,

    /// KeyLayout namespace of the coordination-plane documents. Deployment
    /// unique; defaults to the local default profile.
    #[serde(default = "default_namespace")]
    pub namespace: String,

    // RECORD-ONLY (deliberately NOT configurable until governance decides):
    //
    // - Cut-snapshot retention / reaper: the retention policy is unsettled
    //   (contract-decisions 174). No reaper is mounted and no deletion may
    //   be auto-enabled; expired cuts already refuse continuation at read
    //   time. Do not add a retention switch here without a recorded
    //   governance decision beside it.
    // - Structural verification bounds the composition root keeps fixed by
    //   design (documented, not tunable here): one due-verifications claim
    //   reads at most 64 entries in a single pass with no continuation
    //   token, and the retained-obligation verification actor line
    //   saturates at 8 concurrent verifiers.
}

fn default_supervisor_enabled() -> bool {
    true
}

fn default_verification_backoff_max_ms() -> u64 {
    60_000
}

fn default_shards() -> Vec<u32> {
    vec![1]
}

fn default_public_url() -> String {
    "http://127.0.0.1:21000".to_string()
}

fn default_namespace() -> String {
    "bcs-group-a2a".to_string()
}

impl Default for GroupA2aConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            cursor_secret: None,
            supervisor_enabled: default_supervisor_enabled(),
            verification_backoff_max_ms: default_verification_backoff_max_ms(),
            shards: default_shards(),
            public_url: default_public_url(),
            namespace: default_namespace(),
        }
    }
}
