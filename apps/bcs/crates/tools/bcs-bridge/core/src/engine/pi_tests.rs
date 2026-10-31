use super::*;

fn table(pairs: &[(&str, toml::Value)]) -> toml::Table {
    pairs
        .iter()
        .cloned()
        .map(|(k, v)| (k.to_owned(), v))
        .collect()
}

#[test]
fn assistant_text_concatenates_text_blocks_and_skips_other_content() {
    let message = json!({"role": "assistant", "content": [
        {"type": "thinking", "thinking": "internal"},
        {"type": "text", "text": "first"},
        {"type": "toolCall", "id": "t-1", "name": "bash", "arguments": {}},
        {"type": "text", "text": "second"},
    ]});
    assert_eq!(assistant_text(&message), "first\nsecond");
    assert_eq!(assistant_text(&json!({"role": "assistant"})), "");
    assert_eq!(
        assistant_text(&json!({"role": "assistant", "content": []})),
        ""
    );
}

#[test]
fn flatten_tool_result_concatenates_text_and_preserves_non_text_payloads() {
    let text_result = json!({"content": [
        {"type": "text", "text": "line-1"},
        {"type": "text", "text": "line-2"},
    ]});
    assert_eq!(
        flatten_tool_result(Some(&text_result)),
        json!("line-1\nline-2")
    );
    // Image-only results keep their raw JSON instead of collapsing to "".
    let image_result =
        json!({"content": [{"type": "image", "data": "b64", "mimeType": "image/png"}]});
    assert_eq!(flatten_tool_result(Some(&image_result)), image_result);
    // Results without a content array (custom tool shapes) pass through.
    let raw_result = json!({"details": {"exitCode": 3}});
    assert_eq!(flatten_tool_result(Some(&raw_result)), raw_result);
    assert_eq!(flatten_tool_result(None), json!(""));
    assert_eq!(flatten_tool_result(Some(&Value::Null)), json!(""));
}

#[test]
fn tool_events_map_phases_and_fields() {
    let start = json!({"toolCallId": "call-1", "toolName": "bash", "args": {"command": "ls"}});
    let StreamEvent::Agent(event) = tool_event(&start, "run-1", ToolPhase::Start).unwrap() else {
        panic!("expected agent event");
    };
    let AgentData::Tool(ToolData {
        phase,
        name,
        tool_call_id,
        args,
        result,
        is_error,
        duration_ms,
        ..
    }) = event.data
    else {
        panic!("expected tool data");
    };
    assert_eq!(phase, ToolPhase::Start);
    assert_eq!(name.as_deref(), Some("bash"));
    assert_eq!(tool_call_id.as_deref(), Some("call-1"));
    assert_eq!(args, Some(json!({"command": "ls"})));
    assert!(result.is_none() && is_error.is_none() && duration_ms.is_none());

    let end = json!({"toolCallId": "call-1", "toolName": "bash", "isError": true, "durationMs": 42,
        "result": {"content": [{"type": "text", "text": "boom"}]}});
    let StreamEvent::Agent(event) = tool_event(&end, "run-1", ToolPhase::Result).unwrap() else {
        panic!("expected agent event");
    };
    let AgentData::Tool(ToolData {
        phase,
        is_error,
        duration_ms,
        result,
        ..
    }) = event.data
    else {
        panic!("expected tool data");
    };
    assert_eq!(phase, ToolPhase::Result);
    assert_eq!(is_error, Some(true));
    assert_eq!(duration_ms, Some(42));
    assert_eq!(result, Some(json!("boom")));

    // Frames without ids or names produce no event (the run continues).
    assert!(tool_event(&json!({"toolName": "bash"}), "run-1", ToolPhase::Start).is_none());
    assert!(tool_event(&json!({"toolCallId": "c"}), "run-1", ToolPhase::Start).is_none());
}

use bcs_protocol::stream::AgentData;

