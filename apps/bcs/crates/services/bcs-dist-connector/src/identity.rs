//! Stable identities of a call's sends and key events.
//!
//! None of them contains the holder epoch, a sequence number or a remote event
//! id, so every holder of a call derives the same values (decision 29 b).
//!
//! The hash is the same length-prefixed SHA-256 as `bcs_dist_store::identity`
//! (each field preceded by its length as eight big-endian bytes). It is copied
//! here because the engine must not depend on a store crate; a test with fixed
//! vectors keeps the two copies identical.

use bcs_dist_model::{Did, Digest, InputId, InputSeq, InteractionId, RequestId};
use sha2::{Digest as _, Sha256};

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
    fn round(self) -> Option<u64> {
        match self {
            Self::InputRequired(n) | Self::AuthRequired(n) => Some(n),
            Self::Final | Self::Error => None,
        }
    }
}

/// `sha256:<hex>` over length-prefixed fields.
pub fn digest_fields(fields: &[&[u8]]) -> Digest {
    let mut hasher = Sha256::new();
    for field in fields {
        hasher.update((field.len() as u64).to_be_bytes());
        hasher.update(field);
    }
    Digest(format!("sha256:{}", hex::encode(hasher.finalize())))
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
