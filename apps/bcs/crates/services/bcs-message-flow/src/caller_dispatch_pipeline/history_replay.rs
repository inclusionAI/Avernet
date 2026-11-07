//! Frozen, bounded history body reconstruction. No latest-history query and no
//! identity persistence: exact IDs are fetched once per retry in a batch.
use std::sync::Arc;
use bcs_service_api::port::{HistoryCompleteCommand, HistoryBackfillError as Error};
use bcs_service_api::port::repo::MessageRepoPort;

pub(super) async fn load_messages(
    repo: Option<&Arc<dyn MessageRepoPort>>, snapshot: &HistoryCompleteCommand,
    target_bot: &str,
) -> Result<Vec<(String, String)>, Error> {
    if snapshot.message_ids.len() > 256 || bcs_domain::history_manifest::canonical_manifest_digest(
        target_bot, &snapshot.session_id, snapshot.history_cutoff_seq,
        snapshot.projection_version, &snapshot.message_ids) != snapshot.manifest_digest {
        return Err(Error::NotSent);
    }
    if snapshot.message_ids.is_empty() { return Ok(Vec::new()); }
    let repo = repo.ok_or(Error::NotSent)?;
    let rows = repo.get_messages_by_ids(&snapshot.session_id, &snapshot.message_ids).await
        .map_err(|_| Error::Unavailable { retryable: true })?;
    let mut by_id = std::collections::HashMap::new();
    for row in rows {
        if row.session_id != snapshot.session_id || row.session_seq < 0
            || row.session_seq as u64 > snapshot.history_cutoff_seq
            || by_id.insert(row.message_id.clone(), row).is_some() { return Err(Error::NotSent); }
    }
    let mut result = Vec::new();
    let mut bytes = 0usize;
    for id in &snapshot.message_ids {
        let row = by_id.remove(id).ok_or(Error::NotSent)?;
        // The existing Caller transport is a plain-text projection. Never dump
        // the entire JSON record (tool/private fields) or silently omit media.
        if row.content.get("attachments").is_some_and(|v| !v.is_null() && v.as_array().is_none_or(|a| !a.is_empty())) {
            return Err(Error::NotSent);
        }
        let text = row.content.as_str().or_else(|| row.content.get("text").and_then(|v| v.as_str()))
            .filter(|v| !v.is_empty()).ok_or(Error::NotSent)?;
        bytes = bytes.saturating_add(text.len());
        if bytes > 128 * 1024 { return Err(Error::NotSent); }
        result.push((id.clone(), text.to_owned()));
    }
    if !by_id.is_empty() { return Err(Error::NotSent); }
    Ok(result)
}
