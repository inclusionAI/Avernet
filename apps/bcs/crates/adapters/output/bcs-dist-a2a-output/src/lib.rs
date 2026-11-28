//! Pure A2A formatting of the distributed display model.

use bcs_dist_model::*;
use bcs_service_api::port::dist_output::{DistOutputAdapterPort, OutputAdapterError};
use serde_json::{Value, json};

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

fn artifact(source: &CorrelationKey, id: &ArtifactId, class: OutputClass, format: DisplayContentFormat, value: &Value) -> Result<Value, OutputAdapterError> {
    // Length framing keeps arbitrary source/artifact names collision free. This
    // uses exactly the identity tuple used by the shared display projection.
    let artifact_id = format!("dist:{}:{}:{}:{}", class_name(class), source.0.len(), source.0, id.0);
    let parts = match format {
        DisplayContentFormat::Json => match value {
            Value::String(text) => json!([{ "text": text }]),
            value => json!([{ "data": value }]),
        },
        DisplayContentFormat::Parts => {
            if !value.as_array().is_some_and(|parts| parts.iter().all(Value::is_object)) {
                return Err(invalid("output_parts_require_objects"));
            }
            // Match the HTTP consumer's SDK without re-encoding the original
            // objects: preserve extension fields and per-part metadata.
            serde_json::from_value::<Vec<a2a::Part>>(value.clone())
                .map_err(|_| invalid("output_parts_invalid_a2a"))?;
            value.clone()
        }
    };
    Ok(json!({
        "artifactId": artifact_id,
        "parts": parts,
        "metadata": { "node": source.0, "class": class_name(class) },
    }))
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
            artifacts.push(artifact(&unit.source, &unit.artifact_id, unit.class, unit.content_format, &unit.content)?);
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
        let (payload, append, format) = match &change.update {
            ArtifactUpdate::Upsert { content } => (content, false, DisplayContentFormat::Json),
            ArtifactUpdate::AppendText { delta } => (delta, true, DisplayContentFormat::Json),
            ArtifactUpdate::UpsertParts { content } => (content, false, DisplayContentFormat::Parts),
            ArtifactUpdate::AppendParts { delta } => (delta, true, DisplayContentFormat::Parts),
        };
        let Payload::Inline(value) = payload else {
            return Err(invalid("output_reference_unresolved"));
        };
        if append && format == DisplayContentFormat::Json && !value.is_string() {
            return Err(invalid("output_append_requires_text"));
        }
        Ok(json!({
            "artifact": artifact(&change.source, &change.artifact_id, change.class, format, value)?,
            "append": append,
            "metadata": { "node": change.source.0, "class": class_name(change.class) },
        }))
    }
}
