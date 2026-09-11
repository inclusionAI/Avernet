//! Fixed-column queue telemetry. Hooks only update bounded in-memory counters;
//! the sampler owns SQL and emits message-only events to the existing logger.
use bcs_domain::{message_delivery::PersistedMessageDelivery, DeliveryType};
use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
use bcs_service_api::application::message_delivery::{
    DeliveryInstrumentation, ManagedMessageDeliveryService,
};
use bcs_service_api::port::repo::message_delivery::DeliveryQueueStatistic;
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
    time::Duration,
};

// Codes are a versioned positional contract documented in message-delivery-service-api.md.
const FLOWS: &[&str] = &["group", "direct_a2a", "task", "system", "state_machine"];
const STATUSES: &[&str] = &[
    "queued",
    "dispatching",
    "running",
    "unknown",
    "cancelling",
    "cancel_unknown",
    "completed",
    "failed",
    "cancelled",
    "expired",
    "rejected_capacity",
    "pending_context",
    "bound",
    "consumed",
    "discarded_context",
];
const REASONS: &[&str] = &[
    "prior_message_running",
    "bot_capacity",
    "rate_limited",
    "bot_offline",
    "retry_backoff",
    "paused",
];
const OPERATIONS: &[&str] = &[
    "lane_blocked",
    "lookup",
    "queued_bots",
    "active_count",
    "queued_heads",
    "expiry_batch",
    "control_batch",
    "recovery_batch",
    "queue_statistics",
    "budget_exhausted",
    "tick",
    "bounded_contexts",
];
fn code(value: &str, choices: &[&str]) -> u8 {
    choices
        .iter()
        .position(|v| *v == value)
        .map_or(0, |i| i as u8 + 1)
}
fn label(value: impl serde::Serialize) -> String {
    serde_json::to_value(value)
        .ok()
        .and_then(|v| v.as_str().map(str::to_owned))
        .unwrap_or_default()
}

// type, flow, kind, status, wait reason, metric/event/operation code.
type Key = [u8; 6];
#[derive(Clone, Default)]
struct Sample {
    value: f64,
    count: u64,
    sum: f64,
    max: f64,
}
impl Sample {
    fn observe(&mut self, seconds: Option<f64>, rows: usize) {
        self.value += rows as f64;
        if let Some(seconds) = seconds {
            self.count += 1;
            self.sum += seconds;
            self.max = self.max.max(seconds);
        }
    }
}
#[derive(Default)]
pub struct DeliveryMetrics {
    counters: Mutex<BTreeMap<Key, Sample>>,
}
impl DeliveryInstrumentation for DeliveryMetrics {
    fn operation(&self, operation: &'static str, seconds: f64, rows: usize, success: bool) {
        let mut counters = self.counters.lock().unwrap_or_else(|e| e.into_inner());
        // status 1/2 means operation success/error only for record type 3.
        counters
            .entry([
                3,
                0,
                0,
                if success { 1 } else { 2 },
                0,
                code(operation, OPERATIONS),
            ])
            .or_default()
            .observe(Some(seconds.max(0.0)), rows);
    }
    fn event(&self, event: &'static str, row: &PersistedMessageDelivery) {
        // Context metrics count first send-start only, not safe retries. Values
        // are cumulative quantities; no body, URL or identity is logged.
        if event == "started" && row.attempt_no == 1 {
            if let Some(selection) = row.context_selection_json.as_ref().and_then(|v| serde_json::from_value::<bcs_domain::message_delivery::DeliveryContextSelection>(v.clone()).ok()) {
                let mut counters = self.counters.lock().unwrap_or_else(|e| e.into_inner());
                let discarded = selection.bound_count.saturating_sub(selection.selected.len() as u64);
                let truncated = discarded > 0 || selection.selected.iter().any(|s| s.body_start > 0);
                for (index, value) in [selection.selected.len() as u64, selection.history_bytes, discarded, u64::from(truncated)].into_iter().enumerate() {
                    counters.entry([5, code(&label(row.flow_kind), FLOWS), 1, 0, 0, index as u8 + 1]).or_default().value += value as f64;
                }
            }
        }
        let event_code = code(event, &["admitted", "started", "terminal"]);
        let seconds = match event {
            "started" => row
                .send_started_at_ms
                .map(|t| (t - row.created_at_ms).max(0) as f64 / 1000.0),
            "terminal" => row
                .send_started_at_ms
                .zip(row.terminal_at_ms)
                .map(|(a, b)| (b - a).max(0) as f64 / 1000.0),
            _ => None,
        };
        let key = [
            2,
            code(&label(row.flow_kind), FLOWS),
            if row.state.kind == DeliveryType::Send {
                1
            } else {
                2
            },
            code(&label(row.state.status), STATUSES),
            0,
            event_code,
        ];
        self.counters
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .entry(key)
            .or_default()
            .observe(seconds, 1);
    }
}

