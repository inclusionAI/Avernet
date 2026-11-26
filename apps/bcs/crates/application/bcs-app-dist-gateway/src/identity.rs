//! Stable identities derived from a request.
//!
//! Everything a retry must reproduce is derived, never random: the same
//! message from the same caller selects the same session, task and input, so
//! the admission record of the first attempt answers the retry.
//!
//! | Id | Form | Derived from |
//! | --- | --- | --- |
//! | session | `s<shard:04>.<16 hex>` | tenant, principal, group, message id; shard = hash mod shard count |
//! | task | `<session>.<16 hex>` | session, message id |
//! | input (user) | `in-<32 hex>` | tenant, principal, group, session, message id |
//! | input (reply) | `in-<32 hex>` | principal, session, task, message id |
//! | input (cancel) | `in-<32 hex>` | principal, session, task, request id |
//!
//! Digests are SHA-256 over length-prefixed fields, so `("ab","c")` and
//! `("a","bc")` differ. Content digests hash canonical JSON (sorted keys).

use crate::invalid;
use bcs_dist_model::*;
use bcs_service_api::application::dist_gateway::{CreationSource, GatewayError};
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

pub(crate) fn session_id(scope: &TaskScope, group: &GroupId, message: &RequestId, shards: u32) -> Sid {
    let digest = hash(&["sid", &scope.tenant.0, &scope.principal.0, &group.0, &message.0]);
    let mut head = [0u8; 8];
    head.copy_from_slice(&digest[..8]);
    let shard = u64::from_be_bytes(head) % u64::from(shards.max(1));
    Sid(format!("s{shard:04}.{}", hex::encode(&digest[8..16])))
}

/// The group-A2A session namespace: same derivation domain and a `g`
/// marker in the suffix, so neither entrance can recreate the other's
/// identity for the same message key (plan T8 source isolation). The
/// session's shard is the assembly's mounted shard, never a spread
/// bucket: the strict round admission binding follows ONE shard record
/// (plan T9), so a group-entrance session must always land on the licensed
/// shard claim its rounds admit through.
pub(crate) fn group_session_id(
    scope: &TaskScope,
    group: &GroupId,
    message: &RequestId,
    shard: u32,
) -> Sid {
    let digest = hash(&[
        "group_a2a:v1",
        &scope.tenant.0,
        &scope.principal.0,
        &group.0,
        &message.0,
    ]);
    Sid(format!("s{shard:04}.g{}", hex::encode(&digest[8..16])))
}

pub(crate) fn task_id(sid: &Sid, message: &RequestId) -> Tid {
    let digest = hash(&["tid", &sid.0, &message.0]);
    Tid(format!("{}.{}", sid.0, hex::encode(&digest[..8])))
}

pub(crate) fn group_task_id(sid: &Sid, message: &RequestId) -> Tid {
    let digest = hash(&["group_tid:v1", &sid.0, &message.0]);
    Tid(format!("{}.g{}", sid.0, hex::encode(&digest[..8])))
}

/// The durable creation source encoded in a session id's namespace (the
/// shared contract lives with `CreationSource`: plain hex = Rpc, `g`-marked
/// hex = GroupA2a). Unknown shapes are refused, never folded into one source.
pub fn creation_source_of_sid(sid: &Sid) -> Result<CreationSource, GatewayError> {
    CreationSource::of_sid(sid).ok_or_else(|| invalid("session_id_format"))
}

fn input_id(fields: &[&str]) -> InputId {
    InputId(format!("in-{}", hex::encode(&hash(fields)[..16])))
}

pub(crate) fn user_input_id(scope: &TaskScope, group: &GroupId, sid: &Sid, message: &RequestId) -> InputId {
    input_id(&["user", &scope.tenant.0, &scope.principal.0, &group.0, &sid.0, &message.0])
}

pub(crate) fn group_user_input_id(
    scope: &TaskScope,
    group: &GroupId,
    sid: &Sid,
    message: &RequestId,
) -> InputId {
    input_id(&[
        "group_user:v1",
        &scope.tenant.0,
        &scope.principal.0,
        &group.0,
        &sid.0,
        &message.0,
    ])
}

pub(crate) fn reply_input_id(scope: &TaskScope, tid: &Tid, sid: &Sid, message: &RequestId) -> InputId {
    input_id(&["human_reply", &scope.principal.0, &sid.0, &tid.0, &message.0])
}

pub(crate) fn group_reply_input_id(
    scope: &TaskScope,
    tid: &Tid,
    sid: &Sid,
    message: &RequestId,
) -> InputId {
    input_id(&[
        "group_reply:v1",
        &scope.principal.0,
        &sid.0,
        &tid.0,
        &message.0,
    ])
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

pub(crate) fn group_cancel_input_id(
    scope: &TaskScope,
    tid: &Tid,
    sid: &Sid,
    request: &RequestId,
) -> InputId {
    input_id(&[
        "group_cancel:v1",
        &scope.principal.0,
        &sid.0,
        &tid.0,
        &request.0,
    ])
}

/// The identity derivation of one entrance: every session, task and input id
/// of a request is derived through its own source's namespace only, so no
/// entrance can ever recreate the other's identities for the same message key.
#[derive(Debug, Clone, Copy)]
pub(crate) struct Derivation {
    pub session: fn(&TaskScope, &GroupId, &RequestId, u32) -> Sid,
    pub task: fn(&Sid, &RequestId) -> Tid,
    pub user_input: fn(&TaskScope, &GroupId, &Sid, &RequestId) -> InputId,
    pub reply_input: fn(&TaskScope, &Tid, &Sid, &RequestId) -> InputId,
    pub cancel_input: fn(&TaskScope, &Tid, &Sid, &RequestId) -> InputId,
}

impl Derivation {
    pub fn of(source: CreationSource) -> Self {
        match source {
            CreationSource::Rpc => Self {
                session: session_id,
                task: task_id,
                user_input: user_input_id,
                reply_input: reply_input_id,
                cancel_input: cancel_input_id,
            },
            CreationSource::GroupA2a => Self {
                session: group_session_id,
                task: group_task_id,
                user_input: group_user_input_id,
                reply_input: group_reply_input_id,
                cancel_input: group_cancel_input_id,
            },
        }
    }
}
