//! One fixed-column CSV summary per sample, using the existing log writer.
//! Post-commit hooks only update eight bounded Send counters in memory.
use bcs_domain::{message_delivery::PersistedMessageDelivery, DeliveryType};
use bcs_message_flow::delivery_policy::LiveDeliveryPolicy;
use bcs_service_api::application::message_delivery::{
    DeliveryInstrumentation, ManagedMessageDeliveryService,
};
use bcs_service_api::port::repo::message_delivery::DeliveryQueueStatistic;
use std::{sync::{Arc, Mutex}, time::{Duration, Instant}};

struct Interval {
    counts: [u64; 8],
    started: Instant,
}
impl Default for Interval {
    fn default() -> Self { Self { counts: [0; 8], started: Instant::now() } }
}
#[derive(Default)]
pub struct DeliveryMetrics {
    // Shared across sampler/leadership restarts: never replay an emitted interval.
    interval: Mutex<Interval>,
}
impl DeliveryInstrumentation for DeliveryMetrics {
    // Operation timings belong in ordinary dependency observations. This file
    // contains only queue throughput, depth and sampler/writer health.
    fn operation(&self, _operation: &'static str, _seconds: f64, _rows: usize, _success: bool) {}

    fn event(&self, event: &'static str, row: &PersistedMessageDelivery) {
        if row.state.kind != DeliveryType::Send { return; }
        use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
        let mut interval = self.interval.lock().unwrap_or_else(|e| e.into_inner());
        match (event, row.state.status) {
            ("admitted", Status::RejectedCapacity) => interval.counts[6] += 1,
            ("admitted", _) => interval.counts[0] += 1,
            ("started", _) => {
                interval.counts[1] += 1;
                if row.attempt_no > 1 { interval.counts[7] += 1; }
            }
            ("terminal", Status::Completed) => interval.counts[2] += 1,
            ("terminal", Status::Failed) => interval.counts[3] += 1,
            ("terminal", Status::Cancelled) => interval.counts[4] += 1,
            ("terminal", Status::Expired) => interval.counts[5] += 1,
            _ => {}
        }
    }
}

#[derive(Default)]
struct Snapshot {
    // queued, active, pending context, bound context, uncertain, oldest seconds.
    totals: [f64; 6],
    success: bool,
    last_success: Option<i64>,
}
impl Snapshot {
    fn update(&mut self, rows: Option<Vec<DeliveryQueueStatistic>>, now: i64) {
        self.success = rows.is_some();
        let Some(rows) = rows else { return; };
        self.totals = [0.0; 6];
        for row in rows {
            let send = row.kind == "send";
            if send && row.status == "queued" {
                self.totals[0] += row.count as f64;
                self.totals[5] = self.totals[5].max(
                    row.oldest_created_at_ms.map_or(0.0, |t| (now - t).max(0) as f64 / 1000.0),
                );
            }
            if send && ["dispatching", "running", "unknown", "cancelling", "cancel_unknown"].contains(&row.status.as_str()) {
                self.totals[1] += row.count as f64;
            }
            if !send && row.status == "pending_context" { self.totals[2] += row.count as f64; }
            if !send && row.status == "bound" { self.totals[3] += row.count as f64; }
            if send && ["unknown", "cancel_unknown"].contains(&row.status.as_str()) {
                self.totals[4] += row.count as f64;
            }
        }
        self.last_success = Some(now);
    }

    fn render(&self, now: i64, metrics: &DeliveryMetrics, available: bool, paused: bool, dropped: usize, errors: u64) -> String {
        let (counts, interval_ms) = {
            let mut interval = metrics.interval.lock().unwrap_or_else(|e| e.into_inner());
            let finished = Instant::now();
            let elapsed = finished.duration_since(interval.started).as_millis();
            interval.started = finished;
            (std::mem::take(&mut interval.counts), elapsed)
        };
        // CSV v2: each metric has one stable column. No record-type/code rows.
        // Column order and aggregation rules are in message-delivery-service-api.md.
        let mut columns = vec![now.to_string(), interval_ms.to_string()];
        columns.extend(counts.into_iter().map(|v| v.to_string()));
        columns.extend(self.totals.iter().map(|v| v.to_string()));
        columns.extend([
            u8::from(available).to_string(), u8::from(paused).to_string(),
            u8::from(self.success).to_string(),
            self.last_success.map_or(-1, |t| (now - t).max(0)).to_string(),
            dropped.to_string(), errors.to_string(), "2".to_owned(),
        ]);
        columns.join(",")
    }
}

pub async fn run(
    service: Arc<dyn ManagedMessageDeliveryService>,
    policy: Arc<LiveDeliveryPolicy>,
    metrics: Arc<DeliveryMetrics>,
    mut shutdown: tokio::sync::watch::Receiver<bool>,
) {
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
                &metrics,
                policy
                    .scheduler_available
                    .load(std::sync::atomic::Ordering::SeqCst),
                current.policy.pause_dispatch,
                dropped,
                errors,
        );
        tracing::info!(target: crate::logging::DELIVERY_MONITOR_TARGET, "{}", record.trim_end_matches('\n'));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_domain::message_delivery::MessageDeliveryStatus as Status;

