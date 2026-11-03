//! Exercise the real Plugin flush loop with a deterministic, locally writable sink.
use super::*;
use std::{pin::Pin, task::{Context as TaskContext, Poll}};
use crate::{engine::EngineRegistry, run::{push_frame, PushOutcome}, run_buffer::MAX_RETAINED_EVENTS, sse};

#[derive(Default)]
struct TestSink {
    frames: Vec<Value>,
    writes: usize,
    fail: bool,
    stall: bool,
}

impl futures::Sink<Message> for TestSink {
    type Error = std::io::Error;
    fn poll_ready(self: Pin<&mut Self>, _: &mut TaskContext<'_>) -> Poll<Result<(), Self::Error>> {
        if self.fail { Poll::Ready(Err(std::io::Error::other("synthetic write failure"))) }
        else { Poll::Ready(Ok(())) }
    }
    fn start_send(mut self: Pin<&mut Self>, item: Message) -> Result<(), Self::Error> {
        let Message::Text(text) = item else { panic!("expected text frame") };
        self.frames.push(serde_json::from_str(&text).unwrap());
        self.writes += 1;
        Ok(())
    }
    fn poll_flush(self: Pin<&mut Self>, _: &mut TaskContext<'_>) -> Poll<Result<(), Self::Error>> {
        if self.stall { Poll::Pending } else { Poll::Ready(Ok(())) }
    }
    fn poll_close(self: Pin<&mut Self>, cx: &mut TaskContext<'_>) -> Poll<Result<(), Self::Error>> {
        self.poll_flush(cx)
    }
}

