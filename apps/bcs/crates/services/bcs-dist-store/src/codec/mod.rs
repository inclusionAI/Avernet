//! Stored shapes shared by the Gateway, the Connector and the Worker.
//!
//! | Key | Stored form |
//! | --- | --- |
//! | `admit` | bytes of `{"digest","sid","tid"}` (decision 24: no stream id) |
//! | `inbox` entry | fields `v`, `kind`, `input_id`, `tid`, `digest`, `body` (JSON of `InputBody`) |
//! | `wake` entry | field `sid` |
//! | `spec` | JSON of `SessionSpec` |
//! | `task` | JSON of `TaskSnapshot` |
//!
//! `kind`, `input_id` and `tid` are flat fields so entries can be inspected; the
//! decoder rebuilds the envelope from `body`, `digest`, the flat ids and the
//! session the key belongs to.

mod admit;
mod inbox;
mod json;

pub use admit::AdmitRecord;
pub use inbox::{decode_inbox_entry, encode_inbox_entry, encode_wake_entry};
pub use json::{decode_json, encode_json};

use bcs_service_api::port::repo::distributed::common::StoreError;

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid {
        code: code.to_owned(),
    }
}
