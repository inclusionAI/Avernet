use super::*;
use bcs_protocol::stream::{parse_stream_event, ChatState, InteractionPhase, StreamEvent};

fn parse(frames: &[String]) -> Vec<StreamEvent> {
    frames.iter().map(|frame| {
        let event = frame.lines().find_map(|l| l.strip_prefix("event: ")).unwrap();
        let data = frame.lines().find_map(|l| l.strip_prefix("data: ")).unwrap();
        parse_stream_event(event, serde_json::from_str(data).unwrap())
    }).collect()
}

fn event(value: Value) -> StreamResponse { serde_json::from_value(value).unwrap() }

fn chat_states(events: &[StreamEvent]) -> Vec<String> {
    events.iter().map(|e| match e {
        StreamEvent::Chat(c) => format!("{:?}:{}", c.state, c.delta_text.clone().or_else(|| c.message.as_ref()
            .and_then(|m| m.pointer("/content/0/text")).and_then(Value::as_str).map(str::to_owned))
            .or_else(|| c.error_message.clone()).unwrap_or_default()),
        StreamEvent::Agent(_) => "agent".into(),
        StreamEvent::Interaction(i) => format!("interaction:{:?}:{}", i.phase, i.interaction_id),
        other => format!("{other:?}"),
    }).collect()
}

#[test]
fn bridge_stream_maps_progress_artifacts_and_final() {
    let mut t = Translator::new("run-1");
    let mut frames = Vec::new();
    for value in [
        json!({"task": {"id": "t", "contextId": "c", "status": {"state": "TASK_STATE_SUBMITTED"}}}),
        json!({"statusUpdate": {"taskId": "t", "contextId": "c", "status": {"state": "TASK_STATE_WORKING"}}}),
        json!({"statusUpdate": {"taskId": "t", "contextId": "c", "status": {"state": "TASK_STATE_WORKING"},
            "metadata": {"kind": "commentary.delta", "itemId": "i1", "delta": "Checking"}}}),
        json!({"statusUpdate": {"taskId": "t", "contextId": "c", "status": {"state": "TASK_STATE_WORKING",
            "message": {"messageId": "m", "role": "ROLE_AGENT", "parts": [{"text": "Checking"}]}}, "metadata": {"kind": "commentary"}}}),
        json!({"statusUpdate": {"taskId": "t", "contextId": "c", "status": {"state": "TASK_STATE_WORKING"},
            "metadata": {"kind": "tool", "engine_frame": {"phase": "start", "name": "grep", "toolCallId": "c1"}}}}),
        json!({"artifactUpdate": {"taskId": "t", "contextId": "c", "artifact": {"artifactId": "response", "parts": [{"text": "Fix"}]}}}),
        json!({"artifactUpdate": {"taskId": "t", "contextId": "c", "artifact": {"artifactId": "response", "parts": [{"text": "ed"}]}, "append": true}}),
        json!({"artifactUpdate": {"taskId": "t", "contextId": "c", "artifact": {"artifactId": "response", "parts": [{"text": "Fixed X."}]}, "lastChunk": true}}),
        json!({"statusUpdate": {"taskId": "t", "contextId": "c", "status": {"state": "TASK_STATE_COMPLETED"}}}),
        json!({"task": {"id": "t", "contextId": "c", "status": {"state": "TASK_STATE_COMPLETED"}}}),
    ] { frames.extend(t.translate(&event(value))); }
    let events = parse(&frames);
    assert_eq!(chat_states(&events), ["Delta:Checking", "agent", "Delta:Fix", "Delta:ed", "Delta: X.", "Final:Fixed X."]);
    match &events[1] { StreamEvent::Agent(a) => assert!(matches!(a.data, bcs_protocol::stream::AgentData::Tool(_))), _ => panic!() }
    let seqs: Vec<u64> = events.iter().filter_map(|e| match e { StreamEvent::Chat(c) => c.seq, StreamEvent::Agent(a) => a.seq, _ => None }).collect();
    assert_eq!(seqs, (1..=6).collect::<Vec<_>>());
    assert_eq!(t.task_id.as_deref(), Some("t"));
    assert_eq!(t.context_id.as_deref(), Some("c"));
}

#[test]
fn bridge_interactions_round_trip_through_metadata() {
    let mut t = Translator::new("run-1");
    let frames = t.translate(&event(json!({"statusUpdate": {"taskId": "t", "contextId": "c", "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
        "metadata": {"kind": "interaction.requested", "interactionId": "int-1", "interactionKind": "exec",
            "engine_frame": {"title": "Bash", "command": "ls", "options": [{"decision": "allow-once", "label": "Allow once"}]}}}})));
    let events = parse(&frames);
    let StreamEvent::Interaction(requested) = &events[0] else { panic!("{events:?}") };
    assert_eq!(requested.phase, InteractionPhase::Requested);
    assert_eq!(requested.raw["command"], "ls");
    assert_eq!(requested.raw["kind"], "exec");
    // A snapshot carrying the same pending interaction does not request it twice.
    let snapshot = t.translate(&event(json!({"task": {"id": "t", "status": {"state": "TASK_STATE_INPUT_REQUIRED"},
        "metadata": {"pendingInteractions": [{"interactionId": "int-1", "interactionKind": "exec", "engine_frame": {}}]}}})));
    assert!(snapshot.is_empty());
    let resolved = parse(&t.translate(&event(json!({"task": {"id": "t", "status": {"state": "TASK_STATE_WORKING"}}}))));
    assert!(matches!(&resolved[0], StreamEvent::Interaction(i) if i.phase == InteractionPhase::Resolved && i.interaction_id == "int-1"));
}

