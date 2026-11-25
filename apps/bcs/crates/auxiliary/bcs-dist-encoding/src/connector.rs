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

/// The FSM round identity of a dispatch did: the `<tid>.fsm:<generation>`
/// prefix of the authoritative format `<tid>.fsm:<generation>:<node>:<attempt>`
/// (the reducer's `attempt_did`). `None` for any other shape — old
/// `fsm:{tid}:...` forms and opaque ids carry no round authority.
pub fn round_of(did: &Did) -> Option<String> {
    let (prefix, rest) = did.0.split_once(".fsm:")?;
    let mut parts = rest.split(':');
    let generation = parts.next()?;
    let node = parts.next()?;
    let attempt = parts.next()?;
    if parts.next().is_some() || generation.is_empty() || node.is_empty() || attempt.is_empty() {
        return None;
    }
    Some(format!("{prefix}.fsm:{generation}"))
}

/// Canonical dedupe domain of a frozen target binding: agent, binding
/// version and credential together pin which remote contract a dedupe
/// guarantee was negotiated for. A rebinding (a new binding version or
/// credential) is a different domain.
pub fn dedupe_domain(binding: &bcs_dist_model::AgentBinding) -> String {
    format!("{}:{}:{}", binding.agent.0, binding.binding_version.0, binding.credential_ref.0)
}

/// Digest of a send attempt's normalized body: the request id and the
/// canonical body bytes together, so a proof never matches a different
/// request semantics.
pub fn send_body_digest(request_id: &RequestId, canonical_body: &[u8]) -> Digest {
    digest_fields(&[b"send-body", request_id.0.as_bytes(), canonical_body])
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
