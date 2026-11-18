use super::state::Schedule;
use crate::{codec::decode_json, error::from_read, layout::KeyLayout};
use bcs_dist_model::Sid;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::document::DocumentStorePlugin;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ListenerState {
    Listening,
    NotListening,
    Quarantined,
}
/// Listener state is persisted in the same authority document as activation and
/// owner fencing. The quarantined gate rejects Starts only, never critical input.
pub async fn read_listener(
    documents: &dyn DocumentStorePlugin,
    layout: &KeyLayout,
    sid: &Sid,
) -> Result<ListenerState, StoreError> {
    let Some(doc) = documents
        .read_document(&layout.schedule(sid)?)
        .await
        .map_err(|e| from_read(&e))?
    else {
        return Ok(ListenerState::NotListening);
    };
    let s: Schedule = decode_json(doc.value)?;
    Ok(if s.closed.is_some() {
        ListenerState::Quarantined
    } else if s.listening {
        ListenerState::Listening
    } else {
        ListenerState::NotListening
    })
}
