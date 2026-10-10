//! File-backed engine observation for local bridge debugging.
//!
//! The trace is deliberately separate from the protocol path. It records the
//! exact engine stdout line before parsing, a cleaned copy of engine stderr,
//! the normalized event produced by a driver, and the final SSE frame emitted
//! by the run loop. Trace failures are diagnostic-only: they are logged but do
//! not change engine behavior.

use std::fs::{File, OpenOptions, create_dir_all};
use std::io::{self, Write};
use std::path::Path;
use std::sync::{Arc, Mutex};

use bcs_protocol::stream::{AgentData, StreamEvent};
use serde_json::{Value, json};

pub struct TraceStore {
    raw: Mutex<File>,
    stderr: Mutex<File>,
    converted: Mutex<File>,
    sse: Mutex<File>,
}

#[derive(Clone)]
pub struct TraceContext {
    store: Arc<TraceStore>,
    engine: String,
    run_id: String,
}

impl TraceStore {
    pub fn open(dir: &Path) -> io::Result<Arc<Self>> {
        create_dir_all(dir)?;
        Ok(Arc::new(Self {
            raw: Mutex::new(open_append(dir.join("engine.raw.ndjson"))?),
            stderr: Mutex::new(open_append(dir.join("engine.stderr.ndjson"))?),
            converted: Mutex::new(open_append(dir.join("bridge.converted.ndjson"))?),
            sse: Mutex::new(open_append(dir.join("bridge.sse.ndjson"))?),
        }))
    }

    fn append(&self, stream: &str, file: &Mutex<File>, value: Value) {
        let line = match serde_json::to_string(&value) {
            Ok(line) => line,
            Err(error) => {
                tracing::warn!(
                    target: "bcs_bridge_core::trace",
                    stream,
                    error = %error,
                    "failed to encode trace record"
                );
                return;
            }
        };
        let mut guard = match file.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        if let Err(error) = writeln!(guard, "{line}") {
            tracing::warn!(
                target: "bcs_bridge_core::trace",
                stream,
                error = %error,
                "failed to append trace record"
            );
        }
    }

    fn raw(&self, context: &TraceContext, line: &str) {
        self.append(
            "raw",
            &self.raw,
            json!({
                "direction": "engine_to_bridge",
                "engine": context.engine,
                "runId": context.run_id,
                "line": line,
                "json": serde_json::from_str::<Value>(line).ok(),
            }),
        );
    }

    fn stderr(&self, context: &TraceContext, line: &str) {
        self.append(
            "stderr",
            &self.stderr,
            json!({
                "direction": "engine_stderr",
                "engine": context.engine,
                "runId": context.run_id,
                "text": strip_ansi(line),
                "rawText": line,
            }),
        );
    }

    fn converted(&self, context: &TraceContext, event: &StreamEvent, seq: u64) {
        self.append(
            "converted",
            &self.converted,
            json!({
                "direction": "bridge_converted",
                "engine": context.engine,
                "runId": context.run_id,
                "seq": seq,
                "event": stream_event_value(event),
            }),
        );
    }

    fn sse(&self, context: &TraceContext, seq: u64, frame: &str) {
        self.append(
            "sse",
            &self.sse,
            json!({
                "direction": "bridge_to_bcs",
                "engine": context.engine,
                "runId": context.run_id,
                "seq": seq,
                "frame": frame,
                "data": sse_data(frame),
            }),
        );
    }
}

impl TraceContext {
    pub fn new(store: Arc<TraceStore>, engine: impl Into<String>, run_id: impl Into<String>) -> Self {
        Self { store, engine: engine.into(), run_id: run_id.into() }
    }

    pub fn record_raw(&self, line: &str) {
        self.store.raw(self, line);
    }

    pub fn record_stderr(&self, line: &str) {
        self.store.stderr(self, line);
    }

    pub fn record_converted(&self, event: &StreamEvent, seq: u64) {
        self.store.converted(self, event, seq);
    }

    pub fn record_sse(&self, seq: u64, frame: &str) {
        self.store.sse(self, seq, frame);
    }
}

