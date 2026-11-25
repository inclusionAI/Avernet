//! Ordered native Stop/result/idle evidence for Claude Code >= 2.1.199.
//! One instance belongs to one spawned process and one BCS run. This never reads
//! transcripts or counts notification delivery. Missing evidence is not empty.
use std::collections::HashSet;
use serde_json::{json, Value};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(super) enum CompletionMode {
    Legacy,
    Observe,
    #[default]
    HookIdle,
}
impl CompletionMode {
    pub(super) fn parse(options: &toml::Table) -> Result<Self, String> {
        for key in options.keys() {
            if key != "completion_mode" { return Err(format!("unsupported engine_options key '{key}'")); }
        }
        match options.get("completion_mode") {
            None => Ok(Self::default()),
            Some(toml::Value::String(s)) => match s.as_str() {
                "legacy" => Ok(Self::Legacy), "observe" => Ok(Self::Observe), "hook_idle" => Ok(Self::HookIdle),
                _ => Err("completion_mode must be legacy, observe or hook_idle".into()),
            },
            _ => Err("completion_mode must be a string".into()),
        }
    }
}

pub(super) const UNAVAILABLE: &str = "completion_evidence_unavailable";
pub(super) const CONFLICT: &str = "completion_evidence_conflict";
pub(super) const CAPABILITY: &str = "completion_capability_unavailable";
const MAX_RESULT_BYTES: usize = 1024 * 1024;
const MAX_CALLBACKS: usize = 2048;
const MAX_TASKS: usize = 512;

/// No task descriptions, answers, tool arguments or environment enter diagnostics.
pub(super) fn trace_line(line: &str) -> String {
    let Ok(v) = serde_json::from_str::<Value>(line) else { return json!({"type":"malformed", "reason":"completion_control_redacted"}).to_string(); };
    if v["type"] == "control_response" || (v["type"] == "control_request" && v["request"]["subtype"] == "hook_callback") {
        return json!({"type":v["type"], "subtype":"completion_control_redacted"}).to_string();
    }
    line.to_owned()
}

pub(super) struct Completion {
    pub(super) initialize_id: String,
    pub(super) callback_id: String,
    session: Option<String>,
    epoch: u64,
    snapshot: Option<usize>,
    candidate: Option<String>,
    seen: HashSet<String>,
    seen_frames: HashSet<String>,
    terminal: bool,
}

