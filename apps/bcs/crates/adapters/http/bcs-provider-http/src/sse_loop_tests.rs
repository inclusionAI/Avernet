use super::*;
    use bcs_service_api::{
        BotEventOutcome, ChatAbortCommand, ChatAbortOutcome, GroupCallbackCommand,
        GroupCallbackOutcome, InteractionFrontendEvent, InteractionRequestedOutcome,
        InteractionService, InteractionServiceError, MessageFlowService,
        ProviderInteractionRequestedCommand, ProviderInteractionResolvedCommand,
        ResolveInteractionCommand, ResolveInteractionResult, TaskCompleteCommand,
        TaskCompleteOutcome, TaskDispatchCommand, TaskDispatchOutcome, TaskRunAliasRegistration,
        WebSendCommand, WebSendOutcome,
    };
    use std::sync::Mutex;

    /// Records each ingested (event_type, state, event_payload) tuple.
    #[derive(Default)]
    struct RecordingFlow {
        events: Mutex<Vec<(String, ChatEventState, Value)>>,
    }

    #[derive(Default)]
    struct RecordingInteractions {
        requested: Mutex<Vec<ProviderInteractionRequestedCommand>>,
        resolved: Mutex<Vec<ProviderInteractionResolvedCommand>>,
    }

    #[async_trait]
    impl InteractionService for RecordingInteractions {
        async fn on_provider_requested(
            &self,
            command: ProviderInteractionRequestedCommand,
        ) -> ServiceResult<InteractionRequestedOutcome> {
            self.requested.lock().unwrap().push(command);
            Ok(InteractionRequestedOutcome::Stored)
        }

        async fn on_provider_resolved(
            &self,
            command: ProviderInteractionResolvedCommand,
        ) -> ServiceResult<()> {
            self.resolved.lock().unwrap().push(command);
            Ok(())
        }

        async fn resolve(
            &self,
            _command: ResolveInteractionCommand,
        ) -> Result<ResolveInteractionResult, InteractionServiceError> {
            unimplemented!("not used in Provider SSE tests")
        }

        async fn list_pending(
            &self,
            _bcs_session_id: &str,
        ) -> ServiceResult<Vec<InteractionFrontendEvent>> {
            Ok(Vec::new())
        }

        async fn invalidate_run(
            &self,
            _bcs_run_id: &str,
            _reason: &str,
            _invalidated_at_ms: u64,
        ) -> ServiceResult<usize> {
            Ok(0)
        }

        async fn cleanup_terminal(&self, _terminal_before_ms: u64) -> ServiceResult<usize> {
            Ok(0)
        }
    }

    impl RecordingFlow {
        fn snapshot(&self) -> Vec<(String, ChatEventState, Value)> {
            self.events.lock().unwrap().clone()
        }
        fn pairs(&self) -> Vec<(String, ChatEventState)> {
            self.events
                .lock()
                .unwrap()
                .iter()
                .map(|(event_type, state, _)| (event_type.clone(), state.clone()))
                .collect()
        }
    }

    #[async_trait]
    impl MessageFlowService for RecordingFlow {
        async fn handle_web_send(&self, _cmd: WebSendCommand) -> ServiceResult<WebSendOutcome> {
            unimplemented!("not used in sse loop tests")
        }
        async fn handle_bot_event(&self, cmd: BotEventCommand) -> ServiceResult<BotEventOutcome> {
            self.events.lock().unwrap().push((
                cmd.event_type.clone(),
                cmd.state.clone(),
                cmd.event_payload.clone(),
            ));
            Ok(BotEventOutcome {
                bot_deliveries: vec![],
                frontend_deliveries: vec![],
                unregistered_run_ids: vec![],
                mentions: vec![],
                delivered_count: 1,
                failed_count: 0,
                delivery_results: vec![],
            })
        }
        async fn handle_group_callback(
            &self,
            _cmd: GroupCallbackCommand,
        ) -> ServiceResult<GroupCallbackOutcome> {
            unimplemented!("not used in sse loop tests")
        }
        async fn handle_chat_abort(
            &self,
            _cmd: ChatAbortCommand,
        ) -> ServiceResult<ChatAbortOutcome> {
            unimplemented!("not used in sse loop tests")
        }
        async fn register_task_run_alias(
            &self,
            _task_id: &str,
            _run_id: &str,
            _bot_id: &str,
        ) -> ServiceResult<TaskRunAliasRegistration> {
            unimplemented!("not used in sse loop tests")
        }
        async fn handle_task_dispatch(
            &self,
            _cmd: TaskDispatchCommand,
        ) -> ServiceResult<TaskDispatchOutcome> {
            unimplemented!("not used in sse loop tests")
        }
        async fn handle_task_complete(
            &self,
            _cmd: TaskCompleteCommand,
        ) -> ServiceResult<TaskCompleteOutcome> {
            unimplemented!("not used in sse loop tests")
        }
    }

    #[async_trait]
    impl ProviderEventIngestService for RecordingFlow {
        async fn ingest_provider_event(
            &self,
            cmd: ProviderEventIngestCommand,
        ) -> ServiceResult<BotEventOutcome> {
            self.handle_bot_event(cmd.event).await
        }
    }

    /// Fixed run-context fake: always resolves with the given group/bot and a
    /// configurable deadline (so the per-frame deadline guard can be exercised).
    struct FixedCtx {
        deadline_ms: u64,
    }

    #[async_trait]
    impl BotRunContextPort for FixedCtx {
        async fn put_context(&self, _context: BotRunContext) {}
        async fn get_context(&self, run_id: &str) -> Option<BotRunContext> {
            Some(BotRunContext {
                run_id: run_id.to_string(),
                bot_id: "bot-1".into(),
                group_id: "grp-1".into(),
                bcs_session_id: None,
                deadline_ms: self.deadline_ms,
                terminal: false,
            })
        }
        async fn try_begin_terminal(&self, _run_id: &str) -> bool {
            true
        }
        async fn mark_terminal(&self, _run_id: &str) -> bool {
            true
        }
        async fn release_terminal(&self, _run_id: &str) {}
        async fn begin_provider_transport(&self, _run_id: &str, _deadline_ms: u64) -> bool {
            false
        }
        async fn bind_provider_transport(
            &self,
            _run_id: &str,
            _transport: ProviderRunTransport,
        ) -> bool {
            false
        }
        async fn get_provider_transport(&self, _run_id: &str) -> Option<ProviderRunTransport> {
            None
        }
        async fn mark_provider_transport_terminal(&self, _run_id: &str) {}
        async fn clear_provider_transport(&self, _run_id: &str) {}
    }

    /// Test wrapper: drive the per-frame ingest core over an in-memory SSE text
    /// split on `\n\n`, with one dedupe state spanning the whole stream. Returns
    /// whether a terminal was seen.
    async fn run_sse_text_for_test(
        sse_text: &str,
        bcn_run_id: &str,
        group_id: &str,
        bot_id: &str,
        flow: &Arc<dyn ProviderEventIngestService>,
    ) -> bool {
        run_sse_text_with_deadline(sse_text, bcn_run_id, group_id, bot_id, u64::MAX, flow).await
    }

    /// Like `run_sse_text_for_test` but with an explicit run deadline so the
    /// per-frame deadline guard can be exercised.
    async fn run_sse_text_with_deadline(
        sse_text: &str,
        bcn_run_id: &str,
        group_id: &str,
        bot_id: &str,
        deadline_ms: u64,
        flow: &Arc<dyn ProviderEventIngestService>,
    ) -> bool {
        let mut dedup = SeqDedup::default();
        let mut lag = LagTracker::default();
        let session: Option<String> = None;
        for block in sse_text.split("\n\n") {
            if block.trim().is_empty() {
                continue;
            }
            if let Some(true) = drive_sse_frame(
                block,
                bcn_run_id,
                group_id,
                bot_id,
                &session,
                deadline_ms,
                &mut dedup,
                &mut lag,
                flow,
                None,
            )
            .await
            {
                return true;
            }
        }
        false
    }

    #[tokio::test]
    async fn top_level_interactions_use_application_service_not_message_flow() {
        let recording_flow = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording_flow.clone();
        let interactions = Arc::new(RecordingInteractions::default());
        let context = SseInteractionContext {
            service: interactions.clone(),
            provider_target: BotDeliveryTarget::HttpProvider {
                bot_id: "bot-1".to_string(),
                provider_id: "provider-1".to_string(),
                provider_bot_ref: "ref-1".to_string(),
                webhook_url: "https://provider.example/webhook".to_string(),
                bcs_to_provider_token: bcs_domain::RedactedToken::new("secret"),
                protocol_version: "2.0".to_string(),
            },
            provider_bypass_headers: Vec::new(),
        };
        let session = Some("session-1".to_string());
        let mut dedup = SeqDedup::default();
        let mut lag = LagTracker::default();

        let handled = drive_sse_frame(
            "event: interaction\ndata: {\"runId\":\"provider-run-1\",\"seq\":1,\"phase\":\"requested\",\"interactionId\":\"interaction-1\",\"kind\":\"exec\",\"command\":\"deploy\"}",
            "bcs-run-1",
            "group-1",
            "bot-1",
            &session,
            u64::MAX,
            &mut dedup,
            &mut lag,
            &flow,
            Some(&context),
        )
        .await;

        assert_eq!(handled, Some(false));
        assert!(recording_flow.snapshot().is_empty());
        let requested = interactions.requested.lock().unwrap();
        assert_eq!(requested.len(), 1);
        assert_eq!(requested[0].bcs_run_id, "bcs-run-1");
        assert_eq!(requested[0].provider_run_id, "provider-run-1");
        assert_eq!(requested[0].bcs_session_id, "session-1");
    }

    #[tokio::test]
    async fn chat_timestamp_missing_uses_event_ts_for_every_state() {
        for (state, expected_state) in [
            ("delta", ChatEventState::Delta),
            ("final", ChatEventState::Final),
            ("error", ChatEventState::Error),
            ("aborted", ChatEventState::Aborted),
        ] {
            let recording = Arc::new(RecordingFlow::default());
            let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
            let data = serde_json::json!({
                "runId": "provider-run", "seq": 1, "ts": 1786260001000_u64,
                "state": state,
                "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "complete answer"}
                ]}
            });
            let terminal = run_sse_text_for_test(
                &format!("event: chat\ndata: {data}\n\n"),
                "bcn-run-1", "grp-1", "bot-1", &flow,
            ).await;
            assert_eq!(terminal, state != "delta", "{state}");
            let events = recording.snapshot();
            assert_eq!(events.len(), 1);
            assert_eq!(events[0].1, expected_state);
            assert_eq!(events[0].2["message"]["content"][0]["text"], "complete answer", "{state}");
            assert_eq!(events[0].2["message"]["timestamp"], 1786260001000_u64, "{state}");
        }
    }

    #[tokio::test]
    async fn chat_timestamp_missing_without_usable_ts_uses_receipt_time() {
        for ts in [None, Some(Value::Null), Some(serde_json::json!("bad")),
            Some(serde_json::json!(-1)), Some(serde_json::json!(1.5))] {
            let mut data = serde_json::json!({
                "runId": "provider-run", "seq": 1, "state": "final",
                "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "final without deltas"}
                ]}
            });
            if let Some(ts) = ts { data["ts"] = ts; }
            let recording = Arc::new(RecordingFlow::default());
            let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
            let before = bcs_protocol::now_ms();
            assert!(run_sse_text_for_test(
                &format!("event: chat\ndata: {data}\n\n"),
                "bcn-run-1", "grp-1", "bot-1", &flow,
            ).await);
            let after = bcs_protocol::now_ms();
            let events = recording.snapshot();
            let timestamp = events[0].2["message"]["timestamp"].as_u64()
                .expect("missing timestamp must not discard the final body");
            assert!((before..=after).contains(&timestamp));
            assert_eq!(events[0].2["message"]["content"][0]["text"], "final without deltas");
        }
    }

    #[tokio::test]
    async fn canonical_chat_content_projects_to_message_and_delta_text() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
        let sse = "event: chat\ndata: {\"runId\":\"engine-run\",\"seq\":1,\"ts\":1786260001000,\"state\":\"delta\",\"content\":\"working \"}\n\n\
event: chat\ndata: {\"runId\":\"engine-run\",\"seq\":2,\"ts\":1786260002000,\"state\":\"final\",\"content\":\"working done\"}\n\n";

        assert!(run_sse_text_for_test(
            sse,
            "bcn-run-content",
            "grp-1",
            "bot-1",
            &flow,
        )
        .await);

        let events = recording.snapshot();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].0, "chat.event");
        assert_eq!(events[0].1, ChatEventState::Delta);
        assert_eq!(events[0].2["delta_text"], "working ");
        assert_eq!(events[0].2["message"]["content"][0]["text"], "working ");
        assert_eq!(events[0].2["message"]["timestamp"], 1786260001000_u64);
        assert_eq!(events[1].0, "chat.event");
        assert_eq!(events[1].1, ChatEventState::Final);
        assert_eq!(events[1].2["message"]["content"][0]["text"], "working done");
        assert_eq!(events[1].2["message"]["timestamp"], 1786260002000_u64);
    }

    #[tokio::test]
    async fn canonical_tool_aliases_are_normalized_without_dropping_raw_fields() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
        let sse = "event: agent\ndata: {\"runId\":\"engine-run\",\"seq\":1,\"stream\":\"tool\",\"phase\":\"start\",\"toolCallId\":\"tc-1\",\"toolName\":\"mcp__bcs__call\",\"arguments\":{\"intent\":\"task.create\"},\"providerExtension\":\"kept\"}\n\n\
event: agent\ndata: {\"runId\":\"engine-run\",\"seq\":2,\"stream\":\"tool\",\"phase\":\"result\",\"toolCallId\":\"tc-1\",\"toolName\":\"mcp__bcs__call\",\"isError\":false,\"result\":{\"content\":[{\"type\":\"text\",\"text\":\"ok\"}]}}\n\n";

        assert!(!run_sse_text_for_test(
            sse,
            "bcn-run-tool",
            "grp-1",
            "bot-1",
            &flow,
        )
        .await);

        let events = recording.snapshot();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].1, ChatEventState::ToolCallStart);
        assert_eq!(events[0].2["data"]["name"], "mcp__bcs__call");
        assert_eq!(events[0].2["data"]["args"]["intent"], "task.create");
        assert_eq!(events[0].2["data"]["providerExtension"], "kept");
        assert_eq!(events[1].1, ChatEventState::ToolCallEnd);
        assert_eq!(events[1].2["data"]["name"], "mcp__bcs__call");
        assert_eq!(events[1].2["data"]["isError"], false);
        assert_eq!(events[1].2[TASK_INTENT_ELIGIBLE_KEY], true);
    }

    #[tokio::test]
    async fn agent_error_projects_to_chat_error_terminal() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
        let sse = "event: agent\ndata: {\"runId\":\"engine-run\",\"seq\":1,\"ts\":1786260001000,\"stream\":\"error\",\"errorCode\":\"MODEL_ERROR\",\"errorKind\":\"provider_error\",\"errorMessage\":\"model failed\"}\n\n";

        assert!(run_sse_text_for_test(
            sse,
            "bcn-run-error",
            "grp-1",
            "bot-1",
            &flow,
        )
        .await);

        let events = recording.snapshot();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].0, "chat.event");
        assert_eq!(events[0].1, ChatEventState::Error);
        assert_eq!(events[0].2["state"], "error");
        assert_eq!(events[0].2["errorCode"], "MODEL_ERROR");
        assert_eq!(events[0].2["errorKind"], "provider_error");
        assert_eq!(events[0].2["errorMessage"], "model failed");
        assert_eq!(events[0].2["message"]["content"][0]["text"], "model failed");
    }

    #[test]
    fn chat_timestamp_preserves_supplied_values_and_raw_event() {
        for timestamp in [None, Some(0_u64), Some(1786260000000), Some(u64::MAX)] {
            let mut raw = serde_json::json!({
                "runId": "provider-run", "state": "final", "ts": 1786260001000_u64,
                "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "answer"}
                ]}
            });
            if let Some(timestamp) = timestamp { raw["message"]["timestamp"] = timestamp.into(); }
            let event = parse_stream_event("chat", raw.clone());
            let payload = build_event_payload(&event, "bcn-run-1", "grp-1", 1786260002000);
            assert_eq!(payload["message"]["timestamp"], timestamp.unwrap_or(1786260001000));
            let StreamEvent::Chat(chat) = event else { panic!("expected chat"); };
            assert_eq!(chat.raw, raw);
            assert_eq!(chat.message.as_ref(), raw.get("message"));
        }
    }

    #[test]
    fn chat_timestamp_fallback_does_not_mask_invalid_messages() {
        for message in [
            serde_json::json!({"role": "assistant", "content": [], "timestamp": null}),
            serde_json::json!({"role": "assistant", "content": [], "timestamp": "123"}),
            serde_json::json!({"role": "assistant", "content": [], "timestamp": -1}),
            serde_json::json!({"role": "assistant", "content": [], "timestamp": 1.5}),
            serde_json::json!({"content": []}),
            serde_json::json!({"role": "assistant"}),
            serde_json::json!({"role": 1, "content": []}),
            serde_json::json!({"role": "assistant", "content": "bad"}),
            Value::Null,
        ] {
            let event = parse_stream_event("chat", serde_json::json!({
                "runId": "provider-run", "state": "final", "ts": 1786260001000_u64,
                "message": message,
            }));
            let payload = build_event_payload(&event, "bcn-run-1", "grp-1", 1786260002000);
            assert!(payload["message"].is_null(), "invalid message accepted: {message}");
        }
    }

    #[tokio::test]
    async fn read_loop_ingests_delta_then_final_and_dedupes() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
        let sse = "event: agent\nid: 1\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n\
event: ping\ndata: {\"ts\":1}\n\n\
event: agent\nid: 1\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n\
event: chat\nid: 2\ndata: {\"runId\":\"e\",\"seq\":2,\"state\":\"final\",\"message\":{\"role\":\"assistant\",\"content\":[],\"timestamp\":0}}\n\n";
        let terminal = run_sse_text_for_test(sse, "bcn-run-1", "grp-1", "bot-1", &flow).await;
        assert!(terminal, "final should close the run");

        // duplicate seq1 dropped, ping dropped: thinking(agent/Delta) + chat final.
        assert_eq!(
            recording.pairs(),
            vec![
                ("agent".to_string(), ChatEventState::Delta),
                ("chat.event".to_string(), ChatEventState::Final),
            ]
        );
        // chat payload must use snake_case BCS wire names, NOT deltaText.
        let snapshot = recording.snapshot();
        let chat_payload = &snapshot[1].2;
        assert_eq!(chat_payload["run_id"], Value::String("bcn-run-1".into()));
        assert_eq!(chat_payload["bcs_group_id"], Value::String("grp-1".into()));
        assert!(chat_payload.get("deltaText").is_none());
    }

    #[tokio::test]
    async fn read_loop_synthesizes_error_message_body() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
        let sse = "event: chat\nid: 1\ndata: {\"runId\":\"engine-run\",\"seq\":1,\"state\":\"error\",\"errorMessage\":\"engine crashed\",\"errorKind\":\"provider_error\"}\n\n";
        let terminal = run_sse_text_for_test(sse, "bcn-run-err", "grp-1", "bot-1", &flow).await;
        assert!(terminal, "error should close the run");

        let snapshot = recording.snapshot();
        assert_eq!(snapshot.len(), 1);
        let (event_type, state, payload) = &snapshot[0];
        assert_eq!(event_type, "chat.event");
        assert_eq!(*state, ChatEventState::Error);
        assert_eq!(payload["run_id"], Value::String("bcn-run-err".into()));
        assert_eq!(payload["bcs_group_id"], Value::String("grp-1".into()));
        assert_eq!(payload["errorMessage"], "engine crashed");
        assert_eq!(payload["errorKind"], "provider_error");
        assert_eq!(payload["message"]["content"][0]["text"], "engine crashed");
    }

    #[tokio::test]
    async fn read_loop_falls_back_from_blank_error_message_body() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
        let sse = "event: chat\nid: 1\ndata: {\"runId\":\"engine-run\",\"seq\":1,\"state\":\"error\",\"errorMessage\":\"engine crashed\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"   \"}],\"timestamp\":0}}\n\n";
        let terminal = run_sse_text_for_test(sse, "bcn-run-err", "grp-1", "bot-1", &flow).await;
        assert!(terminal, "error should close the run");

        let snapshot = recording.snapshot();
        assert_eq!(snapshot.len(), 1);
        let payload = &snapshot[0].2;
        assert_eq!(payload["errorMessage"], "engine crashed");
        assert_eq!(payload["message"]["content"][0]["text"], "engine crashed");
    }

    #[tokio::test]
    async fn crlf_frame_separators_are_split() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
        // CRLF line endings AND CRLF-CRLF frame separators across the byte path.
        let sse = "event: agent\r\nid: 1\r\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\r\n\r\n\
event: chat\r\nid: 2\r\ndata: {\"runId\":\"e\",\"seq\":2,\"state\":\"final\",\"message\":{\"role\":\"assistant\",\"content\":[],\"timestamp\":0}}\r\n\r\n";

        // Drive through the real byte splitter (find_frame_sep) to exercise CRLF.
        let mut dedup = SeqDedup::default();
        let mut lag = LagTracker::default();
        let session: Option<String> = None;
        let mut buf = sse.as_bytes().to_vec();
        let mut terminal = false;
        while let Some((idx, sep_len)) = find_frame_sep(&buf) {
            let frame: Vec<u8> = buf.drain(..idx + sep_len).collect();
            if let Some(true) = drive_frame_bytes(
                &frame,
                "bcn-run-1",
                "grp-1",
                "bot-1",
                &session,
                u64::MAX,
                &mut dedup,
                &mut lag,
                &flow,
                None,
            )
            .await
            {
                terminal = true;
                break;
            }
        }
        assert!(terminal);
        assert_eq!(
            recording.pairs(),
            vec![
                ("agent".to_string(), ChatEventState::Delta),
                ("chat.event".to_string(), ChatEventState::Final),
            ]
        );
    }

    #[tokio::test]
    async fn dedupe_works_without_sse_id_using_payload_seq() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
        // No `id:` lines at all — dedupe must come from StreamEvent.seq (#4).
        let sse = "event: agent\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n\
event: agent\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n\
event: agent\ndata: {\"runId\":\"e\",\"seq\":2,\"stream\":\"thinking\",\"delta\":\"b\"}\n\n";
        run_sse_text_for_test(sse, "bcn-run-1", "grp-1", "bot-1", &flow).await;
        // Second seq=1 is a duplicate and dropped: only two accepts.
        assert_eq!(
            recording.pairs(),
            vec![
                ("agent".to_string(), ChatEventState::Delta),
                ("agent".to_string(), ChatEventState::Delta),
            ]
        );
    }

    #[tokio::test]
    async fn stream_end_without_terminal_synthesizes_error() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
        let resp = sse_response(
            "event: agent\nid: 1\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n",
        )
        .await;
        let ctx: Arc<dyn BotRunContextPort> = Arc::new(FixedCtx {
            deadline_ms: u64::MAX,
        });
        stream_and_drive(
            resp,
            "bcn-run-1".into(),
            "bot-1".into(),
            flow.clone(),
            ctx,
            None,
        )
        .await;
        let pairs = recording.pairs();
        // thinking delta, then a synthesized chat error terminal.
        assert_eq!(
            pairs,
            vec![
                ("agent".to_string(), ChatEventState::Delta),
                ("chat.event".to_string(), ChatEventState::Error),
            ]
        );
    }

    #[tokio::test]
    async fn approval_frame_closes_run_unsupported() {
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
        let sse = "event: agent\nid: 1\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"approval\",\"phase\":\"requested\",\"kind\":\"exec\"}\n\n";
        let terminal = run_sse_text_for_test(sse, "bcn-run-1", "grp-1", "bot-1", &flow).await;
        assert!(terminal, "approval gate must close the run");
        assert_eq!(
            recording.pairs(),
            vec![("chat.event".to_string(), ChatEventState::Error)]
        );
    }

    #[tokio::test]
    async fn past_deadline_frames_are_dropped_before_ingest() {
        // A run whose deadline is already in the past must not ingest any frame:
        // the per-frame guard (#2) drops every frame + WARNs before ingest.
        let recording = Arc::new(RecordingFlow::default());
        let flow: Arc<dyn ProviderEventIngestService> = recording.clone();
        let sse = "event: agent\nid: 1\ndata: {\"runId\":\"e\",\"seq\":1,\"stream\":\"thinking\",\"delta\":\"a\"}\n\n\
event: chat\nid: 2\ndata: {\"runId\":\"e\",\"seq\":2,\"state\":\"final\",\"message\":{\"role\":\"assistant\",\"content\":[],\"timestamp\":0}}\n\n";
        // deadline_ms = 0 is always in the past relative to now_ms().
        let terminal =
            run_sse_text_with_deadline(sse, "bcn-run-1", "grp-1", "bot-1", 0, &flow).await;
        assert!(!terminal, "expired run must not report a terminal");
        assert!(
            recording.pairs().is_empty(),
            "no frame may be ingested past the run deadline"
        );

        // Sanity: the very same stream with a fresh deadline ingests normally.
        let recording_ok = Arc::new(RecordingFlow::default());
        let flow_ok: Arc<dyn ProviderEventIngestService> = recording_ok.clone();
        let terminal_ok =
            run_sse_text_with_deadline(sse, "bcn-run-1", "grp-1", "bot-1", u64::MAX, &flow_ok)
                .await;
        assert!(terminal_ok, "fresh-deadline run should close on the final");
        assert_eq!(
            recording_ok.pairs(),
            vec![
                ("agent".to_string(), ChatEventState::Delta),
                ("chat.event".to_string(), ChatEventState::Final),
            ]
        );
    }

    // Helpers --------------------------------------------------------------
    /// Build a streaming reqwest::Response from a fixed SSE body for the
    /// `stream_and_drive` integration tests.
    async fn sse_response(body: &'static str) -> reqwest::Response {
        use axum::Router;
        use axum::routing::post;

        let app = Router::new().route(
            "/sse",
            post(move || async move {
                axum::response::Response::builder()
                    .header("Content-Type", "text/event-stream")
                    .body(axum::body::Body::from(body))
                    .unwrap()
            }),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        reqwest::Client::new()
            .post(format!("http://{addr}/sse"))
            .send()
            .await
            .unwrap()
    }
