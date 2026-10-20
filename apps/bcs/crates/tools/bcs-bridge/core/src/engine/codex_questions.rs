//! Codex request_user_input ↔ BCN ask_user. Engine RPC ids stay local; each
//! question form receives a fresh BCN interaction id scoped to its Bridge run.
use std::collections::{HashMap, HashSet};

use bcs_protocol::stream::{InteractionKind, InteractionPhase, StreamEvent};
use futures::{FutureExt, future::BoxFuture, stream::FuturesUnordered};
use serde_json::{Value, json};
use tokio::sync::mpsc::Sender;

use super::{TurnError, TurnRequest, send_line};
use crate::{engine::codex_transport::CodexConnection, interaction::InteractionRegistry, sse};

enum Source { Rpc(Value), Async }

struct Pending {
    source: Source,
    questions: Vec<Value>,
}

pub(super) struct Questions {
    run_id: String,
    registry: InteractionRegistry,
    events: Sender<StreamEvent>,
    pending: HashMap<String, Pending>,
    seen_async: HashSet<String>,
    followup: Vec<Value>,
    pub completion: Option<Value>,
    pub waits: FuturesUnordered<BoxFuture<'static, (String, Value)>>,
}

impl Questions {
    pub fn new(req: &TurnRequest, events: Sender<StreamEvent>) -> Self {
        Self { run_id: req.run_id.clone(), registry: req.interactions.clone(), events,
            pending: HashMap::new(), seen_async: HashSet::new(), followup: Vec::new(), completion: None, waits: FuturesUnordered::new() }
    }

    pub async fn request(&mut self, cli: &mut CodexConnection, value: &Value) -> Result<(), TurnError> {
        if self.pending.values().any(|pending| matches!(&pending.source, Source::Rpc(id) if *id == value["id"])) { return Ok(()); }
        let questions = match map_questions(&value["params"]) {
            Ok(questions) if self.pending.len() < 16 => questions,
            _ => return send_line(cli, &json!({"jsonrpc":"2.0", "id":value["id"],
                "error":{"code":-32000,"message":"Unsupported or invalid user input form"}})).await,
        };
        self.register(Source::Rpc(value["id"].clone()), value["id"].to_string(), questions).await
    }

    async fn register(&mut self, source: Source, native_id: String, questions: Vec<Value>) -> Result<(), TurnError> {
        let (iid, rx) = self.registry.register(&self.run_id, InteractionKind::AskUser, native_id);
        self.pending.insert(iid.clone(), Pending { source, questions: questions.clone() });
        let future_id = iid.clone();
        self.waits.push(async move {
            (future_id, rx.await.unwrap_or_else(|_| json!({"action":"cancel"})))
        }.boxed());
        self.events.send(sse::interaction_event(&self.run_id, InteractionPhase::Requested,
            InteractionKind::AskUser, &iid, json!({"questions":questions}))).await.map_err(|_| TurnError::Aborted)
    }

    pub async fn answer(&mut self, cli: &mut CodexConnection, iid: &str, resolution: Value) -> Result<(), TurnError> {
        let Some(pending) = self.pending.remove(iid) else { return Ok(()); };
        // An empty answer map is Codex's skipped/cancelled response. Never invent
        // a selected option when the user cancels, or forward unknown question ids.
        let answers = map_answers(&pending.questions, &resolution);
        match pending.source {
            Source::Rpc(id) => send_line(cli, &json!({"jsonrpc":"2.0", "id":id, "result":{"answers":answers}})).await?,
            Source::Async if resolution["action"] == "submit" && answers.as_object().is_some_and(|a| !a.is_empty()) => {
                for question in pending.questions {
                    let id = question["questionId"].as_str().unwrap_or_default();
                    self.followup.push(json!({"question":question["question"],"answers":answers[id]["answers"]}));
                }
            }
            Source::Async => {},
        }
        self.events.send(sse::interaction_event(&self.run_id, InteractionPhase::Resolved,
            InteractionKind::AskUser, iid, json!({"action":resolution.get("action").and_then(Value::as_str)
                .filter(|action| *action == "submit").unwrap_or("cancel")})))
            .await.map_err(|_| TurnError::Aborted)
    }

    pub fn engine_resolved(&mut self, rpc_id: &Value) {
        let iid = self.pending.iter().find(|(_, pending)| matches!(&pending.source, Source::Rpc(id) if *id == *rpc_id)).map(|(iid, _)| iid.clone());
        if let Some(iid) = iid {
            self.pending.remove(&iid);
            self.registry.resolve(&iid, "codex-request-closed", json!({"action":"cancel"}));
            self.closed(&iid);
        }
    }

    /// Async questions are AgentMessage metadata, not RPC requests. Codex
    /// already returned {accepted:true} to its tool and can finish the turn.
    /// Hold the BCN run, then deliver the human answer as input on that thread.
    pub async fn observe_item(&mut self, item: &Value) -> Result<(), TurnError> {
        if item["type"] != "agentMessage" || item.get("questions").is_none_or(Value::is_null) { return Ok(()); }
        let id = nonblank(&item["id"]).ok_or_else(|| TurnError::Protocol("async question item lacks id".into()))?;
        if self.seen_async.contains(id) { return Ok(()); }
        let raw = item["questions"].as_array().ok_or_else(|| TurnError::Protocol("invalid async questions".into()))?;
        if raw.is_empty() { return Ok(()); }
        let questions = map_async_questions(raw).map_err(|_| TurnError::Protocol("invalid async question form".into()))?;
        if self.pending.len() >= 16 { return Err(TurnError::Protocol("too many pending question forms".into())); }
        self.seen_async.insert(id.into());
        self.register(Source::Async, format!("async:{id}"), questions).await
    }

