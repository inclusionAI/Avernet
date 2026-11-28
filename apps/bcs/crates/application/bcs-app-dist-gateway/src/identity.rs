//! Stable identities derived from a request.
//!
//! Everything a retry must reproduce is derived, never random: the same
//! message from the same caller selects the same session, task and input, so
//! the admission record of the first attempt answers the retry.
//!
//! | Id | Form | Derived from |
//! | --- | --- | --- |
//! | session | `s<shard:04>.<16 hex>` | tenant, principal, target kind and id, message id; shard = hash mod shard count |
//! | task | `<session>.<16 hex>` | session, message id |
//! | input (user) | `in-<32 hex>` | tenant, principal, target kind and id, session, message id |
//! | input (reply) | `in-<32 hex>` | principal, session, task, message id |
//! | input (cancel) | `in-<32 hex>` | principal, session, task, request id |
//!
//! Digests are SHA-256 over length-prefixed fields, so `("ab","c")` and
//! `("a","bc")` differ. Content digests hash canonical JSON (sorted keys).

use crate::invalid;
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::GatewayError;
use serde_json::Value;
use sha2::{Digest as _, Sha256};

const MAX_ID_BYTES: usize = 200;

fn hash(fields: &[&str]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    for field in fields {
        hasher.update((field.len() as u64).to_be_bytes());
        hasher.update(field.as_bytes());
    }
    hasher.finalize().into()
}

pub(crate) fn session_id(scope: &TaskScope, target: &SessionTarget, message: &RequestId, shards: u32) -> Sid {
    let namespace = match target { SessionTarget::Group { .. } => "sid", SessionTarget::DirectAgent { .. } => "direct_sid" };
    let digest = hash(&[namespace, &scope.tenant.0, &scope.principal.0, target.id(), &message.0]);
    let mut head = [0u8; 8];
    head.copy_from_slice(&digest[..8]);
    let shard = u64::from_be_bytes(head) % u64::from(shards.max(1));
    Sid(format!("s{shard:04}.{}", hex::encode(&digest[8..16])))
}

pub(crate) fn task_id(sid: &Sid, message: &RequestId) -> Tid {
    let digest = hash(&["tid", &sid.0, &message.0]);
    Tid(format!("{}.{}", sid.0, hex::encode(&digest[..8])))
}

fn input_id(fields: &[&str]) -> InputId {
    InputId(format!("in-{}", hex::encode(&hash(fields)[..16])))
}

pub(crate) fn user_input_id(scope: &TaskScope, target: &SessionTarget, sid: &Sid, message: &RequestId) -> InputId {
    let namespace = match target { SessionTarget::Group { .. } => "user", SessionTarget::DirectAgent { .. } => "direct_user" };
    input_id(&[namespace, &scope.tenant.0, &scope.principal.0, target.id(), &sid.0, &message.0])
}

pub(crate) fn reply_input_id(scope: &TaskScope, tid: &Tid, sid: &Sid, message: &RequestId) -> InputId {
    input_id(&["human_reply", &scope.principal.0, &sid.0, &tid.0, &message.0])
}

pub(crate) fn cancel_input_id(scope: &TaskScope, tid: &Tid, sid: &Sid, request: &RequestId) -> InputId {
    input_id(&["cancel", &scope.principal.0, &sid.0, &tid.0, &request.0])
}

/// Digest of canonical JSON: what makes "the same request again" the same.
pub(crate) fn content_digest(value: &Value) -> Digest {
    let mut canonical = String::new();
    write_canonical(value, &mut canonical);
    Digest(format!("sha256:{}", hex::encode(hash(&[&canonical]))))
}

fn write_canonical(value: &Value, out: &mut String) {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            out.push('{');
            for (i, key) in keys.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                out.push_str(&Value::String((*key).clone()).to_string());
                out.push(':');
                if let Some(inner) = map.get(*key) {
                    write_canonical(inner, out);
                }
            }
            out.push('}');
        }
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_canonical(item, out);
            }
            out.push(']');
        }
        scalar => out.push_str(&scalar.to_string()),
    }
}

fn plain(part: &str) -> bool {
    !part.is_empty()
        && part
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// Checks a client-supplied session id: `s<digits>.<part>`.
pub fn parse_session(sid: &Sid) -> Result<(), GatewayError> {
    let bad = || invalid("session_id_format");
    if sid.0.len() > MAX_ID_BYTES {
        return Err(bad());
    }
    let (shard, rest) = sid.0.split_once('.').ok_or_else(bad)?;
    let digits = shard.strip_prefix('s').ok_or_else(bad)?;
    if digits.is_empty() || digits.len() > 9 || !digits.bytes().all(|b| b.is_ascii_digit()) || !plain(rest) {
        return Err(bad());
    }
    Ok(())
}

/// The session a client-supplied task id belongs to: `<session>.<part>`.
pub fn session_of_task(tid: &Tid) -> Result<Sid, GatewayError> {
    let bad = || invalid("task_id_format");
    if tid.0.len() > MAX_ID_BYTES {
        return Err(bad());
    }
    let (session, part) = tid.0.rsplit_once('.').ok_or_else(bad)?;
    if !plain(part) {
        return Err(bad());
    }
    let sid = Sid(session.to_owned());
    parse_session(&sid).map_err(|_| bad())?;
    Ok(sid)
}

/// A request id used in identities must be present and bounded.
pub(crate) fn check_request_id(id: &RequestId) -> Result<(), GatewayError> {
    if id.0.is_empty() || id.0.len() > 256 {
        return Err(invalid("message_id_length"));
    }
    Ok(())
}
