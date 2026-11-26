//! Stored shapes shared by the Gateway, the Connector and the Worker.
//!
//! | Key | Stored form |
//! | --- | --- |
//! | `admit` | bytes of `{"digest","sid","tid"}` (decision 24: no stream id) |
//! | `inbox` entry | fields `v`, `kind`, `input_id`, `tid`, `digest`, `body` (JSON of `InputBody`) |
//! | `wake` entry | field `sid` |
//! | `spec` | JSON of `SessionSpec` |
//! | `task` | JSON of `TaskSnapshot` |
//! | `ev` entry | id `<commit_seq>-<index>`; fields `v`, `tid`, `kind`, `body` (JSON of `OutputBody`) |
//!
//! `kind`, `input_id` and `tid` are flat fields so entries can be inspected; the
//! decoder rebuilds the envelope from `body`, `digest`, the flat ids and the
//! session the key belongs to.

mod admit;
mod ev;
mod inbox;
mod json;

pub use admit::AdmitRecord;
pub use ev::{decode_ev_entry, encode_ev_entry, ev_entry_tid, event_id, stream_id};
pub use inbox::{decode_inbox_entry, encode_inbox_entry, encode_wake_entry};
pub use json::{decode_json, encode_json};

use bcs_service_api::port::repo::distributed::common::StoreError;

/// Version of stream entries and documents other than SessionSpec and DispatchSpec.
pub const SCHEMA_VERSION: u32 = 1;

fn supported(version: u32) -> Result<(), StoreError> {
    if version == SCHEMA_VERSION {
        Ok(())
    } else {
        Err(StoreError::UnsupportedFormat { format: version })
    }
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid {
        code: code.to_owned(),
    }
}