impl Completion {
    pub(super) fn new() -> Self {
        Self { initialize_id: uuid::Uuid::new_v4().to_string(), callback_id: uuid::Uuid::new_v4().to_string(),
            session: None, epoch: 0, snapshot: None, candidate: None, seen: HashSet::new(), seen_frames: HashSet::new(), terminal: false }
    }
    pub(super) fn initialize(&self) -> Value {
        json!({"type":"control_request","request_id":self.initialize_id,
            "request":{"subtype":"initialize","hooks":{"Stop":[{"hookCallbackIds":[self.callback_id],"timeout":5}]}}})
    }
    pub(super) fn initialized(&self, v: &Value) -> Result<bool, &'static str> {
        if v["type"] != "control_response" { return Ok(false); }
        if v["response"]["request_id"] != self.initialize_id || v["response"]["subtype"] != "success" {
            return Err(CAPABILITY);
        }
        Ok(true)
    }
    pub(super) fn callback(&mut self, v: &Value) -> Result<Option<Value>, &'static str> {
        if v["type"] != "control_request" || v["request"]["subtype"] != "hook_callback" { return Ok(None); }
        if self.terminal || self.epoch == 0 || self.candidate.is_some() { return Err(CONFLICT); }
        let q = &v["request"];
        let x = &q["input"];
        let rid = identifier(&v["request_id"])?;
        if q["callback_id"] != self.callback_id || x["hook_event_name"] != "Stop"
            || x["session_id"].as_str() != self.session.as_deref() || !x["agent_id"].is_null() {
            return Err(CONFLICT);
        }
        identifier(&x["prompt_id"])?;
        if !x["stop_hook_active"].is_boolean() { return Err(UNAVAILABLE); }
        if let Some(crons) = x.get("session_crons") {
            if !crons.as_array().is_some_and(|a| a.is_empty()) { return Err(CAPABILITY); }
        }
        let tasks = x["background_tasks"].as_array().ok_or(UNAVAILABLE)?;
        if tasks.len() > MAX_TASKS || self.seen.len() >= MAX_CALLBACKS { return Err(UNAVAILABLE); }
        for task in tasks {
            identifier(&task["id"])?;
            if !matches!(task["type"].as_str(), Some("subagent" | "shell"))
                || task["status"] != "running" { return Err(CAPABILITY); }
        }
        if self.snapshot.is_some() || !self.seen.insert(rid.to_owned()) { return Err(CONFLICT); }
        self.snapshot = Some(tasks.len());
        Ok(Some(json!({"type":"control_response","response":{"subtype":"success","request_id":rid,"response":{}}})))
    }
    /// Called in stdout order, after a callback has been acknowledged. Only a
    /// newly received idle can release a candidate; later notifications cannot.
    pub(super) fn event(&mut self, v: &Value) -> Result<Option<String>, &'static str> {
        if self.terminal { return Ok(None); }
        let ty = v["type"].as_str().unwrap_or("");
        let sub = v["subtype"].as_str().unwrap_or("");
        if ty == "control_request" || ty == "control_response" { return Ok(None); }
        if !v["parent_tool_use_id"].is_null() { return Ok(None); }
        if ty == "result" || (ty == "system" && matches!(sub, "init" | "session_state_changed")) {
            let id = identifier(&v["uuid"])?;
            if self.seen_frames.len() >= 8192 { return Err(UNAVAILABLE); }
            if !self.seen_frames.insert(id.to_owned()) { return Err(CONFLICT); }
        }
        if ty == "system" && sub == "init" {
            let version = v["claude_code_version"].as_str()
                .and_then(|version| semver::Version::parse(version).ok()).ok_or(CAPABILITY)?;
            if version < semver::Version::new(2, 1, 199) { return Err(CAPABILITY); }
            let sid = identifier(&v["session_id"])?;
            if self.session.as_ref().is_some_and(|s| s != sid) { return Err(CONFLICT); }
            self.session = Some(sid.to_owned());
            self.epoch += 1;
            self.invalidate();
            return Ok(None);
        }
        if ty == "result" || (ty == "system" && sub == "session_state_changed") {
            // The initial running event precedes init on this profile.
            if self.epoch == 0 && v["state"] == "running" { return Ok(None); }
            if self.epoch == 0 || v["session_id"].as_str() != self.session.as_deref() { return Err(CONFLICT); }
        }
        if ty == "result" {
            if v["subtype"] != "success" || v["is_error"] == true { self.invalidate(); return Ok(None); }
            if self.candidate.is_some() { return Err(CONFLICT); }
            let text = v["result"].as_str().ok_or(UNAVAILABLE)?;
            if text.len() > MAX_RESULT_BYTES { return Err(UNAVAILABLE); }
            self.candidate = Some(text.to_owned());
            tracing::debug!(target:"bcs_bridge_core::engine", epoch=self.epoch, snapshot=?self.snapshot, "candidate_deferred");
        } else if ty == "system" && sub == "session_state_changed" {
            match v["state"].as_str() {
                Some("running") => self.invalidate(),
                Some("idle") => {
                    let tasks = self.snapshot.ok_or(UNAVAILABLE)?;
                    let result = self.candidate.take().ok_or(UNAVAILABLE)?;
                    self.snapshot = None;
                    if tasks == 0 {
                        self.terminal = true;
                        tracing::debug!(target:"bcs_bridge_core::engine", epoch=self.epoch, "completion_evidence_matched");
                        return Ok(Some(result));
                    }
                }
                _ => return Err(UNAVAILABLE),
            }
        } else if ty == "system" && sub == "task_started" {
            if !matches!(v["task_type"].as_str(), Some("local_agent" | "local_bash")) { return Err(CAPABILITY); }
            self.invalidate();
        } else if matches!(ty, "assistant" | "stream_event" | "user")
            || (ty == "system" && matches!(sub, "task_notification" | "task_updated")) {
            // Includes another Hook blocking Stop and continuing the same prompt.
            // Ordinary background activity invalidates a stale empty snapshot;
            // it never establishes a new main epoch or releases a result.
            self.invalidate();
        }
        Ok(None)
    }
    pub(super) fn awaiting_idle(&self) -> bool { self.candidate.is_some() }
    fn invalidate(&mut self) { self.snapshot = None; self.candidate = None; }
}
fn identifier(v: &Value) -> Result<&str, &'static str> {
    v.as_str().filter(|s| !s.trim().is_empty() && s.len() <= 256).ok_or(UNAVAILABLE)
}
