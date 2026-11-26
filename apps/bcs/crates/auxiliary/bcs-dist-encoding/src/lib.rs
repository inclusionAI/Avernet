//! Stable digests for identities and content.
//!
//! Format: `sha256:<64 hex>`. Field digests hash each field with an 8-byte
//! big-endian length prefix, so `("ab", "c")` and `("a", "bc")` differ. Content
//! digests hash canonical JSON (object keys sorted at every level), so the same
//! value always has the same digest whatever map order produced it.

pub mod connector;

use bcs_dist_model::Digest;
use serde_json::Value;
use sha2::{Digest as _, Sha256};

/// Digest of an ordered list of fields.
pub fn digest_fields(fields: &[&[u8]]) -> Digest {
    let mut hasher = Sha256::new();
    for field in fields {
        hasher.update((field.len() as u64).to_be_bytes());
        hasher.update(field);
    }
    Digest(format!("sha256:{}", hex::encode(hasher.finalize())))
}

/// Digest of a JSON value in canonical form.
pub fn digest_json(value: &Value) -> Digest {
    let mut canonical = Vec::new();
    write_canonical(value, &mut canonical);
    digest_fields(&[&canonical])
}

/// Hex part of a digest, without the `sha256:` prefix, for building ids.
pub fn hex_of(digest: &Digest) -> &str {
    digest.0.strip_prefix("sha256:").unwrap_or(&digest.0)
}

fn write_canonical(value: &Value, out: &mut Vec<u8>) {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            out.push(b'{');
            for (i, key) in keys.iter().enumerate() {
                if i > 0 {
                    out.push(b',');
                }
                write_canonical(&Value::String((*key).clone()), out);
                out.push(b':');
                if let Some(inner) = map.get(*key) {
                    write_canonical(inner, out);
                }
            }
            out.push(b'}');
        }
        Value::Array(items) => {
            out.push(b'[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(b',');
                }
                write_canonical(item, out);
            }
            out.push(b']');
        }
        scalar => out.extend_from_slice(scalar.to_string().as_bytes()),
    }
}

/// Stable reliable Agent input identity. The caller keeps the same remote event
/// ID across holder changes. The provider verifies this value before journaling;
/// event kind and payload belong to the canonical content digest, not the ID.
pub fn critical_agent_input_id(
    did: &bcs_dist_model::Did,
    remote_event_id: &str,
) -> bcs_dist_model::InputId {
    let digest = digest_fields(&[
        b"critical-agent:v1",
        did.0.as_bytes(),
        remote_event_id.as_bytes(),
    ]);
    bcs_dist_model::InputId(format!("ac-{}", hex_of(&digest)))
}

/// Domain-separated digest of exact immutable Blob bytes. This is shared by
/// producers, readers and persistence; parsing JSON does not change this digest.
pub fn blob_reference(key: &str, bytes: &[u8]) -> bcs_dist_model::BlobRef {
    bcs_dist_model::BlobRef {
        key: key.into(),
        digest: digest_fields(&[b"blob:v1", bytes]),
    }
}
