//! Stable distributed identities shared with the public Worker and Connector.
//! See `bcs-dist-encoding`; concrete stores do not own the wire algorithms.
pub use bcs_dist_encoding::{critical_agent_input_id, digest_fields, digest_json, hex_of};
