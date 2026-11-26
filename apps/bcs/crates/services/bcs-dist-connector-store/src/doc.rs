//! The stored dispatch document (`disp`), as the Connector reads and writes it.
//!
//! Layout: the JSON of the merged `DispatchView` (`spec`, `control`,
//! `execution`) plus a Connector-owned `connector` object. The Worker writes
//! `spec` (at creation) and `control`; the holding Connector writes `execution`
//! and `connector` through one object merge. The Worker can omit `connector` at creation.
//! Other top-level fields the Worker keeps for itself (`receipts`, `due`) are
//! ignored when decoding; the only one the Connector writes is `due` (see
//! [`connector_write`]).
//!
//! ```text
//! key:   bcn:v2:c01:main:s0042:disp:s0042.7f3a9c.b21e.n2.a1
//! value: {"spec": {"schema_version": 2, "did": "s0042.7f3a9c.b21e.n2.a1",
//!                  "agent": "agent-analyst", "downstream_context": "s0042.7f3a9c:0", ...},
//!         "control": {"cancel": null, "pending_input": [{"seq": 1, ...}]},
//!         "execution": {"holder": {"instance": {"node": "conn-03",
//!                       "incarnation": "1794370950"}, "epoch": 1},
//!                       "phase": "accepted", "remote": {"task_id": "t-81",
//!                       "context_id": null}, "confirmed_input_through": null,
//!                       "cancellation": {"kind": "not_sent"}, "outcome": null},
//!         "connector": {"input_sending": 1, "finish_evidence": null},
//!         "receipts": [...], "due": 1794371000000}
//! ```
//!
//! Nothing here reads or writes the store: this module only decodes a read
//! and builds patches (`JsonMutation::Merge`). The store calls are in
//! `shared.rs`; the fencing (which version a patch is written at) is in
//! `queue.rs` and `held.rs`.
//!
//! Glossary:
//! - **Phase** (`/execution/phase`): `created` (Worker made it) -> `claimed`
//!   (first claim) -> `sending` (the first message may be on its way) ->
//!   `accepted` (the agent's task id is stored) -> `ended`.
//! - **Pending input** (`/control/pending_input`): human replies in the
//!   Worker's queue. "Pending" means "queued by the Worker", not
//!   "unconfirmed": only the Worker removes entries, so an entry stays after
//!   the holder confirmed it until the Worker cleans up.
//! - **Confirmed watermark** (`/execution/confirmed_input_through`): the
//!   highest reply seq the agent definitely accepted (spec rule B12).

use bcs_dist_model::{
    DispatchControlState, DispatchExecution, DispatchSpec, DispatchView, InputSeq,
};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::json::JsonMutation;
use bcs_store_api::{Version, Versioned};
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Facts only the Connector needs, which `DispatchExecution` has no field for.
///
/// Stored as `/connector`, e.g. `{"input_sending": 2, "finish_evidence": null}`.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConnectorState {
    /// Highest pending-input seq a send was started for. A later holder treats
    /// a seq at or below it and above the confirmed watermark as "maybe sent".
    ///
    /// Covers human replies only. The send intent of the first message is the
    /// phase itself (`claimed -> sending`); a new holder that finds `sending`
    /// must resolve the start instead.
    #[serde(default)]
    pub input_sending: Option<InputSeq>,
    /// The evidence string given to `finish`, e.g. `"sync_result"` or
    /// `"already_finished"`: why the holder ended the call. Diagnostic only.
    #[serde(default)]
    pub finish_evidence: Option<String>,
}

/// A dispatch document as read, with the version it was read at.
#[derive(Debug, Clone, PartialEq)]
pub struct DispatchDoc {
    /// `spec`, `control` and `execution` exactly as stored (pending inputs the
    /// holder already confirmed are still in it; see [`holder_view`]).
    pub view: DispatchView,
    /// The stored `connector` object, or its default when there is none yet.
    pub connector: ConnectorState,
    /// Whether the stored document has a `connector` object yet. A claim
    /// creates an empty one when it is missing.
    pub has_connector: bool,
    /// The store version of this read. Holder writes go to `version + 1`
    /// (same epoch band); a claim jumps to the next band.
    pub version: Version,
}

/// The decoded shape of a stored document. Unknown top-level fields (the
/// Worker's `receipts`, `due`) are ignored.
#[derive(Deserialize)]
struct Stored {
    spec: DispatchSpec,
    control: DispatchControlState,
    execution: DispatchExecution,
    #[serde(default)]
    connector: Option<ConnectorState>,
}

