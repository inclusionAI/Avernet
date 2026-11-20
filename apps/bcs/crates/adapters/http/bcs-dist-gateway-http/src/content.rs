//! The JSON carried in task snapshots and artifact events.
//!
//! The model stores both as opaque payloads; this file is the only place that
//! knows their shape. They are A2A-shaped JSON written by the Worker and
//! passed through:
//!
//! ```json
//! // task snapshot (display state)
//! { "artifacts": [<A2A Artifact>], "history": [<A2A Message>], "status_message": <A2A Message> }
//! // artifact event content
//! { "artifact": <A2A Artifact>, "append": true, "last_chunk": false }
//! ```
//!
//! Every field is optional. A field that does not parse is dropped and logged;
//! it never fails the request. A payload still held by reference (task lists)
//! shows no content.

use a2a::{Artifact, Message};
use bcs_dist_model::Payload;
use serde::de::DeserializeOwned;
use serde_json::Value;

#[derive(Debug, Default)]
pub(crate) struct Display {
    pub artifacts: Option<Vec<Artifact>>,
    pub history: Option<Vec<Message>>,
    pub status_message: Option<Message>,
}

fn field<T: DeserializeOwned>(object: &Value, name: &str) -> Option<T> {
    let value = object.get(name).filter(|v| !v.is_null())?;
    match T::deserialize(value) {
        Ok(parsed) => Some(parsed),
        Err(error) => {
            tracing::warn!(field = name, %error, "dropping unreadable display content");
            None
        }
    }
}

pub(crate) fn display(snapshot: &Payload) -> Display {
    let Payload::Inline(value) = snapshot else {
        return Display::default();
    };
    Display {
        artifacts: field(value, "artifacts"),
        history: field(value, "history"),
        status_message: field(value, "status_message"),
    }
}

#[derive(Debug)]
pub(crate) struct ArtifactUpdate {
    pub artifact: Artifact,
    pub append: Option<bool>,
    pub last_chunk: Option<bool>,
}

/// `None` when the content carries no readable artifact.
pub(crate) fn artifact_update(content: &Payload) -> Option<ArtifactUpdate> {
    let Payload::Inline(value) = content else {
        return None;
    };
    Some(ArtifactUpdate {
        artifact: field(value, "artifact")?,
        append: field(value, "append"),
        last_chunk: field(value, "last_chunk"),
    })
}
