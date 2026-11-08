    use super::*;

    #[test]
    fn extracts_app_server_ids() {
        assert_eq!(
            extract_thread_id(&json!({"thread": {"id": "t-1"}})).as_deref(),
            Some("t-1")
        );
        assert_eq!(
            extract_turn_id(&json!({"turn": {"id": "turn-1"}})).as_deref(),
            Some("turn-1")
        );
    }

    #[test]
    fn extracts_streaming_delta_shapes() {
        assert_eq!(
            extract_delta(&json!({"params": {"delta": "hello"}})).as_deref(),
            Some("hello")
        );
        assert_eq!(
            extract_delta(&json!({"params": {"text": "world"}})).as_deref(),
            Some("world")
        );
    }

    #[test]
    fn matches_only_the_active_turn() {
        let value = json!({
            "params": {"threadId": "t-1", "turnId": "turn-1"}
        });
        for shared in [false, true] {
            assert!(matches_turn(&value, "t-1", "turn-1", shared));
            assert!(!matches_turn(&value, "t-2", "turn-1", shared));
            assert!(!matches_turn(&value, "t-1", "turn-2", shared));
        }
    }

    #[test]
    fn shared_events_require_positive_ownership_but_cli_keeps_legacy_shapes() {
        for method in ["agent/output_chunk", "agent/turn_completed", "agent/turn_failed"] {
            let value = json!({"method":method,"params":{"text":"foreign"}});
            assert!(!matches_turn(&value, "t-1", "turn-1", true));
            assert!(matches_turn(&value, "t-1", "turn-1", false));
        }
        for params in [
            json!({"threadId":"t-1"}),
            json!({"turnId":"turn-1"}),
            json!({"threadId":"t-1","turnId":null}),
            json!({"threadId":null,"turnId":"turn-1"}),
        ] {
            assert!(!matches_turn(&json!({"params":params}), "t-1", "turn-1", true));
        }
    }

    #[test]
    fn thinking_deltas_stay_delta_only_never_carry_accumulated_snapshot() {
        // Regression guard for the retention overflow seen 2026-10-18 in run
        // 7e63c047: previously every reasoning delta was re-emitted with
        // the full accumulated thinking text attached (`Some(delta)` +
        // `Some(thinking.clone())`), amplifying retained thinking bytes
        // quadratically (~31MB accumulated from 22KB of unique text). Emit
        // only the fresh fragment, matching the Claude driver; this also
        // keeps per-event retained size linear instead of O(N^2).
        let run = "run-think";
        let mut accumulated = String::new();
        let mut emitted_bytes = 0usize;
        for i in 0..500usize {
            let delta = format!("d{i}-");
            accumulated.push_str(&delta);
            let event = sse::agent_thinking(run, Some(delta.clone()), None);
            let StreamEvent::Agent(agent) = event else { panic!("expected Agent event"); };
            let bcs_protocol::stream::AgentData::Thinking(thinking) = agent.data else {
                panic!("agent_thinking must yield ThinkingData");
            };
            assert!(thinking.text.is_none(),
                "must not embed the accumulated snapshot ({})",
                accumulated.len());
            assert_eq!(thinking.delta.as_deref(), Some(delta.as_str()));
            emitted_bytes += thinking.delta.as_deref().unwrap().len()
                + thinking.delta.as_deref().unwrap().len(); // shallow approximation
        }
        // rough linear sanity: emitted total is a small multiple of the unique stream
        let unique = accumulated.len();
        assert!(unique > 0);
        assert!(emitted_bytes < unique * 8,
            "emitted thinking stays near-linear, not quadratic (emitted={emitted_bytes}, unique={unique})");
    }

    #[test]
    fn completions_check_all_supplied_turn_ids_for_native_and_shared_connections() {
        for shared in [false, true] {
            for method in ["turn/completed", "agent/turn_completed", "agent/turn_failed"] {
                let mut value = json!({"method":method,"params":{
                    "threadId":"t-1","turn":{"id":"turn-1"}}});
                assert!(matches_turn(&value, "t-1", "turn-1", shared));
                assert!(!matches_turn(&value, "t-1", "turn-2", shared));
                value["params"]["turnId"] = json!("other-turn");
                assert!(!matches_turn(&value, "t-1", "turn-1", shared));
                assert!(!matches_turn(&value, "t-1", "other-turn", shared));
                value["params"]["turnId"] = Value::Null;
                assert!(!matches_turn(&value, "t-1", "turn-1", shared));
            }
        }
    }

    fn table(pairs: &[(&str, toml::Value)]) -> toml::Table {
        pairs.iter().cloned().map(|(k, v)| (k.to_owned(), v)).collect()
    }

    #[test]
    fn completed_text_reads_agent_message_items_before_any_legacy_fallback() {
        // The real wire shape (verified from a live bridge-side engine trace):
        // the final answer lives in params.turn.items[] as
        // {type:"agentMessage", phase:"final_answer", text:"..."}.
        let wire = json!({"method":"turn/completed","params":{
            "threadId":"t-1",
            "turn":{
                "completedAt":1792320351,"durationMs":95592,"error":null,
                "id":"turn-1","status":"completed",
                "items":[
                    {"type":"reasoning","id":"rs-1"},
                    {"type":"agentMessage","phase":"thinking","text":"对不起，还在中间分析。"},
                    {"type":"agentMessage","phase":"final_answer","text":"已将完整 Review 发布到 PR。"},
                ],
            },
        }});
        assert_eq!(completed_text(&wire).as_deref(), Some("已将完整 Review 发布到 PR。"));
        // Without a final_answer entry, take the last agentMessage with text.
        let wire = json!({"params":{"turn":{"items":[
            {"type":"agentMessage","text":"first"},
            {"type":"agentMessage","text":"second"},
        ]}}});
        assert_eq!(completed_text(&wire).as_deref(), Some("second"));
        // agentMessage with empty text counts as absent.
        let wire = json!({"params":{"turn":{"items":[
            {"type":"agentMessage","phase":"final_answer","text":"  "},
        ]}}});
        assert_eq!(completed_text(&wire), None);
    }

    #[test]
    fn completed_text_keeps_journal_shape_as_legacy_fallback() {
        let value = json!({"method":"turn/completed","params":{
            "threadId":"t-1","turnId":"turn-1",
            "last_agent_message":"review done"
        }});
        assert_eq!(completed_text(&value).as_deref(), Some("review done"));
        let legacy = json!({"method":"turn/completed","params":{
            "text":"legacy fallback never delivered by this engine",
            "finalText":"also unverified",
            "final_text":"ditto"
        }});
        assert_eq!(completed_text(&legacy), None,
            "speculative legacy keys must stay unread, matching the verified wire contract");
        let empty = json!({"method":"turn/completed","params":{
            "last_agent_message":"   "
        }});
        assert_eq!(completed_text(&empty), None, "whitespace-only tail text is not a real final");
        // Wire-shape items win over any legacy key.
        let both = json!({"params":{
            "last_agent_message":"journal text",
            "turn":{"items":[{"type":"agentMessage","phase":"final_answer","text":"wire text"}]},
        }});
        assert_eq!(completed_text(&both).as_deref(), Some("wire text"));
    }

    #[test]
    fn codex_options_default_to_workspace_write_network_auto_review() {
        let options = CodexOptions::parse(&toml::Table::new()).unwrap();
        assert_eq!(options.sandbox, CodexSandbox::WorkspaceWrite);
        assert!(options.network_access);
        assert_eq!(options.approval_policy, CodexApprovalPolicy::OnRequest);
        assert_eq!(options.approval_review, CodexApprovalReview::Auto);
        assert_eq!(options.wire_thread_sandbox(), "workspace-write");
        assert_eq!(options.wire_approval_policy(), "on-request");
        assert_eq!(options.wire_approvals_reviewer(), "auto_review");
        assert_eq!(options.wire_sandbox_policy(),
            json!({"type": "workspaceWrite", "networkAccess": true}));
    }

    #[test]
    fn codex_options_parse_each_supported_key_and_reject_others() {
        let options = CodexOptions::parse(&table(&[
            ("sandbox", "read-only".into()),
            ("network_access", false.into()),
            ("approval_policy", "on-request".into()),
            ("approval_review", "auto".into()),
        ])).unwrap();
        assert_eq!(options.sandbox, CodexSandbox::ReadOnly);
        assert!(!options.network_access);
        assert_eq!(options.approval_policy, CodexApprovalPolicy::OnRequest);
        assert_eq!(options.approval_review, CodexApprovalReview::Auto);
        assert_eq!(options.wire_approvals_reviewer(), "auto_review");
        assert_eq!(options.wire_sandbox_policy(),
            json!({"type": "readOnly", "networkAccess": false}));
        for invalid in [
            table(&[("bogus", "x".into())]),
            table(&[("sandbox", "bogus".into())]),
            table(&[("sandbox", 1.into())]),
            table(&[("network_access", "true".into())]),
            table(&[("approval_policy", "auto".into())]),
            table(&[("approval_review", "everyone".into())]),
            table(&[("reasoning_effort", "bogus".into())]),
            table(&[("reasoning_effort", 3.into())]),
        ] {
            assert!(CodexOptions::parse(&invalid).is_err(), "accepted {invalid:?}");
        }
    }

    #[test]
    fn codex_options_reasoning_effort_only_sends_when_set() {
        let options = CodexOptions::parse(&toml::Table::new()).unwrap();
        assert_eq!(options.reasoning_effort, None, "defaults leave the engine-level effort untouched");
        for (value, expected) in [
            ("minimal", "minimal"), ("low", "low"), ("medium", "medium"),
            ("high", "high"), ("xhigh", "xhigh"),
        ] {
            let parsed = CodexOptions::parse(&table(&[("reasoning_effort", value.into())])).unwrap();
            assert_eq!(parsed.reasoning_effort, Some(expected));
        }
        let parsed = CodexOptions::parse(&table(&[("reasoning_effort", " high ".into())])).unwrap();
        assert_eq!(parsed.reasoning_effort, Some("high"), "surrounding whitespace is trimmed");
    }

    #[test]
    fn codex_options_reject_dead_approval_combinations() {
        let auto_never = table(&[("approval_policy", "never".into()),
            ("approval_review", "auto".into())]);
        let error = CodexOptions::parse(&auto_never).unwrap_err();
        assert!(error.contains("approval_review='auto' requires") && error.contains("never"), "{error}");
        let user_asks = table(&[("approval_policy", "on-request".into()),
            ("approval_review", "user".into())]);
        let error = CodexOptions::parse(&user_asks).unwrap_err();
        assert!(error.contains("HITL") && error.contains("does not implement"), "{error}");
        // Missing reviewer inherits automatic review; explicit never remains usable.
        let unset_review = table(&[("approval_policy", "on-request".into())]);
        assert_eq!(CodexOptions::parse(&unset_review).unwrap().approval_review, CodexApprovalReview::Auto);
        let never = CodexOptions::parse(&table(&[("approval_policy", "never".into())])).unwrap();
        assert_eq!(never.approval_policy, CodexApprovalPolicy::Never);
        assert_eq!(never.approval_review, CodexApprovalReview::User);
        assert!(CodexOptions::parse(&table(&[
            ("approval_policy", "on-request".into()), ("approval_review", "auto".into()),
        ])).is_ok());
    }

    #[test]
    fn codex_options_danger_full_access_wire_omits_network() {
        let options = CodexOptions::parse(&table(&[("sandbox", "danger-full-access".into())])).unwrap();
        assert_eq!(options.sandbox, CodexSandbox::DangerFullAccess);
        assert_eq!(options.wire_sandbox_policy(), json!({"type": "dangerFullAccess"}));
        assert!(CodexOptions::parse(&table(&[
            ("sandbox", "danger-full-access".into()), ("network_access", false.into()),
        ])).is_ok(), "explicit network_access normalizes with a warning");
    }

    fn options_req(cwd: &str) -> TurnRequest {
        struct TestObserver;
        #[async_trait::async_trait]
        impl crate::engine::SessionObserver for TestObserver {
            async fn established(&self, _: &str) -> Result<(), String> { Ok(()) }
        }
        TurnRequest::new("run-1", "prompt", cwd, Arc::new(TestObserver))
    }

    #[test]
    fn wire_params_follow_resolved_options_and_keep_bridge_identifiers() {
        let options = CodexOptions::parse(&toml::Table::new()).unwrap();
        let req = options_req("/tmp/x");
        let thread = thread_start_params(&req, options);
        assert_eq!(thread["sandbox"], "workspace-write");
        assert_eq!(thread["approvalPolicy"], "on-request");
        assert_eq!(thread["approvalsReviewer"], "auto_review");
        assert_eq!(thread["threadSource"], "bridge-provider");
        assert_eq!(thread["ephemeral"], false);
        assert!(thread.get("model").is_none());
        let resume = thread_resume_params(&req, "t-1", options);
        assert_eq!(resume["sandbox"], "workspace-write");
        assert_eq!(resume["threadId"], "t-1");
        assert_eq!(resume["approvalPolicy"], "on-request");
        assert_eq!(resume["approvalsReviewer"], "auto_review");
        let turn = turn_start_params(&req, "t-1", options);
        assert_eq!(turn["sandboxPolicy"], json!({"type": "workspaceWrite", "networkAccess": true}));
        assert_eq!(turn["approvalPolicy"], "on-request");
        assert_eq!(turn["approvalsReviewer"], "auto_review");
        assert_eq!(turn["clientUserMessageId"], "run-1");
        let read_only = CodexOptions::parse(&table(&[("sandbox", "read-only".into()),
            ("network_access", false.into())])).unwrap();
        let turn = turn_start_params(&req, "t-1", read_only);
        assert_eq!(turn["sandboxPolicy"], json!({"type": "readOnly", "networkAccess": false}));
        let banned = CodexOptions::parse(&table(&[("sandbox", "danger-full-access".into())])).unwrap();
        assert_eq!(turn_start_params(&req, "t-1", banned)["sandboxPolicy"],
            json!({"type": "dangerFullAccess"}));
        assert_eq!(thread_start_params(&req, banned)["sandbox"], "danger-full-access");
    }

    #[test]
    fn wire_params_carry_reasoning_effort_only_when_configured() {
        let defaults = CodexOptions::parse(&toml::Table::new()).unwrap();
        let req = options_req("/tmp/x");
        for params in [thread_start_params(&req, defaults),
            thread_resume_params(&req, "t-1", defaults), turn_start_params(&req, "t-1", defaults)] {
            assert!(params.get("effort").is_none(), "no effort key without an override: {params}");
        }
        let with_effort = CodexOptions::parse(&table(&[
            ("reasoning_effort", "medium".into()),
        ])).unwrap();
        for params in [thread_start_params(&req, with_effort),
            thread_resume_params(&req, "t-1", with_effort), turn_start_params(&req, "t-1", with_effort)] {
            assert_eq!(params["effort"], "medium");
        }
    }
