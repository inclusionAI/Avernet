//! Pure A2A formatting of the distributed display model.

use a2a::{Artifact, Part};
use bcs_dist_model::*;
use bcs_service_api::port::dist_output::{DistOutputAdapterPort, OutputAdapterError};
use serde_json::{Value, json};
use std::collections::HashMap;

#[derive(Debug, Default)]
pub struct A2aOutputAdapter;

fn invalid(code: &'static str) -> OutputAdapterError {
    OutputAdapterError { code }
}

fn class_name(class: OutputClass) -> &'static str {
    match class {
        OutputClass::Output => "output",
        OutputClass::Tool => "tool",
        OutputClass::Thinking => "thinking",
    }
}

fn artifact(node: &NodeId, id: &ArtifactId, class: OutputClass, value: &Value) -> Artifact {
    // Length framing keeps arbitrary node/artifact names collision free. This
    // uses exactly the identity tuple used by the shared display projection.
    let artifact_id = format!("dist:{}:{}:{}:{}", class_name(class), node.0.len(), node.0, id.0);
    Artifact {
        artifact_id,
        name: None,
        description: None,
        parts: vec![match value {
            Value::String(text) => Part::text(text),
            value => Part::data(value.clone()),
        }],
        metadata: Some(HashMap::from([
            ("node".to_owned(), json!(node.0)),
            ("class".to_owned(), json!(class_name(class))),
        ])),
        extensions: None,
    }
}

impl DistOutputAdapterPort for A2aOutputAdapter {
    fn snapshot(&self, snapshot: &Value) -> Result<Value, OutputAdapterError> {
        let display: DisplaySnapshot = serde_json::from_value(snapshot.clone())
            .map_err(|_| invalid("output_snapshot_invalid"))?;
        if display.format_version != DISPLAY_FORMAT_VERSION {
            return Err(invalid("output_snapshot_format_unsupported"));
        }
        let mut artifacts = Vec::with_capacity(display.units.len());
        for unit in display.units {
            if unit.class == OutputClass::Thinking {
                return Err(invalid("output_snapshot_contains_thinking"));
            }
            artifacts.push(artifact(&unit.node, &unit.artifact_id, unit.class, &unit.content));
        }
        let mut result = json!({ "artifacts": artifacts });
        // DisplaySnapshot itself has no history or status message. Preserve
        // explicit sources when supplied; never infer messages from artifacts.
        for field in ["history", "status_message"] {
            if let Some(value) = snapshot.get(field) {
                result[field] = value.clone();
            }
        }
        Ok(result)
    }

    fn artifact(&self, change: &ArtifactChange) -> Result<Value, OutputAdapterError> {
        let (payload, append) = match &change.update {
            ArtifactUpdate::Upsert { content } => (content, false),
            ArtifactUpdate::AppendText { delta } => (delta, true),
        };
        let Payload::Inline(value) = payload else {
            return Err(invalid("output_reference_unresolved"));
        };
        if append && !value.is_string() {
            return Err(invalid("output_append_requires_text"));
        }
        Ok(json!({
            "artifact": artifact(&change.node, &change.artifact_id, change.class, value),
            "append": append,
            "metadata": { "node": change.node.0, "class": class_name(change.class) },
        }))
    }
}