#[test]
fn pi_options_parse_shared_keys_and_reject_flavor_exclusive_keys() {
    let options = PiOptions::parse(
        &table(&[
            ("tools", toml::Value::String("read,grep".into())),
            ("approve", toml::Value::Boolean(true)),
            ("thinking", toml::Value::String(" high ".into())),
            ("no_extensions", toml::Value::Boolean(true)),
        ]),
        PiFlavor::Pi,
    )
    .unwrap();
    let mut args = Vec::new();
    options.push_args(&mut args, PiFlavor::Pi);
    assert_eq!(
        args.iter().map(String::as_str).collect::<Vec<_>>(),
        vec![
            "--tools",
            "read,grep",
            "--approve",
            "--thinking",
            "high",
            "--no-extensions"
        ]
    );

    let options = PiOptions::parse(
        &table(&[("approve", toml::Value::Boolean(false))]),
        PiFlavor::Omp,
    )
    .unwrap();
    let mut args = Vec::new();
    options.push_args(&mut args, PiFlavor::Omp);
    assert_eq!(
        args.iter().map(String::as_str).collect::<Vec<_>>(),
        vec!["--no-approve"]
    );

    // Neutral defaults pass no flags.
    let mut args = Vec::new();
    PiOptions::default().push_args(&mut args, PiFlavor::Pi);
    assert!(args.is_empty());

    // Flavor-exclusive keys fail fast instead of being ignored.
    assert!(
        PiOptions::parse(
            &table(&[("session_dir", toml::Value::String("/tmp/s".into()))]),
            PiFlavor::Omp
        )
        .unwrap_err()
        .contains("pi-only key")
    );
    assert!(
        PiOptions::parse(
            &table(&[("no_ui", toml::Value::Boolean(true))]),
            PiFlavor::Pi
        )
        .unwrap_err()
        .contains("omp-only key")
    );
    assert!(
        PiOptions::parse(
            &table(&[("sandbox", toml::Value::String("read-only".into()))]),
            PiFlavor::Pi
        )
        .unwrap_err()
        .contains("unsupported engine_options key 'sandbox'")
    );
    assert!(
        PiOptions::parse(
            &table(&[("tools", toml::Value::String("  ".into()))]),
            PiFlavor::Pi
        )
        .unwrap_err()
        .contains("unsupported engine_options key 'tools'")
    );

    // pi session_dir reaches the argv.
    let options = PiOptions::parse(
        &table(&[(
            "session_dir",
            toml::Value::String("/var/pi-sessions".into()),
        )]),
        PiFlavor::Pi,
    )
    .unwrap();
    let mut args = Vec::new();
    options.push_args(&mut args, PiFlavor::Pi);
    assert!(
        args.is_empty(),
        "session_dir is spawn-time wiring, not a pushed flag"
    );

    // omp no_ui reaches the argv only for omp.
    let options = PiOptions::parse(
        &table(&[("no_ui", toml::Value::Boolean(true))]),
        PiFlavor::Omp,
    )
    .unwrap();
    let mut args = Vec::new();
    options.push_args(&mut args, PiFlavor::Omp);
    assert_eq!(
        args.iter().map(String::as_str).collect::<Vec<_>>(),
        vec!["--no-ui"]
    );
}

#[test]
fn omp_session_ids_and_directories_are_bridge_shaped() {
    let id = new_omp_session_id();
    assert!(
        is_valid_engine_session_id(&id),
        "generated id must satisfy the bridge validator: {id}"
    );
    assert!(
        id.starts_with("bcs-"),
        "id should be recognizable as bridge-owned: {id}"
    );
    let dir = omp_session_dir(Path::new("/ws"), &id);
    assert_eq!(dir, Path::new("/ws/.bcs-bridge/omp-sessions").join(&id));
}

