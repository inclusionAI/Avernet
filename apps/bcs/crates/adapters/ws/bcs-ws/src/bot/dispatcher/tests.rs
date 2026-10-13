    use bcs_protocol::{ChatEventState, RouteSelectorWire};
    use bcs_service_api::{Group, Participant, ParticipantRole};
    use opentelemetry::trace::TracerProvider as _;
    use opentelemetry_sdk::trace::{InMemorySpanExporterBuilder, SdkTracerProvider, SpanData};
    use tracing_subscriber::prelude::*;

    use super::routing::resolve_route_selectors;
    use super::trace::{
        BOT_RESPONSE_CONTENT_LIMIT_BYTES, TRUNCATION_MARKER, record_ws_bot_response_trace,
        truncate_bot_response_content,
    };
    use super::*;

    fn participant(id: &str, name: &str, role: ParticipantRole) -> Participant {
        let mut participant = Participant::bot(id, role);
        participant.bot_name = Some(name.to_string());
        participant
    }

    fn poker_group() -> Group {
        Group::new(
            "group-1",
            "20260512_1fl0038o:437240",
            vec![
                participant(
                    "20260512_1fl0038o:437240",
                    "poker_发牌师傅",
                    ParticipantRole::Driver,
                ),
                participant(
                    "20260604_m869qsxf:146836",
                    "qz的德州钻石玩家bot_v1",
                    ParticipantRole::Consultant,
                ),
            ],
        )
    }

    #[test]
    fn unwrap_outbound_group_id_accepts_legacy_plain_group_session_param() {
        let (group_id, session_id) = unwrap_outbound_group_id("group-1:abcdef12", Some("group-1"));

        assert_eq!(group_id, "group-1");
        assert_eq!(session_id.as_deref(), Some("group-1:abcdef12"));
    }

    #[test]
    fn bot_response_truncation_is_utf8_safe_and_preserves_head_and_tail() {
        let content = format!("START{}END", "你".repeat(2000));

        let (captured, truncated) = truncate_bot_response_content(&content);

        assert!(truncated);
        assert!(captured.starts_with("START"));
        assert!(captured.ends_with("END"));
        assert!(captured.contains(TRUNCATION_MARKER));
        assert!(captured.len() <= BOT_RESPONSE_CONTENT_LIMIT_BYTES);
        assert!(std::str::from_utf8(captured.as_bytes()).is_ok());
    }

    #[test]
    fn terminal_bot_response_records_schema_compliant_output_messages() {
        let span = capture_bot_response_span(
            ChatEventState::Final,
            serde_json::json!({
                "state": "final",
                "message": {
                    "content": [{ "type": "text", "text": "bot \"answer\"" }]
                }
            }),
        );

        let Some(value) = span
            .attributes
            .iter()
            .find_map(|attribute| match &attribute.value {
                opentelemetry::Value::String(value)
                    if attribute.key.as_str() == "gen_ai.output.messages" =>
                {
                    Some(value.as_str())
                }
                _ => None,
            })
        else {
            panic!("expected gen_ai.output.messages string attribute");
        };
        let Ok(messages): std::result::Result<Value, _> = serde_json::from_str(value) else {
            panic!("expected schema-compliant output messages JSON");
        };
        assert_eq!(messages.as_array().map(Vec::len), Some(1));
        assert_eq!(messages[0]["role"], "assistant");
        assert_eq!(messages[0]["parts"].as_array().map(Vec::len), Some(1));
        assert_eq!(messages[0]["parts"][0]["type"], "text");
        assert_eq!(messages[0]["parts"][0]["content"], "bot \"answer\"");
        assert_eq!(messages[0]["finish_reason"], "stop");
    }

    #[test]
    fn delta_bot_response_records_bcn_chunk_instead_of_complete_output() {
        let span = capture_bot_response_span(
            ChatEventState::Delta,
            serde_json::json!({
                "state": "delta",
                "message": {
                    "content": [{ "type": "text", "text": "partial response" }]
                }
            }),
        );

        assert!(
            span.attributes
                .iter()
                .all(|attribute| { attribute.key.as_str() != "gen_ai.output.messages" })
        );
        assert!(span.attributes.iter().any(|attribute| {
            attribute.key.as_str() == "bcn.bot.response.chunk"
                && matches!(&attribute.value, opentelemetry::Value::String(value) if value.as_str() == "partial response")
        }));
    }

    #[test]
    fn terminal_bot_response_preserves_non_text_payload_as_text_content() {
        let payload = serde_json::json!({
            "state": "final",
            "message": {
                "content": [{ "type": "image", "url": "https://example.invalid/image.png" }]
            }
        });
        let span = capture_bot_response_span(ChatEventState::Final, payload.clone());

        let Some(value) = span
            .attributes
            .iter()
            .find_map(|attribute| match &attribute.value {
                opentelemetry::Value::String(value)
                    if attribute.key.as_str() == "gen_ai.output.messages" =>
                {
                    Some(value.as_str())
                }
                _ => None,
            })
        else {
            panic!("expected gen_ai.output.messages string attribute");
        };
        let Ok(messages): std::result::Result<Value, _> = serde_json::from_str(value) else {
            panic!("expected schema-compliant output messages JSON");
        };
        assert_eq!(messages[0]["parts"][0]["content"], payload.to_string());
    }

    fn capture_bot_response_span(event_state: ChatEventState, event_payload: Value) -> SpanData {
        let exporter = InMemorySpanExporterBuilder::new().build();
        let provider = SdkTracerProvider::builder()
            .with_simple_exporter(exporter.clone())
            .build();
        let tracer = provider.tracer("bcn-ws-response-test");
        let subscriber =
            tracing_subscriber::registry().with(tracing_opentelemetry::layer().with_tracer(tracer));

        tracing::subscriber::with_default(subscriber, || {
            let span = tracing::info_span!(target: "bcn_otel", "bcn.bot.response");
            let _guard = span.enter();
            record_ws_bot_response_trace("bot-1", "run-1", &event_state, &event_payload);
        });

        assert!(provider.force_flush().is_ok());
        let Ok(mut spans) = exporter.get_finished_spans() else {
            panic!("expected exported bot response span");
        };
        spans.remove(0)
    }

    #[test]
    fn route_resolve_unknown_name_returns_candidates() {
        let group = poker_group();
        let result = resolve_route_selectors(
            &group.participants,
            &[RouteSelectorWire {
                selector_type: "name".to_string(),
                value: Some("钻石玩家".to_string()),
            }],
        );

        assert!(!result.ok);
        assert_eq!(result.error.as_deref(), Some("UNKNOWN_ROUTE_TARGET"));
        assert_eq!(result.candidates.len(), 1);
        assert_eq!(result.candidates[0].bot_uuid, "20260604_m869qsxf:146836");
        assert_eq!(
            result.candidates[0].bot_name.as_deref(),
            Some("qz的德州钻石玩家bot_v1")
        );
    }

    #[test]
    fn route_resolve_exact_name_returns_canonical_bot_selector() {
        let group = poker_group();
        let result = resolve_route_selectors(
            &group.participants,
            &[RouteSelectorWire {
                selector_type: "name".to_string(),
                value: Some("qz的德州钻石玩家bot_v1".to_string()),
            }],
        );

        assert!(result.ok);
        assert_eq!(result.resolved.len(), 1);
        assert_eq!(result.resolved[0].selector_type, "bot");
        assert_eq!(result.resolved[0].value, "20260604_m869qsxf:146836");
        assert_eq!(
            result.resolved[0].bot_name.as_deref(),
            Some("qz的德州钻石玩家bot_v1")
        );
    }
