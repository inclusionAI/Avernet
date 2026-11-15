use super::*;

#[path = "../../../../test-support/legacy_log_capture.rs"]
mod legacy;

#[test]
fn coverage_legacy_sse_diagnostics_preserve_frames_and_terminal_errors() {
    legacy::isolated(|| {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let recording = Arc::new(RecordingFlow::default());
            let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
            let mut dedup = SeqDedup::default();
            let mut lag = LagTracker::default();
            for seq in 1..=4 {
                let ts = if seq % 2 == 1 { bcs_protocol::now_ms() - SSE_LAG_ALERT_MS - 1000 } else { bcs_protocol::now_ms() };
                let frame = format!("event: chat\ndata: {}", serde_json::json!({"state":"delta", "seq":seq,"ts":ts,"deltaText":"token"}));
                assert_eq!(drive_sse_frame(&frame, "legacy-lag", "grp-1", "bot-1", &None,
                    u64::MAX, &mut dedup, &mut lag, &flow, None).await, Some(false));
            }
            assert_eq!(recording.snapshot().len(), 4, "diagnostics must preserve every accepted frame");
            assert!(lag.correlated_pair_completed);
            let rows = legacy::rows("bcs_sse_detail");
            let alerts: Vec<_> = rows.iter().filter(|r| r.message.contains("event_code=\"sse.lag_")).collect();
            assert_eq!(alerts.len(), 4, "one alert/recovery per rising/falling edge");
            for (index, row) in alerts.iter().enumerate() {
                if index < 2 { row.assert_field("log_class", "business_key"); }
                else { assert!(!row.message.contains("log_class="), "later pairs remain ordinary"); }
            }

            const FINAL: &str = "event: chat\ndata: {\"state\":\"final\",\"seq\":1,\"message\":{\"role\":\"assistant\",\"content\":[],\"timestamp\":0}}\n\n";
            let read_error = reqwest::Body::wrap_stream(futures::stream::once(async {
                Err::<Vec<u8>, _>(std::io::Error::other("fixture stream interrupted"))
            }));
            for (run, body, reason, state) in [
                ("legacy-final", reqwest::Body::from(FINAL), "terminal", ChatEventState::Final),
                ("legacy-eof", reqwest::Body::from(""), "eof", ChatEventState::Error),
                ("legacy-read-error", read_error, "read_error", ChatEventState::Error),
                ("legacy-oversized", reqwest::Body::from("x".repeat(SSE_MAX_FRAME_BYTES + 1)), "oversized_frame", ChatEventState::Error),
            ] {
                let recording = Arc::new(RecordingFlow::default());
                let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
                let ctx: Arc<dyn BotRunContextPort> = Arc::new(FixedCtx { deadline_ms: u64::MAX, missing: false });
                stream_and_drive(axum::http::Response::new(body).into(), run.into(), "bot-1".into(), flow, ctx, None).await;
                assert_eq!(recording.pairs(), vec![("chat.event".into(), state.clone())], "{run}: exactly one terminal");
                let rows = legacy::rows("bcs_sse_detail");
                let summaries: Vec<_> = rows.iter().filter(|row| row.message.contains(&format!("run_id={run} "))
                    && row.message.contains("event_code=\"sse.closed\"")).collect();
                assert_eq!(summaries.len(), 1, "{run}: one close summary: {rows:?}");
                summaries[0].assert_field("log_class", "business_key");
                summaries[0].assert_field("reason", reason);
                summaries[0].assert_field("saw_terminal", if state == ChatEventState::Final { "true" } else { "false" });
            }
        });
    });
}