#[derive(Default)]
struct Snapshot {
    depths: BTreeMap<Key, Sample>,
    totals: [f64; 7],
    success: bool,
    last_success: i64,
}
impl Snapshot {
    fn update(&mut self, rows: Option<Vec<DeliveryQueueStatistic>>, now: i64) {
        self.success = rows.is_some();
        let Some(rows) = rows else {
            return;
        };
        // Retain the finite key set and explicitly zero vanished groups.
        for value in self.depths.values_mut() {
            *value = Sample::default();
        }
        self.totals = [0.0; 7];
        for row in rows {
            let send = row.kind == "send";
            let key = [
                1,
                code(&row.flow_kind, FLOWS),
                if send { 1 } else { 2 },
                code(&row.status, STATUSES),
                code(&row.wait_reason, REASONS),
                0,
            ];
            self.depths.entry(key).or_default().value += row.count as f64;
            if send && row.status == "queued" {
                self.totals[0] += row.count as f64;
                self.totals[5] = self.totals[5].max(
                    row.oldest_created_at_ms
                        .map_or(0.0, |t| (now - t).max(0) as f64 / 1000.0),
                );
            }
            if send
                && [
                    "dispatching",
                    "running",
                    "unknown",
                    "cancelling",
                    "cancel_unknown",
                ]
                .contains(&row.status.as_str())
            {
                self.totals[1] += row.count as f64;
            }
            if row.status == "pending_context" {
                self.totals[2] += row.count as f64;
            }
            if row.status == "bound" {
                self.totals[3] += row.count as f64;
            }
            if send && ["unknown", "cancel_unknown"].contains(&row.status.as_str()) {
                self.totals[4] += row.count as f64;
            }
        }
        self.totals[6] = self.totals[4];
        self.last_success = now;
    }
    fn line(&self, now: i64, boot: &str, key: Key, sample: &Sample) -> String {
        format!(
            "1,{now},{boot},{},{},{},{},{},{},{},{},{},{},{},{}\n",
            key[0],
            key[1],
            key[2],
            key[3],
            key[4],
            key[5],
            sample.value,
            sample.count,
            sample.sum,
            sample.max,
            u8::from(self.success),
            self.last_success
        )
    }
    fn render(
        &self,
        now: i64,
        boot: &str,
        metrics: &DeliveryMetrics,
        available: bool,
        paused: bool,
        version: u64,
        cache: [u64; 6],
        dropped: usize,
        errors: u64,
    ) -> String {
        let mut text = String::new();
        for (i, value) in self
            .totals
            .iter()
            .copied()
            .chain([
                f64::from(available),
                f64::from(paused),
                version as f64,
                dropped as f64,
                errors as f64,
            ])
            .enumerate()
        {
            text.push_str(&self.line(
                now,
                boot,
                [0, 0, 0, 0, 0, i as u8 + 1],
                &Sample {
                    value,
                    ..Default::default()
                },
            ));
        }
        for (key, sample) in &self.depths {
            text.push_str(&self.line(now, boot, *key, sample));
        }
        let counters = metrics
            .counters
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        for (key, sample) in counters {
            text.push_str(&self.line(now, boot, key, &sample));
        }
        for (i, value) in cache.into_iter().enumerate() {
            text.push_str(&self.line(
                now,
                boot,
                [4, 0, 0, 0, 0, i as u8 + 1],
                &Sample {
                    value: value as f64,
                    ..Default::default()
                },
            ));
        }
        text
    }
}

