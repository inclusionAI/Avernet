//! Project the AgentEvent payloads produced by the distributed A2A client.
//! Keep just artifact identities and text eligibility in the checkpoint. The
//! Worker owns their accumulated content and applies its existing output quotas.
use bcs_dist_encoding::digest_fields;
use bcs_dist_model::*;
use bcs_service_api::core::dist_runner::ActionIntent;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeSet;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub(super) struct Artifacts {
    first: Option<ArtifactId>,
    text_slots: BTreeSet<ArtifactId>,
}

pub(super) fn message_text(message: &Value) -> Option<String> {
    let parts = message.get("parts")?.as_array()?;
    let mut text = String::new();
    for part in parts {
        if let Some(s) = part.get("text").and_then(Value::as_str) {
            text.push_str(s);
        }
    }
    (!text.is_empty()).then_some(text)
}

fn parts_content(source: &Value) -> Option<Value> {
    let parts = source.get("parts")?.as_array()?;
    if parts.is_empty() {
        return None;
    }
    if parts.iter().all(|part| part.get("text").is_some_and(Value::is_string)) {
        return Some(Value::String(
            parts.iter().filter_map(|part| part["text"].as_str()).collect(),
        ));
    }
    // Keep data, file and mixed parts intact, including their metadata.
    Some(Value::Array(parts.clone()))
}

fn show(
    tid: &Tid,
    artifact_id: ArtifactId,
    class: OutputClass,
    update: ArtifactUpdate,
) -> ActionIntent {
    ActionIntent::Show {
        tid: tid.clone(),
        source: CorrelationKey("agent".into()),
        artifact_id,
        class,
        update,
    }
}

impl Artifacts {
    pub(super) fn update(
        &mut self,
        tid: &Tid,
        kind: &AgentEventKind,
        value: &Value,
        actions: &mut Vec<ActionIntent>,
    ) {
        match kind {
            AgentEventKind::Delta => {
                self.artifact(tid, &value["artifact"], value["append"] == true, actions);
            }
            AgentEventKind::Snapshot => {
                self.artifact_list(tid, &value["task"]["artifacts"], actions);
            }
            AgentEventKind::Tool => {
                let tool = &value["tool"];
                if let Some(id) = tool["toolCallId"].as_str() {
                    let key = ArtifactId(digest_fields(&[b"direct-tool", id.as_bytes()]).0);
                    actions.push(show(
                        tid,
                        key,
                        OutputClass::Tool,
                        ArtifactUpdate::Upsert { content: Payload::Inline(tool.clone()) },
                    ));
                }
            }
            _ => {}
        }
    }

    pub(super) fn final_output(
        &mut self,
        tid: &Tid,
        value: &Value,
        actions: &mut Vec<ActionIntent>,
    ) {
        if self.artifact_list(tid, &value["artifacts"], actions) {
            return;
        }
        if let Some(content) = parts_content(&value["message"]) {
            actions.push(show(
                tid,
                ArtifactId("answer".into()),
                OutputClass::Output,
                ArtifactUpdate::Upsert { content: Payload::Inline(content) },
            ));
        }
    }

    fn artifact_list(
        &mut self,
        tid: &Tid,
        artifacts: &Value,
        actions: &mut Vec<ActionIntent>,
    ) -> bool {
        let before = actions.len();
        if let Some(artifacts) = artifacts.as_array() {
            for artifact in artifacts {
                self.artifact(tid, artifact, false, actions);
            }
        }
        actions.len() > before
    }

    fn artifact(
        &mut self,
        tid: &Tid,
        artifact: &Value,
        append: bool,
        actions: &mut Vec<ActionIntent>,
    ) {
        let Some(content) = parts_content(artifact) else {
            return;
        };
        let Some(id) = artifact["artifactId"].as_str() else {
            return;
        };
        let key = ArtifactId(digest_fields(&[b"direct-artifact", id.as_bytes()]).0);
        let slot = if self.first.get_or_insert_with(|| key.clone()) == &key {
            ArtifactId("answer".into())
        } else {
            key
        };
        let was_text = self.text_slots.remove(&slot);
        let text = content.is_string();
        if text {
            self.text_slots.insert(slot.clone());
        }
        let payload = Payload::Inline(content);
        let update = if append && was_text && text {
            ArtifactUpdate::AppendText { delta: payload }
        } else {
            ArtifactUpdate::Upsert { content: payload }
        };
        actions.push(show(tid, slot, OutputClass::Output, update));
    }
}