#[tokio::test]
async fn pi_settled_frame_finishes_with_authoritative_text() {
    let (events_tx, mut events_rx) = tokio::sync::mpsc::channel(16);
    let mut state = TurnState::default();
    let abort = CancellationToken::new();
    // No subprocess is spawned on the pure mapping paths.
    let mut cli = null_session().await;
    let settled = json!({"type": "agent_settled", "aborted": false});
    let outcome = handle_frame(
        &PiFlavor::Pi,
        json!({"type": "message_end", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "答案"}], "stopReason": "stop"}}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap();
    assert!(matches!(outcome, FrameOutcome::Continue));
    let outcome = handle_frame(
        &PiFlavor::Pi,
        settled,
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap();
    let FrameOutcome::Settled { final_text } = outcome else {
        panic!("expected settled")
    };
    let result = finish(state, "sess-1".into(), final_text).unwrap();
    assert_eq!(result.final_text.as_deref(), Some("答案"));
    assert_eq!(result.engine_session_id.as_deref(), Some("sess-1"));
    // No events were emitted by the terminal frames.
    assert!(events_rx.try_recv().is_err());
}

#[tokio::test]
async fn pi_aborted_settled_and_error_stop_reasons_surface_as_turn_errors() {
    let (events_tx, _events_rx) = tokio::sync::mpsc::channel(16);
    let abort = CancellationToken::new();
    let mut cli = null_session().await;
    let mut state = TurnState::default();
    let error = handle_frame(
        &PiFlavor::Pi,
        json!({"type": "agent_settled", "aborted": true}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap_err();
    assert_eq!(error.to_string(), "engine exited: pi aborted the run");

    let mut state = TurnState::default();
    handle_frame(
        &PiFlavor::Pi,
        json!({"type": "message_end", "message": {"role": "assistant", "content": [],
            "stopReason": "error", "errorMessage": "provider 500"}}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap();
    let outcome = handle_frame(
        &PiFlavor::Pi,
        json!({"type": "agent_settled", "aborted": false}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap();
    let FrameOutcome::Settled { final_text } = outcome else {
        panic!("expected settled")
    };
    let error = finish(state, "sess-1".into(), final_text).unwrap_err();
    assert_eq!(error.to_string(), "engine exited: provider 500");
}

#[tokio::test]
async fn locally_handled_prompts_settle_with_empty_final_text() {
    let (events_tx, _events_rx) = tokio::sync::mpsc::channel(16);
    let abort = CancellationToken::new();
    let mut cli = null_session().await;
    let mut state = TurnState::default();

    let pi = handle_frame(
        &PiFlavor::Pi,
        json!({"type": "response", "id": "bridge-prompt", "command": "prompt",
            "success": true, "data": {"disposition": "handled"}}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap();
    let FrameOutcome::Settled { final_text } = pi else {
        panic!("expected settled")
    };
    assert_eq!(final_text, Some(String::new()));

    let mut state = TurnState::default();
    let omp = handle_frame(
        &PiFlavor::Omp,
        json!({"type": "response", "id": "bridge-prompt", "command": "prompt",
            "success": true, "data": {"agentInvoked": false}}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap();
    let FrameOutcome::Settled { final_text } = omp else {
        panic!("expected settled")
    };
    assert_eq!(final_text, Some(String::new()));
}

#[tokio::test]
async fn rejected_prompts_and_omp_prompt_results_map_to_errors() {
    let (events_tx, _events_rx) = tokio::sync::mpsc::channel(16);
    let abort = CancellationToken::new();
    let mut cli = null_session().await;
    let mut state = TurnState::default();

    let rejected = handle_frame(
        &PiFlavor::Pi,
        json!({"type": "response", "id": "bridge-prompt", "command": "prompt",
            "success": false, "error": "Model not found: invalid/model"}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap_err();
    assert!(rejected.to_string().contains("invalid/model"), "{rejected}");

    let mut state = TurnState::default();
    let aborted = handle_frame(
        &PiFlavor::Omp,
        json!({"type": "prompt_result", "id": "bridge-prompt", "agentInvoked": true,
            "status": "aborted", "sessionSettled": true}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap_err();
    assert_eq!(aborted.to_string(), "engine exited: omp aborted the prompt");

    let mut state = TurnState::default();
    let failed = handle_frame(
        &PiFlavor::Omp,
        json!({"type": "prompt_result", "id": "bridge-prompt", "agentInvoked": true,
            "status": "error", "sessionSettled": true,
            "error": {"message": "provider timeout", "retryable": false}}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap_err();
    assert!(failed.to_string().contains("provider timeout"), "{failed}");
}

#[tokio::test]
async fn omp_turn_waits_for_session_settled_after_unsettled_prompt_result() {
    let (events_tx, _events_rx) = tokio::sync::mpsc::channel(16);
    let abort = CancellationToken::new();
    let mut cli = null_session().await;
    let mut state = TurnState::default();

    // Pre-prompt quiescence must not end the turn.
    let early = handle_frame(
        &PiFlavor::Omp,
        json!({"type": "session_settled"}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap();
    assert!(matches!(early, FrameOutcome::Continue));

    let pending = handle_frame(
        &PiFlavor::Omp,
        json!({"type": "prompt_result", "id": "bridge-prompt", "agentInvoked": true,
            "status": "completed", "sessionSettled": false}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap();
    assert!(
        matches!(pending, FrameOutcome::Continue),
        "background work may still stream"
    );

    // A background follow-up run streams before quiescence.
    handle_frame(
        &PiFlavor::Omp,
        json!({"type": "message_end", "message": {"role": "assistant",
            "content": [{"type": "text", "text": "后台结论"}], "stopReason": "stop"}}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap();

    let settled = handle_frame(
        &PiFlavor::Omp,
        json!({"type": "session_settled"}),
        "run-1",
        "bridge-prompt",
        &mut state,
        &events_tx,
        &mut cli,
        &abort,
    )
    .await
    .unwrap();
    let FrameOutcome::Settled { final_text } = settled else {
        panic!("expected settled")
    };
    let result = finish(state, "bcs-x".into(), final_text).unwrap();
    assert_eq!(result.final_text.as_deref(), Some("后台结论"));
}

#[tokio::test]
async fn unknown_frames_are_ignored_for_forward_compatibility() {
    let (events_tx, _events_rx) = tokio::sync::mpsc::channel(16);
    let abort = CancellationToken::new();
    let mut cli = null_session().await;
    let mut state = TurnState::default();
    for frame in [
        json!({"type": "queue_update", "steering": [], "followUp": []}),
        json!({"type": "compaction_start", "reason": "threshold"}),
        json!({"type": "available_commands_update", "commands": []}),
        json!({"type": "rpc_frame_error"}),
        json!({"type": "extension_ui_request", "dialog": {}}),
        json!({"type": "response", "id": "someone-else", "command": "bash", "success": true}),
        json!({"type": "prompt_result", "id": "someone-else", "agentInvoked": true, "status": "completed", "sessionSettled": true}),
    ] {
        let flavor = if frame["type"] == "prompt_result" {
            &PiFlavor::Omp
        } else {
            &PiFlavor::Pi
        };
        let outcome = handle_frame(
            flavor,
            frame.clone(),
            "run-1",
            "bridge-prompt",
            &mut state,
            &events_tx,
            &mut cli,
            &abort,
        )
        .await
        .unwrap();
        assert!(
            matches!(outcome, FrameOutcome::Continue),
            "frame must be ignored: {frame}"
        );
    }
}

/// A [`CliSession`] handle for tests that exercise pure frame mapping: the
/// mapping paths never touch the process, and a sleeping placeholder keeps
/// the signature honest.
async fn null_session() -> CliSession {
    CliSession::spawn(
        Path::new("/bin/sleep"),
        &["300".to_string()],
        Path::new("/tmp"),
        &[],
        None,
    )
    .await
    .unwrap()
}
