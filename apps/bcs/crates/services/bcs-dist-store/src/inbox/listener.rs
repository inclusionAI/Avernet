//! The admission step that reads whether a Worker is listening to a session.

use crate::error::need_contract;
use crate::layout::KeyLayout;
use bcs_dist_model::Sid;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::document::DocumentStorePlugin;

/// What admission needs to know about the session's Worker.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ListenerState {
    /// A Worker of the current owner epoch reads the inbox: no `wake` needed.
    Listening,
    /// Nobody (valid) listens: admission writes a `wake` hint.
    NotListening,
    /// The session is quarantined: admission rejects the input.
    Quarantined,
}

/// Delayed contract D1 (walkthrough §16, Gaps 1 and 2): the admission ↔ Worker
/// listener and idle-exit protocol. Which `ckpt` fields mean "a valid listener"
/// (listener flag, owner epoch, quarantine) and whether admission must also
/// invalidate a scheduling token are not agreed with the Worker yet.
///
/// Until then this always fails with `need_contract_not_implemented`. Admission
/// treats that as `NotListening` and writes `wake`, which is also the rule for a
/// failed read, so it stays correct; the quarantine check is skipped.
pub async fn read_listener(
    _documents: &dyn DocumentStorePlugin,
    _layout: &KeyLayout,
    _sid: &Sid,
) -> Result<ListenerState, StoreError> {
    Err(need_contract())
}