fn open_append(path: impl AsRef<Path>) -> io::Result<File> {
    OpenOptions::new().create(true).append(true).open(path)
}

fn stream_event_value(event: &StreamEvent) -> Value {
    match event {
        StreamEvent::Chat(chat) => json!({
            "kind": "chat",
            "state": chat.state,
            "runId": chat.run_id,
            "deltaText": chat.delta_text,
            "stopReason": chat.stop_reason,
            "errorMessage": chat.error_message,
            "errorKind": chat.error_kind,
            "message": chat.message,
            "raw": chat.raw,
        }),
        StreamEvent::Agent(agent) => match &agent.data {
            AgentData::Tool(tool) => json!({
                "kind": "agent",
                "stream": "tool",
                "runId": agent.run_id,
                "data": serde_json::to_value(tool).unwrap_or(Value::Null),
                "raw": agent.raw,
            }),
            AgentData::Thinking(thinking) => json!({
                "kind": "agent",
                "stream": "thinking",
                "runId": agent.run_id,
                "data": serde_json::to_value(thinking).unwrap_or(Value::Null),
                "raw": agent.raw,
            }),
            AgentData::Lifecycle(lifecycle) => json!({
                "kind": "agent",
                "stream": "lifecycle",
                "runId": agent.run_id,
                "data": serde_json::to_value(lifecycle).unwrap_or(Value::Null),
                "raw": agent.raw,
            }),
            AgentData::Approval(_) => json!({
                "kind": "agent",
                "stream": "approval",
                "runId": agent.run_id,
                "raw": agent.raw,
            }),
            AgentData::Phase(_) => json!({
                "kind": "agent",
                "stream": "phase",
                "runId": agent.run_id,
                "raw": agent.raw,
            }),
            AgentData::Assistant { raw } => json!({
                "kind": "agent",
                "stream": "assistant",
                "runId": agent.run_id,
                "raw": raw,
            }),
            AgentData::Error { raw } => json!({
                "kind": "agent",
                "stream": "error",
                "runId": agent.run_id,
                "raw": raw,
            }),
            AgentData::Unknown { stream, raw } => json!({
                "kind": "agent",
                "stream": stream,
                "runId": agent.run_id,
                "raw": raw,
            }),
        },
        StreamEvent::Interaction(interaction) => json!({
            "kind": "interaction",
            "runId": interaction.run_id,
            "phase": interaction.phase,
            "interactionId": interaction.interaction_id,
            "interactionKind": interaction.kind,
            "raw": interaction.raw,
        }),
        StreamEvent::Ping { ts } => json!({ "kind": "ping", "ts": ts }),
        StreamEvent::Unknown { event, raw } => json!({
            "kind": "unknown",
            "event": event,
            "raw": raw,
        }),
    }
}

fn sse_data(frame: &str) -> Value {
    let data = frame
        .lines()
        .filter_map(|line| line.strip_prefix("data: "))
        .collect::<String>();
    serde_json::from_str(&data).unwrap_or(Value::String(data))
}

/// Remove CSI-style ANSI sequences from a child diagnostic line while keeping
/// the original text in the trace record's `rawText` field.
pub fn strip_ansi(input: &str) -> String {
    let mut output = String::with_capacity(input.len());
    let mut chars = input.chars();
    while let Some(ch) = chars.next() {
        if ch != '\u{1b}' {
            output.push(ch);
            continue;
        }
        match chars.next() {
            Some('[') => {
                for next in chars.by_ref() {
                    if ('@'..='~').contains(&next) {
                        break;
                    }
                }
            }
            Some(_) | None => {}
        }
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_ansi_color_sequences() {
        assert_eq!(strip_ansi("\u{1b}[2mINFO\u{1b}[0m hello"), "INFO hello");
    }

    #[test]
    fn preserves_sse_data_as_json() {
        let frame = "event: chat\ndata: {\"state\":\"delta\"}\n\n";
        assert_eq!(sse_data(frame)["state"], "delta");
    }
}
