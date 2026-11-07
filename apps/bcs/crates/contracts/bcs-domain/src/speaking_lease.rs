//! Session speaking lease (spec §6.3 / §6.3.1 B7): speaking rights for ONE
//! Human business actor over one session. The persisted shape is exactly
//! the five-field whitelist — session, holder business actor id, lease id,
//! epoch and expiry. Never a credential, never a caller proof, never a
//! redeemable identity reference: holder association grants submission
//! eligibility only and is never a basis for issuing caller identity.

/// The persisted business speaking-lease state. `holder_actor_id` is the
/// existing business actor id (e.g. `human_12345`), not a login artifact.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionSpeakingLease {
    pub session_id: String,
    pub holder_actor_id: String,
    pub lease_id: String,
    /// Submission-fencing counter: only an ever-increasing epoch admits;
    /// it is NOT an execution-revocation version (spec §6.3 rule 3).
    pub epoch: u64,
    /// Idle expiry in server time: renewed only by a freshly accepted
    /// Human message, never by bots, heartbeats, results or retries.
    pub expires_at_ms: i64,
}

impl SessionSpeakingLease {
    /// Server-clock expiry check. Expiry does not depend on a background
    /// reaper being timely: an expired lease admits nothing.
    pub fn is_expired(&self, now_ms: i64) -> bool {
        now_ms >= self.expires_at_ms
    }
}

/// Whether a session's Human speech is lease-fenced (spec §6.1):
///
/// ```text
/// group_caller_enabled = has_human_member && exists bot where caller_identity.enabled
/// ```
///
/// No Human members or no caller-enabled bots keep the ordinary group
/// path — plain groups never consult the lease store at all. A caller-
/// enabled Bot whose Provider is blocked still selects LeaseRequired:
/// being fenced is not a downgrade into an ordinary group.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionSpeakingMode {
    /// Ordinary group: no container, no token issuance, no speaking
    /// limits, no lease reads or writes.
    #[default]
    Plain,
    /// Human submissions must carry a valid speaking lease.
    LeaseRequired,
}

/// Dynamic mode resolution over the CURRENT membership and the CURRENT
/// per-bot caller configuration.
pub fn resolve_speaking_mode(
    has_human_member: bool,
    any_bot_caller_enabled: bool,
) -> SessionSpeakingMode {
    if has_human_member && any_bot_caller_enabled {
        SessionSpeakingMode::LeaseRequired
    } else {
        SessionSpeakingMode::Plain
    }
}