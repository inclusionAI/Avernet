//! Stable identities shared by the Connector and its Inbox validator.
use super::digest_fields;
use bcs_dist_model::{Did, Digest, InputId, InputSeq, InteractionId, RequestId};

/// The key events of one call.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyEvent {
    Final,
    Error,
    /// The n-th question round of the call (1-based).
    InputRequired(u64),
    AuthRequired(u64),
}

impl KeyEvent {
    fn name(self) -> &'static str {
        match self {
            Self::Final => "final",
            Self::Error => "error",
            Self::InputRequired(_) => "input_required",
            Self::AuthRequired(_) => "auth_required",
        }
    }
    /// Question round, or `None` for a terminal event.
    pub fn round(self) -> Option<u64> {
        match self {
            Self::InputRequired(n) | Self::AuthRequired(n) => Some(n),
            Self::Final | Self::Error => None,
        }
    }
}

fn key_hex(did: &Did, key: KeyEvent) -> String {
    let digest = match key.round() {
        Some(n) => digest_fields(&[did.0.as_bytes(), key.name().as_bytes(), &n.to_be_bytes()]),
        None => digest_fields(&[did.0.as_bytes(), key.name().as_bytes()]),
    };
    digest.0.trim_start_matches("sha256:").to_owned()
}

/// Request id (the agent's message id) of the call's start.
pub fn start_request_id(did: &Did) -> RequestId {
    RequestId(format!("{}:start", did.0))
}

/// Request id of the call's continue-input `seq`.
pub fn input_request_id(did: &Did, seq: InputSeq) -> RequestId {
    RequestId(format!("{}:in:{}", did.0, seq.0))
}

/// Input id of a key event: `ae:<hash>`.
pub fn key_input_id(did: &Did, key: KeyEvent) -> InputId {
    InputId(format!("ae:{}", key_hex(did, key)))
}

/// Interaction id of a question round: `ia:<hash>`, same hash as its input id.
pub fn interaction_id(did: &Did, key: KeyEvent) -> InteractionId {
    InteractionId(format!("ia:{}", key_hex(did, key)))
}

/// Business digest of a key event: its identity plus the terminal state. The
/// payload is left out on purpose (two holders see different bytes).
pub fn key_digest(did: &Did, key: KeyEvent, state: &str) -> Digest {
    let round = key.round().unwrap_or(0).to_be_bytes();
    digest_fields(&[
        b"agent_key",
        did.0.as_bytes(),
        key.name().as_bytes(),
        &round,
        state.as_bytes(),
    ])
}