    fn row() -> PersistedMessageDelivery {
        serde_json::from_value(serde_json::json!({
            "delivery_id":"private", "env":"test", "source_message_id":"private",
            "target_bot_id":"private", "session_id":"private", "group_id":"private", "source_session_seq":1,
            "flow_kind":"group", "kind":"send", "status":"queued", "state_version":1,
            "may_have_been_sent":false, "available_at_ms":1000, "created_at_ms":1000,
            "updated_at_ms":1000, "attempt_no":0, "semantic_projection_json":{}
        })).unwrap()
    }
    fn columns(snapshot: &Snapshot, metrics: &DeliveryMetrics) -> Vec<String> {
        snapshot.render(10000, metrics, true, false, 0, 0).split(',').map(str::to_owned).collect()
    }

    #[test]
    fn one_csv_row_has_stable_metric_columns_and_no_private_data() {
        let mut snapshot = Snapshot::default();
        snapshot.update(Some(vec![]), 9000);
        let metrics = DeliveryMetrics::default();
        let mut delivery = row();
        metrics.event("admitted", &delivery);
        metrics.event("started", &delivery);
        delivery.state.status = Status::Completed;
        metrics.event("terminal", &delivery);
        let text = snapshot.render(10000, &metrics, true, false, 4, 5);
        assert_eq!(text.lines().count(), 1);
        let fields: Vec<_> = text.split(',').collect();
        assert_eq!(fields.len(), 23);
        assert_eq!(fields[0], "10000");
        assert!(fields[1].parse::<u128>().is_ok());
        assert_eq!(&fields[2..10], &["1", "1", "1", "0", "0", "0", "0", "0"]);
        assert_eq!(&fields[10..16], &["0"; 6]);
        assert_eq!(&fields[16..], &["1", "0", "1", "1000", "4", "5", "2"]);
        assert!(!text.contains("private"));
        assert!(!text.contains("INFO"));
    }

    #[test]
    fn deltas_reset_and_sampler_restart_does_not_replay() {
        let metrics = Arc::new(DeliveryMetrics::default());
        metrics.event("admitted", &row());
        let first = columns(&Snapshot::default(), &metrics);
        assert_eq!(first[2], "1");
        let resumed = metrics.clone();
        assert_eq!(columns(&Snapshot::default(), &resumed)[2], "0");
        resumed.event("admitted", &row());
        assert_eq!(columns(&Snapshot::default(), &resumed)[2], "1");
    }

    #[test]
    fn retry_rejection_and_terminal_counts_exclude_inject() {
        let metrics = DeliveryMetrics::default();
        let mut delivery = row();
        delivery.attempt_no = 2;
        metrics.event("started", &delivery);
        for status in [Status::Failed, Status::Cancelled, Status::Expired] {
            delivery.state.status = status;
            metrics.event("terminal", &delivery);
        }
        delivery.state.status = Status::RejectedCapacity;
        metrics.event("admitted", &delivery);
        metrics.event("terminal", &delivery);
        delivery.state.kind = DeliveryType::Inject;
        for event in ["admitted", "started", "terminal"] { metrics.event(event, &delivery); }
        let fields = columns(&Snapshot::default(), &metrics);
        assert_eq!(&fields[2..10], &["0", "1", "0", "1", "1", "1", "1", "1"]);
        assert_eq!(&columns(&Snapshot::default(), &metrics)[2..10], &["0"; 8]);
    }

    #[test]
    fn failed_snapshot_retains_gauges_but_marks_them_invalid() {
        let mut snapshot = Snapshot::default();
        let metrics = DeliveryMetrics::default();
        snapshot.update(None, 1000);
        assert_eq!(&columns(&snapshot, &metrics)[18..20], &["0", "-1"]);
        snapshot.update(Some(vec![DeliveryQueueStatistic {
            flow_kind: "group".into(), kind: "send".into(), status: "queued".into(),
            wait_reason: "paused".into(), count: 3, oldest_created_at_ms: Some(1000),
        }]), 5000);
        snapshot.update(None, 6000);
        let fields = columns(&snapshot, &metrics);
        assert_eq!(fields[10], "3");
        assert_eq!(fields[15], "4");
        assert_eq!(&fields[18..20], &["0", "5000"]);
        // SQL failure does not invalidate or discard post-commit event counts.
        metrics.event("admitted", &row());
        assert_eq!(columns(&snapshot, &metrics)[2], "1");
        snapshot.update(Some(vec![]), 10000);
        assert_eq!(&columns(&snapshot, &metrics)[10..16], &["0"; 6]);
        assert_eq!(&columns(&snapshot, &metrics)[18..20], &["1", "0"]);
    }

    #[test]
    fn depth_summary_keeps_active_uncertain_and_context_separate() {
        let mut snapshot = Snapshot::default();
        let rows = [("send", "running", 2), ("send", "unknown", 1),
            ("send", "cancel_unknown", 1), ("inject", "pending_context", 3), ("inject", "bound", 4)];
        snapshot.update(Some(rows.into_iter().map(|(kind, status, count)| DeliveryQueueStatistic {
            flow_kind: "group".into(), kind: kind.into(), status: status.into(),
            wait_reason: "none".into(), count, oldest_created_at_ms: None,
        }).collect()), 10000);
        assert_eq!(&columns(&snapshot, &DeliveryMetrics::default())[10..16], &["0", "4", "3", "4", "2", "0"]);
    }
}
