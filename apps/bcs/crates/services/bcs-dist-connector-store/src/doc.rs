//! The stored dispatch document.
//!
//! Layout: the JSON of the merged `DispatchView` (`spec`, `control`,
//! `execution`) plus a Connector-owned `connector` object. The Worker writes
//! `spec` (at creation) and `control`; the holding Connector writes `execution`
//! and `connector`, always as whole objects built from what it just read (the
//! Worker creates the document without `connector`, and a path edit needs an
//! existing parent).

use bcs_dist_model::{DispatchControlState, DispatchExecution, DispatchSpec, DispatchView, InputSeq};
use bcs_dist_store::codec::SCHEMA_VERSION;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::json::{JsonEdit, JsonMutation};
use bcs_store_api::{Version, Versioned};
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Facts only the Connector needs, which `DispatchExecution` has no field for.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConnectorState {
    /// Highest pending-input seq a send was started for. A later holder treats
    /// a seq at or below it and above the confirmed watermark as "maybe sent".
    #[serde(default)]
    pub input_sending: Option<InputSeq>,
    /// The evidence string given to `finish`.
    #[serde(default)]
    pub finish_evidence: Option<String>,
}

/// A dispatch document as read, with the version it was read at.
#[derive(Debug, Clone, PartialEq)]
pub struct DispatchDoc {
    pub view: DispatchView,
    pub connector: ConnectorState,
    pub version: Version,
}

#[derive(Deserialize)]
struct Stored {
    spec: DispatchSpec,
    control: DispatchControlState,
    execution: DispatchExecution,
    #[serde(default)]
    connector: Option<ConnectorState>,
}

/// Decodes a stored document. The schema version is checked before the shape.
pub fn decode(stored: Versioned<Value>) -> Result<DispatchDoc, StoreError> {
    let format = stored
        .value
        .pointer("/spec/schema_version")
        .and_then(Value::as_u64)
        .and_then(|v| u32::try_from(v).ok())
        .ok_or_else(|| invalid("disp_schema_version"))?;
    if format != SCHEMA_VERSION {
        return Err(StoreError::UnsupportedFormat { format });
    }
    let doc: Stored = serde_json::from_value(stored.value).map_err(|_| invalid("disp_decode"))?;
    Ok(DispatchDoc {
        view: DispatchView {
            spec: doc.spec,
            control: doc.control,
            execution: doc.execution,
        },
        connector: doc.connector.unwrap_or_default(),
        version: stored.version,
    })
}

/// The Connector's write: both of its objects, whole. Never `/spec` or `/control`.
pub fn connector_write(
    execution: &DispatchExecution,
    connector: &ConnectorState,
) -> Result<JsonMutation, StoreError> {
    let set = |pointer: &str, value: Result<Value, serde_json::Error>| {
        value
            .map(|value| JsonEdit::Set {
                pointer: pointer.to_owned(),
                value,
            })
            .map_err(|_| invalid("disp_encode"))
    };
    Ok(JsonMutation::Patch(vec![
        set("/execution", serde_json::to_value(execution))?,
        set("/connector", serde_json::to_value(connector))?,
    ]))
}

/// The view a holder works with: pending inputs at or below the confirmed
/// watermark are hidden (removing them from the document is the Worker's job).
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
