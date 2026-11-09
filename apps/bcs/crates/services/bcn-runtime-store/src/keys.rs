//! Fixed-size, domain-separated keys; caller identities are never interpolated raw.
use sha2::{Digest, Sha256};
use bcn_runtime_model::*;
pub(crate) const PARTITION: &str = "bcn-runtime-v1";
pub(crate) fn digest(parts: &[&str]) -> String {
    let mut hash = Sha256::new();
    for part in parts { hash.update((part.len() as u64).to_be_bytes()); hash.update(part.as_bytes()); }
    format!("{:x}", hash.finalize())
}
pub(crate) fn owner(shard: &ShardId) -> String { format!("owner/{:04}", shard.number) }
pub(crate) fn entity(kind: &str, value: &str) -> String { format!("{kind}/{}", digest(&[value])) }
pub(crate) fn wake(session: &SessionId) -> String { format!("wake/{:04}/{}", session_shard(session).number, digest(&[&session.value])) }
pub(crate) fn inbox_prefix(session: &SessionId) -> String { format!("inbox/{}/", digest(&[&session.value])) }
pub(crate) fn inbox(session: &SessionId, position: u64) -> String { format!("{}{:020}", inbox_prefix(session), position) }
pub(crate) fn input_id(session: &SessionId, event: &EventId) -> String { format!("input-id/{}", digest(&[&session.value, &event.value])) }
pub(crate) fn target_scope(target: &ExecutionTarget) -> &Scope { match target { ExecutionTarget::Agent { agent_id } => &agent_id.scope, ExecutionTarget::Group { group_id } => &group_id.scope } }
pub(crate) fn query_prefix(ctx: &CallerContext, target: Option<&ExecutionTarget>, session: Option<&SessionId>) -> RuntimeResult<String> {
    let target = target.map(serde_json::to_string).transpose().map_err(|e| RuntimeError::InvalidInput(e.to_string()))?.unwrap_or_default();
    Ok(format!("task-index/{}/", digest(&[&ctx.principal, &target, session.map_or("", |s| s.value.as_str())])))
}
