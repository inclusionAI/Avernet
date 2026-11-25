//! Negotiated observation records. Remote data never supplies routing authority.
use std::collections::HashMap;
use serde_json::{json, Value};

pub const EXTENSION: &str = "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/tools/v1.md";
pub const EXTENSION_V2: &str = "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/tools/v2.md";

pub fn is_extension(uri: &str) -> bool { matches!(uri, EXTENSION | EXTENSION_V2) }

#[derive(Clone, Debug, Default)]
pub struct Tools {
    v1_enabled: bool,
    v2_enabled: bool,
    records: HashMap<(String, String), Value>,
}

impl Tools {
    pub fn new(enabled: bool) -> Self { Self::with_versions(enabled, false) }
    pub fn with_versions(v1_enabled: bool, v2_enabled: bool) -> Self { Self { v1_enabled, v2_enabled, ..Self::default() } }

    pub fn decode(&mut self, message: &a2a::Message) -> Result<Vec<Value>, &'static str> {
        let raw = serde_json::to_value(message).map_err(|_| "invalid tool message")?;
        let v1_marker = raw.get("metadata").and_then(|m|m.get(EXTENSION));
        let has_v2 = message.extensions.as_ref().is_some_and(|exts|exts.iter().any(|uri|uri == EXTENSION_V2));
        if v1_marker.is_some() && has_v2 { return Err("ambiguous tool extension version"); }
        let is_v2 = match (v1_marker, has_v2) {
            (Some(marker), false) => {
                if !self.v1_enabled || marker != &json!({"version":"1"}) { return Err("unnegotiated tool extension or version"); }
                false
            }
            (None, true) => {
                if !self.v2_enabled { return Err("unnegotiated tool extension or version"); }
                true
            }
            (None, false) => return Ok(vec![]),
            (Some(_), true) => unreachable!(),
        };
        if message.role != a2a::Role::Agent { return Err("tool observation must be from Agent"); }
        let mut outputs = Vec::new();
        for part in raw.get("parts").and_then(Value::as_array).ok_or("missing tool parts")? {
            let record = part.get("data").ok_or("tool record requires DataPart")?;
            let string = |key: &str| record.get(key).and_then(Value::as_str).filter(|v|!v.trim().is_empty()).ok_or("missing tool field");
            let id = string("toolCallId")?;
            let name = string("name")?;
            let phase = string("phase")?;
            let mut output = json!({});
            let mut data = json!({"phase":phase,"toolCallId":id,"name":name});
            match phase {
                "start" => {
                    output["state"] = json!("tool_call_start");
                    if is_v2 {
                        if let Some(args) = record.get("args") {
                            if !args.is_object() { return Err("tool args must be object"); }
                            data["args"] = args.clone();
                        }
                    } else {
                        let args = record.get("args").filter(|v|v.is_object()).ok_or("tool args must be object")?;
                        data["args"] = args.clone();
                    }
                }
                "result" => {
                    output["state"] = json!("tool_call_end");
                    if is_v2 {
                        if let Some(error) = record.get("isError") {
                            let error = error.as_bool().ok_or("missing tool result status")?;
                            data["isError"] = json!(error);
                        }
                        if let Some(result) = record.get("result") {
                            for content in result.get("content").and_then(Value::as_array).ok_or("missing result content")? {
                                if content.get("type").and_then(Value::as_str) != Some("text")
                                    || content.get("text").and_then(Value::as_str).is_none() { return Err("unsupported tool result part"); }
                            }
                            data["result"] = result.clone();
                        }
                    } else {
                        let error = record.get("isError").and_then(Value::as_bool).ok_or("missing tool result status")?;
                        let result = record.get("result").ok_or("missing tool result")?;
                        for content in result.get("content").and_then(Value::as_array).ok_or("missing result content")? {
                            if content.get("type").and_then(Value::as_str) != Some("text")
                                || content.get("text").and_then(Value::as_str).is_none() { return Err("unsupported tool result part"); }
                        }
                        data["result"] = result.clone(); data["isError"] = json!(error);
                    }
                }
                _ => return Err("unknown tool phase"),
            }
            output["stream"] = json!("tool");
            output["data"] = data;
            if self.records.iter().any(|((other_id, _), previous)| other_id == id && previous.get("name") != record.get("name")) {
                return Err("tool name changed");
            }
            let key = (id.to_owned(), phase.to_owned());
            match self.records.get(&key) {
                Some(previous) if previous == record => {}
                Some(_) => return Err("conflicting tool record"),
                None => { self.records.insert(key, record.clone()); outputs.push(output); }
            }
        }
        Ok(outputs)
    }
}