#[test]
fn third_party_input_required_becomes_a_free_text_question() {
    let mut t = Translator::new("run-1");
    let frames = t.translate(&event(json!({"statusUpdate": {"taskId": "t", "contextId": "c", "status": {"state": "TASK_STATE_INPUT_REQUIRED",
        "message": {"messageId": "m", "role": "ROLE_AGENT", "parts": [{"text": "Which city?"}]}}}})));
    let events = parse(&frames);
    let StreamEvent::Interaction(question) = &events[0] else { panic!() };
    assert!(question.interaction_id.starts_with(SYNTHETIC_INTERACTION_PREFIX));
    assert_eq!(question.raw["questions"][0]["question"], "Which city?");
    assert_eq!(resolution_parts(&question.interaction_id, &json!({"action": "submit", "answers": {"answer": {"values": ["Paris"]}}})),
        vec![Part::text("Paris")]);
    assert_eq!(resolution_parts(&question.interaction_id, &json!({"action": "submit", "answers": {"answer": {"values": [], "customValues": ["Rome"]}}})),
        vec![Part::text("Rome")]);
    assert_eq!(resolution_parts("int-1", &json!({"decision": "deny"})), vec![Part::data(json!({"decision": "deny"}))]);
    let resumed = parse(&t.translate(&event(json!({"statusUpdate": {"taskId": "t", "contextId": "c", "status": {"state": "TASK_STATE_WORKING"}}}))));
    assert!(matches!(&resumed[0], StreamEvent::Interaction(i) if i.phase == InteractionPhase::Resolved));
}

#[test]
fn message_replies_and_snapshots_complete_runs() {
    let mut t = Translator::new("run-1");
    let events = parse(&t.translate(&event(json!({"message": {"messageId": "m", "role": "ROLE_AGENT", "parts": [{"text": "hi"}]}}))));
    assert_eq!(chat_states(&events), ["Final:hi"]);
    assert!(t.translate(&event(json!({"message": {"messageId": "m2", "role": "ROLE_AGENT", "parts": [{"text": "again"}]}}))).is_empty());

    // Polling: growing artifact text becomes deltas, terminal snapshot is final.
    let mut t = Translator::new("run-2");
    let mut frames = t.translate(&event(json!({"task": {"id": "t", "status": {"state": "TASK_STATE_WORKING"},
        "artifacts": [{"artifactId": "a", "parts": [{"text": "par"}]}]}})));
    frames.extend(t.translate(&event(json!({"task": {"id": "t", "status": {"state": "TASK_STATE_COMPLETED"},
        "artifacts": [{"artifactId": "a", "parts": [{"text": "partial done"}]}]}}))));
    assert_eq!(chat_states(&parse(&frames)), ["Delta:par", "Delta:tial done", "Final:partial done"]);
}

#[test]
fn failures_cancellations_and_errors_are_terminal() {
    let mut t = Translator::new("run-1");
    let events = parse(&t.translate(&event(json!({"statusUpdate": {"taskId": "t", "contextId": "c", "status": {"state": "TASK_STATE_FAILED",
        "message": {"messageId": "m", "role": "ROLE_AGENT", "parts": [{"text": "boom"}]}}, "metadata": {"errorCode": "deadline"}}}))));
    let StreamEvent::Chat(chat) = &events[0] else { panic!() };
    assert_eq!((chat.state, chat.error_message.as_deref(), chat.error_kind.as_deref()), (ChatState::Error, Some("boom"), Some("deadline")));

    let mut t = Translator::new("run-2");
    let events = parse(&t.translate(&event(json!({"task": {"id": "t", "status": {"state": "TASK_STATE_CANCELED"}}}))));
    assert!(matches!(&events[0], StreamEvent::Chat(c) if c.state == ChatState::Aborted));

    let mut t = Translator::new("run-3");
    assert_eq!(chat_states(&parse(&t.fail("bad frame", "a2a_error"))), ["Error:bad frame"]);
    assert!(t.fail("again", "x").is_empty());
}

#[test]
fn card_url_accepts_base_or_direct_card() {
    assert_eq!(card_url("http://h/a2a/p/b"), "http://h/a2a/p/b/.well-known/agent-card.json");
    assert_eq!(card_url("http://h/a2a/p/b/"), "http://h/a2a/p/b/.well-known/agent-card.json");
    assert_eq!(card_url("http://h/card.json"), "http://h/card.json");
}