pub async fn run(
    service: Arc<dyn ManagedMessageDeliveryService>,
    policy: Arc<LiveDeliveryPolicy>,
    metrics: Arc<DeliveryMetrics>,
    mut shutdown: tokio::sync::watch::Receiver<bool>,
) {
    let boot = uuid::Uuid::new_v4().simple().to_string();
    let mut ticker = tokio::time::interval(Duration::from_secs(10));
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut snapshot = Snapshot::default();
    loop {
        tokio::select! { _ = shutdown.changed() => break, _ = ticker.tick() => {} }
        if *shutdown.borrow() {
            break;
        }
        let current = policy.snapshot.read().await.clone();
        let result = tokio::select! {
            _ = shutdown.changed() => break,
            result = tokio::time::timeout(Duration::from_secs(5), service.queue_statistics()) => result,
        };
        let rows = match result {
            Ok(Ok(rows)) => Some(rows),
            Ok(Err(error)) => {
                tracing::warn!(%error, "delivery monitor queue snapshot failed; retaining last successful values");
                None
            }
            Err(error) => {
                tracing::warn!(%error, "delivery monitor queue snapshot timed out; retaining last successful values");
                None
            }
        };
        let now = chrono::Utc::now().timestamp_millis();
        snapshot.update(rows, now);
        let (dropped, errors) = crate::logging::delivery_monitor_statistics();
        let record = snapshot.render(
                now,
                &boot,
                &metrics,
                policy
                    .scheduler_available
                    .load(std::sync::atomic::Ordering::SeqCst),
                current.policy.pause_dispatch,
                current.version,
                bcs_bot_store::provider_cache::statistics(),
                dropped,
                errors,
        );
        tracing::info!(target: crate::logging::DELIVERY_MONITOR_TARGET, "{}", record.trim_end_matches('\n'));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn snapshot_failure_retains_data_and_successful_empty_clears_it() {
        let mut snapshot = Snapshot::default();
        snapshot.update(None, 1);
        assert!(!snapshot.success);
        assert_eq!(snapshot.last_success, 0);
        snapshot.update(
            Some(vec![DeliveryQueueStatistic {
                flow_kind: "group".into(),
                kind: "send".into(),
                status: "queued".into(),
                wait_reason: "paused".into(),
                count: 3,
                oldest_created_at_ms: Some(1000),
            }]),
            5000,
        );
        snapshot.update(None, 6000);
        assert_eq!(snapshot.totals[0], 3.0);
        assert_eq!(snapshot.totals[5], 4.0);
        assert_eq!(snapshot.last_success, 5000);
        assert!(!snapshot.success);
        snapshot.update(Some(vec![]), 7000);
        assert_eq!(snapshot.totals[0], 0.0);
        assert!(snapshot.depths.values().all(|s| s.value == 0.0));
        assert!(snapshot.success);
    }
    #[test]
    fn monitor_has_fixed_columns_and_no_header_or_tracing_prefix() {
        let snapshot = Snapshot::default();
        let metrics = DeliveryMetrics::default();
        metrics.operation("tick", 0.5, 3, true);
        metrics.operation("tick", 0.2, 2, true);
        let records = snapshot.render(1000, "abc", &metrics, true, false, 7, [0; 6], 0, 0);
        assert_eq!(
            records.lines().next().unwrap(),
            "1,1000,abc,0,0,0,0,0,1,0,0,0,0,0,0"
        );
        assert!(records.lines().all(|line| line.split(',').count() == 15));
        assert!(records.contains("1,1000,abc,3,0,0,1,0,11,5,2,0.7,0.5,0,0\n"));
        assert!(!records.contains("tick"));
        assert!(!records.contains("INFO"));
    }
    #[test]
    fn event_counts_and_duration_samples_have_distinct_denominators() {
        let mut row: PersistedMessageDelivery = serde_json::from_value(serde_json::json!({
            "delivery_id":"not-logged", "env":"test", "source_message_id":"not-logged",
            "target_bot_id":"not-logged", "session_id":"not-logged", "group_id":"not-logged",
            "source_session_seq":1, "flow_kind":"group", "kind":"send", "status":"queued",
            "state_version":1, "may_have_been_sent":false, "available_at_ms":1000,
            "created_at_ms":1000, "updated_at_ms":1000, "attempt_no":0, "semantic_projection_json":{}
        })).unwrap();
        let metrics = DeliveryMetrics::default();
        metrics.event("admitted", &row);
        row.send_started_at_ms = Some(3000);
        row.state.status = bcs_domain::message_delivery::MessageDeliveryStatus::Dispatching;
        metrics.event("started", &row);
        row.terminal_at_ms = Some(8000);
        row.state.status = bcs_domain::message_delivery::MessageDeliveryStatus::Completed;
        metrics.event("terminal", &row);
        let data = metrics.counters.lock().unwrap();
        assert_eq!(data[&[2, 1, 1, 1, 0, 1]].value, 1.0);
        assert_eq!(data[&[2, 1, 1, 1, 0, 1]].count, 0);
        assert_eq!(data[&[2, 1, 1, 2, 0, 2]].sum, 2.0);
        assert_eq!(data[&[2, 1, 1, 7, 0, 3]].sum, 5.0);
    }

    #[test]
    fn context_monitor_uses_existing_columns_and_counts_first_attempt_only() {
        let mut row: PersistedMessageDelivery = serde_json::from_value(serde_json::json!({
            "delivery_id":"private", "env":"test", "source_message_id":"private",
            "target_bot_id":"private", "session_id":"private", "group_id":"private",
            "source_session_seq":1, "flow_kind":"group", "kind":"send", "status":"dispatching",
            "state_version":2, "may_have_been_sent":true, "available_at_ms":1000,
            "created_at_ms":1000, "updated_at_ms":1000, "attempt_no":1, "semantic_projection_json":{},
            "context_selection_json":{"version":1,"max_messages":24,"max_bytes":131072,"bound_count":3,"history_bytes":512,
                "selected":[{"delivery_id":"private-context","state_version":2,"body_start":10}]}
        })).unwrap();
        let metrics = DeliveryMetrics::default();
        metrics.event("started", &row);
        row.attempt_no = 2;
        metrics.event("started", &row);
        let data = metrics.counters.lock().unwrap();
        for (index, expected) in [1.0, 512.0, 2.0, 1.0].into_iter().enumerate() {
            assert_eq!(data[&[5,1,1,0,0,index as u8 + 1]].value, expected);
        }
        drop(data);
        let text = Snapshot::default().render(1000, "boot", &metrics, true, false, 1, [0;6], 0, 0);
        assert!(text.lines().all(|l| l.split(',').count() == 15));
        assert!(!text.contains("private"));
        assert!(text.contains(",5,1,1,0,0,2,512,0,0,0,"));
    }
}
