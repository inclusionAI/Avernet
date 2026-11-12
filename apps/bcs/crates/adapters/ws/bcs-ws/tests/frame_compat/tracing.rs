use super::*;

#[tokio::test]
async fn traced_chat_event_creates_bot_response_child_span_after_message_flow_accepts() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let (client_tx, _client_rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());
    let trace_parent = SpanContext::new(
        TraceId::from_hex("0af7651916cd43dd8448eb211c80319c").unwrap(),
        SpanId::from_hex("b7ad6b7169203331").unwrap(),
        TraceFlags::SAMPLED,
        false,
        TraceState::default(),
    );
    state
        .dispatch_state
        .run_channels
        .register_internal_with_trace_parent(
            "run-traced".to_string(),
            "group-1:abcdef12".to_string(),
            client_tx,
            Some("http-chat-async".to_string()),
            None,
            Some(trace_parent),
        )
        .await;
    assert!(
        state
            .dispatch_state
            .run_channels
            .trace_parent("run-traced")
            .await
            .is_some()
    );
    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-traced",
            "bcs_group_id": "group-1",
            "state": WireChatEventState::Delta,
            "message": {
                "role": "assistant",
                "content": [{ "type": "text", "text": "ws-response-content" }],
                "timestamp": 123
            }
        })),
        Some(1),
    ));
    let text = serde_json::to_string(&event).unwrap();

    let (result, spans) = capture_otel_spans(async move {
        dispatch_frame(
            &state.dispatch_state,
            &text,
            &tx,
            &mut registered_bot_id,
        )
        .await
    })
    .await;

    result.unwrap();
    assert_eq!(spans.len(), 1, "log correlation must not add a span");
    let span = spans
        .iter()
        .find(|span| span.name == "bcn.bot.response")
        .unwrap_or_else(|| panic!("expected bot response span, got {spans:#?}"));
    assert_eq!(span.span_context.trace_id().to_string(), "0af7651916cd43dd8448eb211c80319c");
    assert_eq!(span.parent_span_id.to_string(), "b7ad6b7169203331");
    assert!(span.events.events.is_empty());
    assert!(span.attributes.iter().any(|attr| {
        attr.key.as_str() == "bcn.content.untrusted"
            && attr.value == opentelemetry::Value::Bool(false)
    }));
    assert!(span.attributes.iter().all(|attr| {
        attr.key.as_str() != "gen_ai.output.messages"
    }));
    assert!(span.attributes.iter().any(|attr| {
        attr.key.as_str() == "bcn.bot.response.chunk"
            && matches!(&attr.value, opentelemetry::Value::String(value) if value.as_str() == "ws-response-content")
    }));
}

#[tokio::test]
async fn traced_plugin_run_alias_creates_bot_response_child_span() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let (client_tx, _client_rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());
    let trace_parent = SpanContext::new(
        TraceId::from_hex("0af7651916cd43dd8448eb211c80319c").unwrap(),
        SpanId::from_hex("b7ad6b7169203331").unwrap(),
        TraceFlags::SAMPLED,
        false,
        TraceState::default(),
    );
    state
        .dispatch_state
        .run_channels
        .register_internal_with_trace_parent(
            "gateway-run".to_string(),
            "group-1:abcdef12".to_string(),
            client_tx,
            Some("http-chat-async".to_string()),
            None,
            Some(trace_parent),
        )
        .await;

    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "plugin-run",
            "bcs_group_id": "group-1",
            "state": WireChatEventState::Final,
            "message": {
                "role": "assistant",
                "content": [{ "type": "text", "text": "plugin-ws-response" }],
                "timestamp": 123
            }
        })),
        Some(1),
    ));
    let event_text = serde_json::to_string(&event).unwrap();

    let (result, spans) = capture_otel_spans(async move {
        dispatch_frame(
            &state.dispatch_state,
            r#"{"type":"res","id":"gateway-run","ok":true,"payload":{"run_id":"plugin-run"}}"#,
            &tx,
            &mut registered_bot_id,
        )
        .await?;
        dispatch_frame(
            &state.dispatch_state,
            &event_text,
            &tx,
            &mut registered_bot_id,
        )
        .await
    })
    .await;

    result.unwrap();
    assert_eq!(spans.len(), 1, "log correlation must not add a span");
    let span = spans
        .iter()
        .find(|span| span.name == "bcn.bot.response")
        .unwrap_or_else(|| panic!("expected aliased bot response span, got {spans:#?}"));
    assert_eq!(span.span_context.trace_id().to_string(), "0af7651916cd43dd8448eb211c80319c");
    assert_eq!(span.parent_span_id.to_string(), "b7ad6b7169203331");
    assert!(span.events.events.is_empty());
    assert_gen_ai_output_message(span, "plugin-ws-response", "stop");
}

#[tokio::test]
async fn chat_event_without_trace_mapping_does_not_create_response_span() {
    let state = new_state();
    let (tx, _rx) = mpsc::channel(8);
    let mut registered_bot_id = Some("bot-compat:staff".to_string());
    let event = BcsFrame::Event(EventFrame::new(
        "chat.event",
        Some(serde_json::json!({
            "run_id": "run-untraced",
            "bcs_group_id": "group-1",
            "state": WireChatEventState::Delta,
            "message": {
                "role": "assistant",
                "content": [{ "type": "text", "text": "untraced-ws-response" }],
                "timestamp": 123
            }
        })),
        Some(1),
    ));
    let text = serde_json::to_string(&event).unwrap();

    let (result, spans) = capture_otel_spans(async move {
        dispatch_frame(
            &state.dispatch_state,
            &text,
            &tx,
            &mut registered_bot_id,
        )
        .await
    })
    .await;

    result.unwrap();
    assert!(spans.is_empty());
}
