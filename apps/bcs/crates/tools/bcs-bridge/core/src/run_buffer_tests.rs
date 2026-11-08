use super::*;
use crate::sse;
use serde_json::json;

fn append(buffer: &mut RunEventBuffer, seq: u64, bytes: usize) {
    buffer.append(RunEvent {run_id:"buffer-test".into(), seq, ts:42,
        event:sse::chat_delta("buffer-test", "payload")}, bytes, false);
}

#[test]
fn consuming_prefix_updates_exact_accounting_and_preserves_sequence() {
    let mut buffer = RunEventBuffer::default();
    for seq in 1..=20_000 {
        append(&mut buffer, seq, seq as usize + 100);
        let event = buffer.next_after(seq - 1).unwrap().unwrap();
        assert_eq!(event.seq, seq);
        assert_eq!(event.ts, 42);
        buffer.commit_delivery_through(seq).unwrap();
        let stats = buffer.stats();
        assert_eq!((stats.retained_events, stats.retained_bytes), (0, 0));
        assert_eq!((stats.delivery_cursor, stats.last_emitted_seq), (seq, seq));
        assert!(buffer.next_after(seq).unwrap().is_none());
    }
    assert_eq!(buffer.stats().peak_retained_events, 1);
}

#[test]
fn commits_are_idempotent_and_reject_gaps_without_partial_mutation() {
    let mut buffer = RunEventBuffer::default();
    append(&mut buffer, 1, 101);
    append(&mut buffer, 2, 202);
    let initial = buffer.stats();
    for cursor in [2, 3, u64::MAX] {
        assert!(matches!(buffer.commit_delivery_through(cursor), Err(DeliveryCursorError::NonContiguous {..})));
        assert_eq!(buffer.stats(), initial);
    }
    buffer.commit_delivery_through(1).unwrap();
    let consumed = buffer.stats();
    for cursor in [0, 1] { buffer.commit_delivery_through(cursor).unwrap(); }
    assert_eq!(buffer.stats(), consumed);
    assert_eq!(consumed.retained_bytes, 202);
    assert!(matches!(buffer.next_after(0), Err(DeliveryCursorError::Reclaimed {..})));
    assert!(matches!(buffer.next_after(3), Err(DeliveryCursorError::Ahead {..})));
    buffer.commit_delivery_through(2).unwrap();
    assert_eq!(buffer.stats().retained_bytes, 0);
}

#[test]
fn failed_accounting_does_not_pop_or_advance_cursor() {
    let mut buffer = RunEventBuffer::default();
    append(&mut buffer, 1, 100);
    buffer.stats.retained_bytes = 99;
    assert_eq!(buffer.commit_delivery_through(1), Err(DeliveryCursorError::Accounting));
    assert_eq!(buffer.stats().delivery_cursor, 0);
    assert_eq!(buffer.records.len(), 1);
}

#[test]
fn budget_counts_current_records_not_historical_sequences() {
    let mut buffer = RunEventBuffer::default();
    append(&mut buffer, 1, MAX_RETAINED_BYTES);
    assert_eq!(buffer.limit_reason(1), Some("bytes"));
    buffer.commit_delivery_through(1).unwrap();
    assert_eq!(buffer.limit_reason(1), None);
    for seq in 2..=MAX_RETAINED_EVENTS as u64 + 1 { append(&mut buffer, seq, 1); }
    assert_eq!(buffer.limit_reason(1), Some("events"));
    assert_eq!(buffer.limit_reason(MAX_RETAINED_BYTES), Some("both"));
    buffer.commit_delivery_through(2).unwrap();
    assert_eq!(buffer.limit_reason(1), None);
}

#[test]
fn terminal_reserve_is_accounted_and_can_be_consumed_after_full_backlog() {
    let mut buffer = RunEventBuffer::default();
    append(&mut buffer, 1, MAX_RETAINED_BYTES);
    buffer.append(RunEvent {run_id:"buffer-test".into(),seq:2,ts:43,
        event:sse::chat_error("buffer-test", "budget exhausted", Some("buffer_overflow"))}, 700, true);
    assert!(buffer.failure_retained());
    buffer.commit_delivery_through(1).unwrap();
    assert_eq!(buffer.stats().retained_bytes, 700);
    buffer.commit_delivery_through(2).unwrap();
    assert_eq!(buffer.stats().retained_bytes, 0);
}

#[test]
fn disposal_does_not_claim_unsent_events_were_consumed() {
    let mut buffer = RunEventBuffer::default();
    append(&mut buffer, 1, 100);
    append(&mut buffer, 2, 200);
    buffer.commit_delivery_through(1).unwrap();
    buffer.clear();
    buffer.clear();
    assert_eq!(buffer.stats().delivery_cursor, 1);
    assert_eq!(buffer.stats().last_emitted_seq, 2);
    assert_eq!(buffer.stats().retained_bytes, 0);
    assert_eq!(buffer.records.capacity(), 0);
}

#[test]
fn thinking_and_raw_data_are_still_in_the_byte_budget() {
    let small = sse::agent_thinking("test", Some("小".into()), None);
    let large = sse::agent_thinking("test", Some("小".repeat(1000)), None);
    assert!(retained_event_size(&large).unwrap() > retained_event_size(&small).unwrap() + 2000);
    let mut raw = small.clone();
    if let StreamEvent::Agent(agent) = &mut raw { agent.raw = json!({"payload":"x".repeat(4096)}); }
    assert!(retained_event_size(&raw).unwrap() > retained_event_size(&small).unwrap() + 4096);
}