/// Decodes a stored document. The schema version is checked before the shape.
///
/// A document of another schema version is `UnsupportedFormat` (a permanent
/// error, never a reason to guess); a document that does not match the shape
/// is `Invalid` with code `disp_decode`.
pub fn decode(stored: Versioned<Value>) -> Result<DispatchDoc, StoreError> {
    let format = stored
        .value
        .pointer("/spec/schema_version")
        .and_then(Value::as_u64)
        .and_then(|v| u32::try_from(v).ok())
        .ok_or_else(|| invalid("disp_schema_version"))?;
    if format != bcs_dist_model::DISPATCH_SCHEMA_VERSION {
        return Err(StoreError::UnsupportedFormat { format });
    }
    let doc: Stored = serde_json::from_value(stored.value).map_err(|_| invalid("disp_decode"))?;
    Ok(DispatchDoc {
        view: DispatchView {
            spec: doc.spec,
            control: doc.control,
            execution: doc.execution,
        },
        has_connector: doc.connector.is_some(),
        connector: doc.connector.unwrap_or_default(),
        version: stored.version,
    })
}

/// Diff only the two Connector-owned objects at the version already read.
/// Nested objects are diffed key by key; a key missing from `new` becomes
/// `null`; any other changed value is sent whole. `None` = nothing changed.
/// Optional null and absent both decode as None; clearing an enum variant's
/// fields requires explicit nulls (MERGE does not remove omitted keys).
fn delta(old: &Value, new: &Value) -> Option<Value> {
    if old == new {
        return None;
    }
    if let (Value::Object(a), Value::Object(b)) = (old, new) {
        let mut patch = serde_json::Map::new();
        for k in a.keys().filter(|k| !b.contains_key(*k)) {
            patch.insert(k.clone(), Value::Null);
        }
        for (k, v) in b {
            if let Some(change) = delta(a.get(k).unwrap_or(&Value::Null), v) {
                patch.insert(k.clone(), change);
            }
        }
        return Some(Value::Object(patch));
    }
    Some(new.clone())
}

/// Builds the merge patch of one holder write: only what changed in
/// `execution` and `connector` compared with `old` (the document this write is
/// based on). The Worker's `spec` and `control` are never in the patch, so a
/// holder write cannot undo a Worker write. The caller writes it at
/// `old.version + 1`, so the patch is applied only on top of exactly `old`.
///
/// When this write ends the call, it also sets the Worker's top-level `due`
/// to 0 so the Worker's dispatch scan picks the ended call up at once.
pub fn connector_write(
    old: &DispatchDoc,
    execution: &DispatchExecution,
    connector: &ConnectorState,
) -> Result<JsonMutation, StoreError> {
    let encode = |value: Result<Value, _>| value.map_err(|_| invalid("disp_encode"));
    let mut patch = serde_json::Map::new();
    // A terminal report must be reconsidered even after a long supervisor backoff.
    if old.view.execution.phase != execution.phase
        && execution.phase == bcs_dist_model::DispatchPhase::Ended
    {
        patch.insert("due".into(), Value::from(0));
    }
    for (key, before, after) in [
        (
            "execution",
            encode(serde_json::to_value(&old.view.execution))?,
            encode(serde_json::to_value(execution))?,
        ),
        (
            "connector",
            encode(serde_json::to_value(&old.connector))?,
            encode(serde_json::to_value(connector))?,
        ),
    ] {
        if let Some(value) = delta(&before, &after) {
            patch.insert(key.into(), value);
        }
    }
    Ok(JsonMutation::Merge(patch))
}

/// Builds the merge patch of a claim: `execution.holder` only (plus
/// `phase: claimed` on the first claim, and an empty `connector` object when
/// the document has none yet).
///
/// A higher-generation claim may race intervening writes: never send a snapshot.
/// The claim is written at `epoch x 10^9`, a version higher than any write of
/// the former holder, so it does not require the version it read; a full copy
/// of the read document could overwrite a Worker or former-holder write made
/// in between. Phase, remote task and watermarks therefore stay as stored.
pub fn claim_write(
    holder: &bcs_dist_model::Holder,
    first_claim: bool,
    create_connector: bool,
) -> Result<JsonMutation, StoreError> {
    let mut execution = serde_json::Map::new();
    execution.insert(
        "holder".into(),
        serde_json::to_value(holder).map_err(|_| invalid("disp_encode"))?,
    );
    if first_claim {
        execution.insert("phase".into(), Value::String("claimed".into()));
    }
    let mut patch = serde_json::Map::new();
    patch.insert("execution".into(), Value::Object(execution));
    if create_connector {
        patch.insert("connector".into(), Value::Object(Default::default()));
    }
    Ok(JsonMutation::Merge(patch))
}

/// The view a holder works with: pending inputs at or below the confirmed
/// watermark are hidden (removing them from the document is the Worker's job).
/// The holder sends the reply with `seq = confirmed + 1` next (spec rule B12).
pub fn holder_view(mut view: DispatchView) -> DispatchView {
    if let Some(confirmed) = view.execution.confirmed_input_through {
        view.control.pending_input.retain(|input| input.seq > confirmed);
    }
    view
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid {
        code: code.to_owned(),
    }
}