async fn client() -> (tempfile::TempDir, BotClient) {
    let dir = tempfile::tempdir().unwrap();
    let config = crate::config::ProviderConfig::from_toml(&format!(r#"
provider_id = "test-provider"
mode = "plugin"
state_path = "state.sqlite3"
[plugin]
url = "ws://127.0.0.1:1/ws/bot"
connect_timeout_ms = 10
[[bot]]
provider_bot_ref = "worker"
bot_id = "bot-a"
engine = "claude-code"
engine_bin = "/bin/sh"
cwd = "{}"
"#, dir.path().display()), &dir.path().join("bridge.toml")).unwrap();
    let state = Arc::new(AppState::new(config.clone(), &EngineRegistry::builtin()).unwrap());
    let mut client = BotClient::new(state, config.bots[0].clone(), config.plugin.unwrap(), crate::status::BridgeStatus::default()).await.unwrap();
    client.protocol_v3 = true;
    (dir, client)
}

fn pending(client: &mut BotClient, run_id: &str) -> RunHandle {
    let (handle, _) = client.state.runs.begin_with_mode(run_id, "fingerprint".into(), crate::run::RetentionMode::PluginPending);
    let encoder = PluginEncoder::for_protocol(client.protocol_v3, "test-provider", "worker", "group", "group:session");
    client.routes.insert(run_id.into(), Route {session:"group:session".into(), alias:"alias".into(),
        group:"group".into(), wire_session:"group:session".into(), cursor:0, touched:Instant::now()});
    client.pending.insert(run_id.into(), PendingRun {handle:handle.clone(),encoder,cursor:0,failed_delivery:false});
    handle
}

async fn consumed_stream(count: usize) {
    let (_dir, mut client) = client().await;
    let handle = pending(&mut client, "long-run");
    let mut sink = TestSink::default();
    let mut seq = 0;
    for index in 0..count {
        let event = if index % 3 == 0 { sse::chat_delta("long-run", "text") }
            else { sse::agent_thinking("long-run", Some("thought".into()), None) };
        assert!(matches!(push_frame(&handle, &mut seq, "long-run", &event, None), PushOutcome::Ok),
            "already consumed history must not exhaust retention at event {seq}");
        client.flush_events(&mut sink).await.unwrap();
        assert_eq!(client.pending["long-run"].cursor, seq);
        assert_eq!(client.routes["long-run"].cursor, seq);
        assert_eq!(handle.retention_stats().retained_events, 0);
        assert_eq!(handle.retention_stats().retained_bytes, 0);
        assert_eq!(sink.frames[0]["seq"], seq);
        assert_eq!(sink.frames[0]["seq"], sink.frames[0]["payload"]["seq"]);
        sink.frames.clear(); // The test collector must not itself retain the full stream.
    }
    assert!(matches!(push_frame(&handle, &mut seq, "long-run", &sse::chat_final("long-run", "done".into()), None), PushOutcome::Ok));
    client.state.runs.finish("long-run");
    client.flush_events(&mut sink).await.unwrap();
    client.flush_events(&mut sink).await.unwrap();
    assert!(!client.pending.contains_key("long-run"));
    assert_eq!(sink.writes, count + 1);
    assert_eq!(handle.retention_stats().peak_retained_events, 1);
    assert_eq!(sink.frames[0]["seq"], (count + 1) as u64);
    assert_eq!(sink.frames.last().unwrap()["payload"]["state"], "final");
}

#[tokio::test]
async fn failed_timed_out_and_cancelled_writes_keep_the_pending_record() {
    for failure in ["write", "timeout", "cancelled"] {
        let (_dir, mut client) = client().await;
        let handle = pending(&mut client, "retry-run");
        let mut seq = 0;
        push_frame(&handle, &mut seq, "retry-run", &sse::chat_delta("retry-run", "retry me"), None);
        let original = handle.next_after(0).unwrap().unwrap();
        let stats = handle.retention_stats();
        let mut sink = TestSink {fail:failure == "write", stall:failure != "write", ..Default::default()};
        if failure == "cancelled" {
            // Cancel the enclosing flush while Sink::flush is still pending.
            client.config.connect_timeout_ms = 1000;
            assert!(tokio::time::timeout(Duration::from_millis(5), client.flush_events(&mut sink)).await.is_err());
        } else {
            assert!(matches!(client.flush_events(&mut sink).await, Err(ConnectionFailure::Retry(_))));
        }
        assert_eq!(handle.retention_stats(), stats);
        assert_eq!(client.pending["retry-run"].cursor, 0);
        assert_eq!(client.routes["retry-run"].cursor, 0);
        assert_eq!(handle.next_after(0).unwrap().unwrap().ts, original.ts);
        let mut reconnected = TestSink::default();
        client.flush_events(&mut reconnected).await.unwrap();
        assert_eq!(reconnected.frames[0]["seq"], 1);
        assert_eq!(handle.retention_stats().retained_events, 0);
        assert!(!handle.is_terminal());
        assert!(!handle.abort.is_cancelled());
    }
}

#[tokio::test]
async fn filtered_events_commit_and_protocol_renegotiation_keeps_the_run_cursor() {
    let (_dir, mut client) = client().await;
    client.protocol_v3 = false;
    let handle = pending(&mut client, "versions");
    let mut seq = 0;
    let mut sink = TestSink::default();
    push_frame(&handle, &mut seq, "versions", &bcs_protocol::stream::StreamEvent::Ping {ts:Some(10)}, None);
    client.flush_events(&mut sink).await.unwrap();
    assert_eq!(sink.writes, 0);
    assert_eq!(handle.retention_stats().delivery_cursor, 1);
    push_frame(&handle, &mut seq, "versions", &sse::chat_delta("versions", "v2"), None);
    client.flush_events(&mut sink).await.unwrap();
    assert_eq!(sink.frames[0]["event"], "chat.event");
    assert_eq!(sink.frames[0]["seq"], 1); // V2 per-Bot wire seq excludes filtered pings.
    assert_eq!(handle.retention_stats().delivery_cursor, 2);
    client.protocol_v3 = true;
    client.refresh_run_transport();
    push_frame(&handle, &mut seq, "versions", &sse::chat_delta("versions", "v3"), None);
    client.flush_events(&mut sink).await.unwrap();
    assert_eq!(sink.frames[1]["seq"], 3); // V3 uses the immutable run sequence.
    assert_eq!(sink.frames[1]["payload"]["seq"], 3);
    push_frame(&handle, &mut seq, "versions", &bcs_protocol::stream::StreamEvent::Ping {ts:None}, None);
    client.flush_events(&mut sink).await.unwrap();
    assert_eq!(sink.writes, 2);
    assert_eq!(handle.retention_stats().delivery_cursor, 4);
    assert_eq!(handle.retention_stats().retained_bytes, 0);
}

#[tokio::test]
async fn encoding_failure_is_not_committed_as_original_event_delivery() {
    let (_dir, mut client) = client().await;
    let handle = pending(&mut client, "unsupported");
    let mut seq = 0;
    push_frame(&handle, &mut seq, "unsupported", &bcs_protocol::stream::StreamEvent::Unknown {
        event:"opaque".into(), raw:json!({"x":1})}, None);
    let mut sink = TestSink::default();
    client.flush_events(&mut sink).await.unwrap();
    assert_eq!(sink.frames[0]["payload"]["errorKind"], "encoding_error");
    assert!(client.pending["unsupported"].failed_delivery);
    assert!(handle.abort.is_cancelled());
    assert_eq!(handle.retention_stats().delivery_cursor, 0);
    assert_eq!(handle.retention_stats().retained_events, 1);
    client.state.runs.finish("unsupported");
    client.flush_events(&mut sink).await.unwrap();
    assert_eq!(handle.retention_stats().retained_events, 0);
    assert_eq!(handle.retention_stats().delivery_cursor, 0);
    assert!(!client.pending.contains_key("unsupported"));
}

#[tokio::test]
async fn real_count_backlog_keeps_one_accounted_error_then_drains() {
    let (_dir, mut client) = client().await;
    let handle = pending(&mut client, "offline");
    let mut seq = 0;
    let event = sse::agent_thinking("offline", Some("delta".into()), None);
    for _ in 0..MAX_RETAINED_EVENTS { assert!(matches!(push_frame(&handle, &mut seq, "offline", &event, None), PushOutcome::Ok)); }
    assert!(matches!(push_frame(&handle, &mut seq, "offline", &event, None), PushOutcome::Terminate));
    assert_eq!(handle.retention_stats().retained_events, MAX_RETAINED_EVENTS + 1);
    assert!(matches!(push_frame(&handle, &mut seq, "offline", &event, None), PushOutcome::Terminate));
    assert_eq!(seq, MAX_RETAINED_EVENTS as u64 + 1, "only one reserved error may be appended");
    client.state.runs.finish("offline");
    let mut sink = TestSink::default();
    for expected in 1..=MAX_RETAINED_EVENTS as u64 + 1 {
        client.flush_events(&mut sink).await.unwrap();
        assert_eq!(sink.frames[0]["seq"], expected);
        if expected == MAX_RETAINED_EVENTS as u64 + 1 {
            assert_eq!(sink.frames[0]["payload"]["errorKind"], "buffer_overflow");
            let message = sink.frames[0]["payload"]["errorMessage"].as_str().unwrap();
            for field in ["limit_reason=events", "retention_mode=plugin_pending", "delivery_cursor=0"] {
                assert!(message.contains(field), "missing diagnostic: {field}");
            }
            assert!(message.contains(&format!("last_emitted_seq={MAX_RETAINED_EVENTS}")));
        }
        sink.frames.clear();
    }
    assert_eq!(handle.retention_stats().retained_bytes, 0);
    client.flush_events(&mut sink).await.unwrap();
    assert!(!client.pending.contains_key("offline"));
}

#[tokio::test]
async fn synthetic_incident_shapes_preserve_tools_and_thinking_while_consuming() {
    for (thinking, text, tools) in [(6924, 424, 844), (7830, 316, 46)] {
        let (_dir, mut client) = client().await;
        let handle = pending(&mut client, "incident-shape");
        let other = pending(&mut client, "other-run");
        let mut other_seq = 0;
        push_frame(&other, &mut other_seq, "other-run", &sse::chat_delta("other-run", "independent"), None);
        let mut seq = 0;
        let mut sink = TestSink::default();
        for index in 0..thinking + text + tools + 1 {
            let event = if index < thinking { sse::agent_thinking("incident-shape", Some("increment".into()), None) }
                else if index < thinking + text { sse::chat_delta("incident-shape", "text") }
                else if index < thinking + text + tools {
                    let tool_index = index - thinking - text;
                    let data = if tool_index % 2 == 0 {
                        json!({"phase":"start","name":"synthetic-tool","toolCallId":format!("tool-{}", tool_index/2),"args":{"sample":true}})
                    } else {
                        json!({"phase":"result","toolCallId":format!("tool-{}", tool_index/2),"result":{"content":[{"type":"text","text":"output"}],"extra":17}})
                    };
                    sse::agent_tool("incident-shape", serde_json::from_value(data).unwrap())
                } else { sse::chat_final("incident-shape", "done".into()) };
            assert!(matches!(push_frame(&handle, &mut seq, "incident-shape", &event, None), PushOutcome::Ok));
            let record = handle.next_after(seq - 1).unwrap().unwrap();
            let expected = client.pending["incident-shape"].encoder.encode(&record).unwrap().unwrap();
            client.flush_events(&mut sink).await.unwrap();
            assert_eq!(sink.frames[0], serde_json::to_value(expected).unwrap());
            assert_eq!(handle.retention_stats().retained_events, 0);
            assert_eq!(other.retention_stats().last_emitted_seq, 1);
            assert_eq!(other.retention_stats().delivery_cursor, 1);
            sink.frames.clear();
        }
        assert_eq!(seq, 8193, "a successful final can follow 8192 consumed events");
        assert!(!handle.abort.is_cancelled());
        assert_eq!(handle.retention_stats().peak_retained_events, 1);
    }
}

#[tokio::test]
async fn expired_route_uses_terminal_tombstone_consumption_cursor() {
    let (_dir, mut client) = client().await;
    let request = RequestFrame::new("retry", "chat.send", Some(json!({
        "session_key":"group:session", "bcs_group_id":"group:session", "idempotency_key":"tombstone",
        "message":{"role":"user","content":[{"type":"text","text":"original"}],"timestamp":0}
    })));
    let command = WebSocketV2Encoder::new("test-provider", "worker", "group").decode(request.clone()).unwrap();
    let fp = crate::run::body_fingerprint(&command, command.session_id.as_ref().unwrap());
    let (handle, _) = client.state.runs.begin_with_mode("tombstone", fp, crate::run::RetentionMode::PluginPending);
    let mut seq = 0;
    push_frame(&handle, &mut seq, "tombstone", &sse::chat_final("tombstone", "done".into()), None);
    handle.commit_delivery_through(1).unwrap();
    client.state.runs.finish("tombstone");
    handle.release_delivery();
    assert!(!client.routes.contains_key("tombstone"));
    assert_eq!(client.dispatch(request).await.unwrap()["run_id"], "tombstone");
    assert_eq!(client.pending["tombstone"].cursor, 1);
    let mut sink = TestSink::default();
    client.flush_events(&mut sink).await.unwrap();
    assert_eq!(sink.writes, 0);
    assert!(!client.pending.contains_key("tombstone"));
}

#[tokio::test]
async fn inconsistent_cursors_are_rejected_before_any_network_write() {
    for route_cursor in [0, 1] {
        let (_dir, mut client) = client().await;
        let handle = pending(&mut client, "invalid-cursor");
        let mut seq = 0;
        for _ in 0..2 { push_frame(&handle, &mut seq, "invalid-cursor", &sse::chat_delta("invalid-cursor", "data"), None); }
        client.pending.get_mut("invalid-cursor").unwrap().cursor = 1;
        client.routes.get_mut("invalid-cursor").unwrap().cursor = route_cursor;
        let before = handle.retention_stats();
        let mut sink = TestSink::default();
        assert!(matches!(client.flush_events(&mut sink).await, Err(ConnectionFailure::Fatal(_))));
        assert_eq!(sink.writes, 0);
        assert_eq!(handle.retention_stats(), before);
    }
}

#[tokio::test]
async fn consumed_plugin_stream_can_exceed_twenty_thousand_records() {
    consumed_stream(20_000).await;
}

#[tokio::test]
#[ignore = "explicit long-stream resource stress test"]
async fn consumed_plugin_stream_two_hundred_thousand_records() {
    consumed_stream(200_000).await;
}
