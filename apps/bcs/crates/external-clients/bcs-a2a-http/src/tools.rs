//! Negotiated observation records. Remote data never supplies routing authority.
use std::collections::HashMap;
use serde_json::{json, Value};

pub const EXTENSION: &str = "https://github.com/inclusionAI/Avernet/blob/dev/docs/specs/a2a/extensions/tools/v1.md";

#[derive(Clone, Debug, Default)]
pub struct Tools {
    enabled: bool,
    records: HashMap<(String, String), Value>,
}

impl Tools {
    pub fn new(enabled: bool) -> Self { Self { enabled, ..Self::default() } }

    pub fn decode(&mut self, message: &a2a::Message) -> Result<Vec<Value>, &'static str> {
        let raw = serde_json::to_value(message).map_err(|_| "invalid tool message")?;
        let Some(marker) = raw.get("metadata").and_then(|m|m.get(EXTENSION)) else { return Ok(vec![]); };
        if !self.enabled || marker != &json!({"version":"1"}) { return Err("unnegotiated tool extension or version"); }
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
                    let args = record.get("args").filter(|v|v.is_object()).ok_or("tool args must be object")?;
                    output["state"] = json!("tool_call_start");
                    data["args"] = args.clone();
                }
                "result" => {
                    let error = record.get("isError").and_then(Value::as_bool).ok_or("missing tool result status")?;
                    let result = record.get("result").ok_or("missing tool result")?;
                    for content in result.get("content").and_then(Value::as_array).ok_or("missing result content")? {
                        if content.get("type").and_then(Value::as_str) != Some("text")
                            || content.get("text").and_then(Value::as_str).is_none() { return Err("unsupported tool result part"); }
                    }
                    output["state"] = json!("tool_call_end");
                    data["result"] = result.clone(); data["isError"] = json!(error);
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
