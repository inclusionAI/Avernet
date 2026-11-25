//! The JSON carried in task snapshots and artifact events.
//!
//! The injected output adapter formats neutral Worker output before this HTTP
//! boundary. This file only decodes those formatted fields for the A2A envelope:
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
use std::collections::HashMap;

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
    pub metadata: Option<HashMap<String, Value>>,
}

/// `None` when the content carries no readable artifact.
pub(crate) fn artifact_update(value: &Value) -> Option<ArtifactUpdate> {
    Some(ArtifactUpdate {
        artifact: field(value, "artifact")?,
        append: field(value, "append"),
        last_chunk: field(value, "last_chunk"),
        metadata: field(value, "metadata"),
    })
}
