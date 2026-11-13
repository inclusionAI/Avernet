//! Allowlisted Card transport summaries. Never format a request body or raw error.
use std::time::Instant;
use crate::{CardWriteRequest, FuseClientError};

fn bounded(value: &str) -> String {
    value.chars().filter(|c| !c.is_control()).take(128).collect()
}

pub(crate) struct CardWriteObservation {
    id: String,
    method: &'static str,
    started: Instant,
    pub status: Option<u16>,
    pub stage: &'static str,
}

impl CardWriteObservation {
    pub fn start(id: &str, method: &'static str, request: Option<&CardWriteRequest>) -> Self {
        let observation = Self { id: bounded(id), method, started: Instant::now(), status: None, stage: "send" };
        let label = |key: &str| request.and_then(|r| r.labels.get(key))
            .and_then(|values| values.first()).filter(|value| matches!(value.as_str(), "public" | "protected" | "private"))
            .map(String::as_str).unwrap_or("");
        tracing::info!(target: "bcs_observation", event = "fuse.card.write_started",
            resource_id = %observation.id, method, route = "/api/v2/agent-cards/{id}",
            resource_type = request.map(|r| match r.resource_type { crate::CardResourceType::Agent => "agent", crate::CardResourceType::Group => "group" }),
            name = %bounded(request.and_then(|r| r.card.get("name")).and_then(serde_json::Value::as_str).unwrap_or("")),
            skills_count = request.and_then(|r| r.card.get("skills")).and_then(serde_json::Value::as_array).map(Vec::len),
            visibility = label("visibility"), user_visibility = label("user_visibility"),
            request_id = %bcs_observability::CurrentRequestId,
            "Sending Fuse Card write");
        observation
    }

    pub fn succeeded(&self, version: Option<u64>) {
        tracing::info!(target: "bcs_observation", event = "fuse.card.write_succeeded",
            resource_id = %self.id, method = self.method, http_status = self.status,
            duration_ms = self.started.elapsed().as_secs_f64() * 1000.0,
            saved = (self.method == "PUT").then_some(true),
            deleted = (self.method == "DELETE").then_some(true), card_version = version,
            request_id = %bcs_observability::CurrentRequestId,
            "Fuse Card write acknowledged");
    }

    pub fn failed(&self, error: &FuseClientError) {
        let error_kind = match error {
            FuseClientError::HttpRequest(error) if error.is_timeout() => "timeout",
            FuseClientError::HttpRequest(error) if error.is_status() => "http_status",
            FuseClientError::HttpRequest(error) if error.is_decode() => "decode",
            FuseClientError::InvalidResponse(_) => "negative_ack",
            _ => "transport",
        };
        tracing::warn!(target: "bcs_observation", event = "fuse.card.write_failed",
            resource_id = %self.id, method = self.method, http_status = self.status,
            duration_ms = self.started.elapsed().as_secs_f64() * 1000.0,
            stage = self.stage, error_kind,
            request_id = %bcs_observability::CurrentRequestId,
            "Fuse Card write failed");
    }
}