    pub async fn observe_completion(&mut self, value: &Value) -> Result<(), TurnError> {
        if let Some(items) = value.pointer("/params/turn/items").and_then(Value::as_array) {
            for item in items { self.observe_item(item).await?; }
        }
        Ok(())
    }

    pub fn has_async_work(&self) -> bool {
        self.pending.values().any(|p| matches!(p.source, Source::Async)) || !self.followup.is_empty()
    }

    pub fn ready_completion(&mut self) -> Option<Value> {
        if self.pending.values().any(|p| matches!(p.source, Source::Async)) { None } else { self.completion.take() }
    }

    pub fn take_followup(&mut self) -> Option<String> {
        if self.followup.is_empty() { return None; }
        Some(format!("用户已回答你刚才提出的问题：\n{}", json!(std::mem::take(&mut self.followup))))
    }

    fn closed(&self, iid: &str) {
        let _ = self.events.try_send(sse::interaction_event(&self.run_id, InteractionPhase::Resolved,
            InteractionKind::AskUser, iid, json!({"action":"cancel"})));
    }
}

impl Drop for Questions {
    fn drop(&mut self) {
        // Covers abort, engine disconnect, terminal completion, and dropped runs.
        self.registry.invalidate_run(&self.run_id, json!({"action":"cancel"}));
        for iid in self.pending.keys() { self.closed(iid); }
    }
}

fn nonblank(value: &Value) -> Option<&str> { value.as_str().filter(|s| !s.trim().is_empty()) }

fn map_questions(params: &Value) -> Result<Vec<Value>, ()> {
    let raw = params["questions"].as_array().filter(|q| !q.is_empty()).ok_or(())?;
    let mut ids = HashSet::new();
    let mut questions = Vec::new();
    for q in raw {
        // Secret answers must never enter chat/event persistence.
        if q.get("isSecret").is_some_and(|v| v != false) || q.get("secret").is_some_and(|v| v != false) { return Err(()); }
        let id = nonblank(&q["id"]).ok_or(())?;
        if !ids.insert(id) { return Err(()); }
        let text = nonblank(&q["question"]).ok_or(())?;
        let mut mapped = json!({"questionId":id, "question":text, "multiSelect":false,
            "allowOther":q.get("isOther").map_or(Ok(false), |v| v.as_bool().ok_or(()))?});
        if let Some(header) = q.get("header") { mapped["header"] = json!(header.as_str().ok_or(())?); }
        if let Some(options) = q.get("options").filter(|v| !v.is_null()) {
            let options = options.as_array().ok_or(())?;
            if !options.is_empty() {
                let mut labels = HashSet::new();
                let mut mapped_options = Vec::new();
                for option in options {
                    let label = nonblank(&option["label"]).ok_or(())?;
                    if !labels.insert(label) { return Err(()); }
                    let mut item = json!({"label":label,"value":label});
                    if let Some(description) = option.get("description") { item["description"] = json!(description.as_str().ok_or(())?); }
                    mapped_options.push(item);
                }
                mapped["options"] = json!(mapped_options);
            }
        }
        questions.push(mapped);
    }
    Ok(questions)
}

fn map_answers(questions: &[Value], resolution: &Value) -> Value {
    if resolution["action"] != "submit" { return json!({}); }
    let Some(answers) = resolution["answers"].as_object() else { return json!({}); };
    let mut mapped = serde_json::Map::new();
    for question in questions {
        let id = question["questionId"].as_str().unwrap_or_default();
        let Some(values) = answers.get(id).and_then(|a| a["values"].as_array()) else { return json!({}); };
        // Empty means explicitly skipped; a single-select form accepts at most
        // selected values, with free text allowed only for text/Other questions.
        if values.iter().any(|v| nonblank(v).is_none()) { return json!({}); }
        if question["allowOther"] != true && let Some(options) = question["options"].as_array() {
            if values.iter().any(|value| !options.iter().any(|option| option["value"] == *value)) { return json!({}); }
        }
        mapped.insert(id.into(), json!({"answers":values}));
    }
    Value::Object(mapped)
}

#[cfg(test)]
#[path = "codex_questions_tests.rs"]
mod tests;

fn map_async_questions(raw: &[Value]) -> Result<Vec<Value>, ()> {
    let mut normalized = Vec::new();
    for (index, question) in raw.iter().enumerate() {
        if question.get("isSecret").is_some_and(|v| v != false) || question.get("secret").is_some_and(|v| v != false) { return Err(()); }
        let mut q = json!({"id":format!("question_{}",index + 1),"question":nonblank(&question["title"]).ok_or(())?,"isOther":true});
        if let Some(options) = question.get("options").filter(|v| !v.is_null()) {
            let options = options.as_array().ok_or(())?;
            let options: Result<Vec<_>, ()> = options.iter().map(|o| Ok(json!({"label":nonblank(o).ok_or(())?}))).collect();
            q["options"] = json!(options?);
        }
        normalized.push(q);
    }
    map_questions(&json!({"questions":normalized}))
}
