use super::mapper::*;
use super::permission::*;
    use super::*;
    use bcs_protocol::stream::{AgentData, ChatState, StreamEvent};

    #[test]
    fn answered_ask_user_response_delivers_answers_as_denial_message() {
        use serde_json::json;
        let resolution = json!({"action":"submit","answers":{
            "q1":{"values":["静态"]},
            "q2":{"values":["a","b"]},
            "q3":{"values":[]}
        }});
        let response = answered_ask_user_response("req-1", &resolution);
        assert_eq!(response["type"], "control_response");
        assert_eq!(response["response"]["subtype"], "success");
        assert_eq!(response["response"]["request_id"], "req-1");
        let message = response["response"]["response"]["message"].as_str().unwrap();
        assert!(response["response"]["response"]["behavior"] == "deny",
            "the message-carrying answer rides on a denial");
        assert!(message.starts_with(ANSWERED_ASK_USER_MARK), "signed for transcription lifting");
        assert!(message.contains("- q1: 静态"));
        assert!(message.contains("- q2: a; b"), "multi-select answers are joined");
        assert!(message.contains("- q3: (unanswered)"), "an explicit skip stays visible");
        // Without an answers object the message still guides the model onward.
        let response = answered_ask_user_response("req-2", &json!({"action":"submit"}));
        assert!(response["response"]["response"]["message"]
            .as_str().unwrap().contains("without answers"));
    }

    #[test]
    fn answered_ask_user_tool_result_lifts_the_error_flag() {
        // The CLI renders a denial as {content: our message, is_error: true};
        // the transcription surfaces exactly these as a normal answered row.
        let marked = json!({"type":"user","message":{"content":[
            {"type":"tool_result","tool_use_id":"t1",
             "content":"The user answered this question card; adopt these answers:\n- q1: 静态",
             "is_error":true}]}}).to_string();
        match map_cc_line(&marked, "run-1") {
            CcMap::Events(events) => match &events[0] {
                StreamEvent::Agent(agent) => match &agent.data {
                    AgentData::Tool(tool) => {
                        assert_eq!(tool.is_error, Some(false), "our answer text is not a failure");
                        assert_eq!(tool.tool_call_id.as_deref(), Some("t1"));
                    }
                    other => panic!("expected tool event, got {other:?}"),
                },
                other => panic!("expected agent event, got {other:?}"),
            },
            other => panic!("expected events, got {other:?}"),
        }
        // A genuine failure retains its error flag.
        let failed = json!({"type":"user","message":{"content":[
            {"type":"tool_result","tool_use_id":"t2",
             "content":"command exited 1","is_error":true}]}}).to_string();
        match map_cc_line(&failed, "run-1") {
            CcMap::Events(events) => match &events[0] {
                StreamEvent::Agent(agent) => match &agent.data {
                    AgentData::Tool(tool) => assert_eq!(tool.is_error, Some(true)),
                    other => panic!("expected tool event, got {other:?}"),
                },
                other => panic!("expected agent event, got {other:?}"),
            },
            other => panic!("expected events, got {other:?}"),
        }
    }

    #[test]
    fn maps_cc_ndjson_turn() {
        let lines: Vec<String> = std::fs::read_to_string(
            concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/cc_turn.ndjson"))
            .unwrap().lines().map(str::to_string).collect();
        let mut session_id = None;
        let mut deltas = String::new();
        let mut tools = 0;
        let mut final_text = None;
        for line in &lines {
            match map_cc_line(line, "r-1") {
                CcMap::SessionId(s) => session_id = Some(s),
                CcMap::Events(events) => for ev in events {
                    match ev {
                        StreamEvent::Chat(c) if c.state == ChatState::Delta =>
                            deltas.push_str(&c.delta_text.unwrap()),
                        StreamEvent::Agent(_) => tools += 1,
                        _ => {}
                    }
                },
                CcMap::Final(text) => final_text = Some(text),
                CcMap::Failed(_) => {}
                CcMap::ControlRequest { .. } => {}
                CcMap::BackgroundStarted { .. }
                | CcMap::BackgroundEnqueued { .. }
                | CcMap::BackgroundDelivered { .. } => {}
                CcMap::Ignore | CcMap::Malformed => {}
            }
        }
        assert_eq!(session_id.as_deref(), Some("cc-sess-1"));
        assert_eq!(deltas, "正在分析");
        assert_eq!(tools, 2);
        assert_eq!(final_text.as_deref(), Some("完成了"));
    }

    #[test]
    fn malformed_json_is_malformed() {
        assert!(matches!(map_cc_line("not json", "r-1"), CcMap::Malformed));
        assert!(matches!(map_cc_line("{", "r-1"), CcMap::Malformed));
    }

    #[test]
    fn unrecognized_type_is_ignore() {
        let line = r#"{"type":"mystery","payload":42}"#;
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn json_without_type_is_ignore() {
        let line = r#"{"hello":"world"}"#;
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn non_success_result_is_failed_with_sanitized_note() {
        // error subtype 标识符被保留（引擎错误类别，非用户数据）。
        let line = r#"{"type":"result","subtype":"error_max_cycles","result":"secret detail"}"#;
        match map_cc_line(line, "r-1") {
            CcMap::Failed(note) => {
                assert!(note.contains("error_max_cycles"), "note: {note}");
                // 不携带原始 result payload。
                assert!(!note.contains("secret detail"), "note leaked payload: {note}");
            }
            other => panic!("expected Failed, got {other:?}"),
        }
    }

    #[test]
    fn empty_result_text_success_maps_to_empty_final() {
        let line = r#"{"type":"result","subtype":"success","result":""}"#;
        match map_cc_line(line, "r-1") {
            CcMap::Final(text) => assert_eq!(text, ""),
            other => panic!("expected Final, got {other:?}"),
        }
    }

    #[test]
    fn error_flag_does_not_acknowledge_a_successful_turn() {
        let line = r#"{"type":"result","subtype":"success","is_error":true,"result":"private upstream error"}"#;
        match map_cc_line(line, "r-1") {
            CcMap::Failed(note) => assert!(!note.contains("private upstream error")),
            other => panic!("an error result must not consume pending injects: {other:?}"),
        }
    }

    #[test]
    fn result_error_reports_safe_upstream_causes() {
        let cases = [
            (json!({"is_error":true,"subtype":"success","api_error_status":401,
                "result":"Failed to authenticate. API Error: 401 review synthetic authentication failure"}),
                "upstream authentication_error (HTTP 401): model provider authentication failed; check credentials"),
            (json!({"is_error":true,"subtype":"success","api_error_status":429,"result":"PRIVATE detail"}),
                "upstream rate_limit_error (HTTP 429): model provider rate limit exceeded; retry later"),
            (json!({"is_error":true,"subtype":"success","api_error_status":401,
                "error":{"type":"rate_limit_error","message":"PRIVATE detail"},
                "result":"API Error: 503 PRIVATE detail"}),
                "upstream authentication_error (HTTP 401): model provider authentication failed; check credentials"),
            (json!({"is_error":true,"subtype":"success",
                "result":"API Error: Request rejected (429) · provider rate limit; PRIVATE credential"}),
                "upstream rate_limit_error (HTTP 429): model provider rate limit exceeded; retry later"),
            (json!({"is_error":true,"subtype":"error_during_execution",
                "errors":["PRIVATE detail", "API Error: 401 invalid credential PRIVATE"]}),
                "upstream authentication_error (HTTP 401): model provider authentication failed; check credentials"),
            (json!({"subtype":"error_during_execution",
                "error":{"type":"overloaded_error","message":"PRIVATE detail"}}),
                "upstream overloaded_error (HTTP 529): model provider overloaded; retry later"),
            (json!({"is_error":true,"subtype":"success",
                "errors":[{"error":{"type":"rate_limit_error","message":"PRIVATE detail"}}]}),
                "upstream rate_limit_error (HTTP 429): model provider rate limit exceeded; retry later"),
            (json!({"is_error":true,"subtype":"success",
                "errors":[{"status":503,"message":"PRIVATE detail"}]}),
                "upstream service_unavailable (HTTP 503): model provider unavailable; retry later"),
            (json!({"is_error":true,"subtype":"success",
                "errors":[r#"{"error":{"type":"rate_limit_error","message":"PRIVATE detail"}}"#]}),
                "upstream rate_limit_error (HTTP 429): model provider rate limit exceeded; retry later"),
        ];
        for (mut event, expected) in cases {
            event["type"] = json!("result");
            match map_cc_line(&event.to_string(), "r-error") {
                CcMap::Failed(note) => assert_eq!(note, expected),
                other => panic!("expected safe error, got {other:?}"),
            }
        }
    }

    #[test]
    fn result_error_invalid_api_status_preserves_existing_fallbacks() {
        for status in [json!(null), json!("401"), json!(-1), json!(401.5), json!(true),
            json!({}), json!([]), json!(200), json!(418), json!(4010)] {
            let mut event = json!({"type":"result","is_error":true,"subtype":"success",
                "api_error_status":status,"result":"PRIVATE detail"});
            match map_cc_line(&event.to_string(), "r-error") {
                CcMap::Failed(note) => assert_eq!(note, "engine_result_error"),
                other => panic!("expected generic error for {event}, got {other:?}"),
            }
            event["result"] = json!("API Error: 429 PRIVATE detail");
            match map_cc_line(&event.to_string(), "r-error") {
                CcMap::Failed(note) => assert_eq!(note,
                    "upstream rate_limit_error (HTTP 429): model provider rate limit exceeded; retry later"),
                other => panic!("expected text fallback for {event}, got {other:?}"),
            }
        }
    }

    #[test]
    fn result_error_does_not_guess_from_private_or_successful_content() {
        for text in ["private document mentions 429", "API Error: 4290 private", "PRIVATE secret"] {
            let event = json!({"type":"result","is_error":true,"subtype":"success","result":text});
            match map_cc_line(&event.to_string(), "r-error") {
                CcMap::Failed(note) => assert_eq!(note, "engine_result_error"),
                other => panic!("expected generic error, got {other:?}"),
            }
        }
        for status in [json!(null), json!(401)] {
            let text = "API Error: Request rejected (429) is an example, not a failure";
            let event = json!({"type":"result","subtype":"success","is_error":false,
                "api_error_status":status,"result":text});
            match map_cc_line(&event.to_string(), "r-ok") {
                CcMap::Final(result) => assert_eq!(result, text),
                other => panic!("expected unchanged successful result, got {other:?}"),
            }
        }
        let event = json!({"type":"result","subtype":"error_max_turns","is_error":true,
            "api_error_status":418,"errors":["PRIVATE"]});
        match map_cc_line(&event.to_string(), "r-error") {
            CcMap::Failed(note) => assert_eq!(note, "engine result subtype: error_max_turns"),
            other => panic!("expected native error category, got {other:?}"),
        }
    }

    #[test]
    fn control_request_can_use_tool_maps_to_control_request() {
        // 不再发占位 thinking（Task 9）——Task 12 接线为 ControlRequest 载体，
        // 由 run_turn 驱动 HITL 交互；绝不发 `stream:"approval"`（spec §2）。
        let line = r#"{"type":"control_request","request_id":"req-7","request":{"subtype":"can_use_tool","tool_name":"Bash","input":{"command":"ls"}}}"#;
        match map_cc_line(line, "r-1") {
            CcMap::ControlRequest { request_id, tool_name, input } => {
                assert_eq!(request_id, "req-7");
                assert_eq!(tool_name, "Bash");
                assert_eq!(input["command"], json!("ls"));
            }
            other => panic!("expected ControlRequest, got {other:?}"),
        }
    }

    #[test]
    fn invalid_permission_requests_fail_instead_of_parking_an_unresolvable_interaction() {
        let valid = json!({
            "type": "control_request", "request_id": "req-1",
            "request": { "subtype": "can_use_tool", "tool_name": "Bash",
                         "input": { "command": "ls" } }
        });
        for invalid_id in [Value::Null, json!(""), json!(" "), json!(7)] {
            let mut request = valid.clone();
            request["request_id"] = invalid_id;
            // A nested ID must never substitute for the engine's top-level ID.
            request["request"]["request_id"] = json!("nested-id");
            assert!(matches!(map_cc_line(&request.to_string(), "r-1"), CcMap::Failed(_)));
        }
        for (field, value) in [("tool_name", json!("")), ("tool_name", Value::Null),
                               ("input", Value::Null), ("input", json!([]))] {
            let mut request = valid.clone();
            request["request"][field] = value;
            assert!(matches!(map_cc_line(&request.to_string(), "r-1"), CcMap::Failed(_)));
        }
    }

    #[test]
    fn build_requested_extra_plan_mode_exit_reads_as_confirmation_with_plan_text() {
        // The plan-mode exit gate shows a human title and the proposed plan
        // verbatim instead of the tool name plus a truncated JSON rendering.
        let extra = build_requested_extra("ExitPlanMode", &json!({"plan":"1. 找接口\n2. 写迁移"}));
        assert_eq!(extra["title"], json!("Exit plan mode"));
        assert_eq!(extra["description"], json!("1. 找接口\n2. 写迁移"));
        assert!(extra.get("command").is_none());
        assert_eq!(extra["options"][0]["decision"], json!("allow-once"));
        assert_eq!(extra["options"][1]["decision"], json!("deny"));
        // Without plan text the generic exec description fallback still applies.
        let extra = build_requested_extra("ExitPlanMode", &json!({}));
        assert_eq!(extra["title"], json!("Exit plan mode"));
        assert!(extra["description"].is_string());
        // The snake_case spelling hits the same branch.
        let extra = build_requested_extra("exit_plan_mode", &json!({"plan":"步骤"}));
        assert_eq!(extra["title"], json!("Exit plan mode"));
        assert_eq!(extra["description"], json!("步骤"));
    }

    #[test]
    fn build_requested_extra_exec_carries_title_command_and_fixed_options() {
        let extra = build_requested_extra("Bash", &json!({"command":"ls -la"}));
        assert_eq!(extra["title"], json!("Bash"));
        assert_eq!(extra["command"], json!("ls -la"));
        assert_eq!(extra["options"][0]["decision"], json!("allow-once"));
        assert_eq!(extra["options"][0]["label"], json!("Allow once"));
        assert_eq!(extra["options"][1]["decision"], json!("deny"));
        assert_eq!(extra["options"][1]["label"], json!("Deny"));
    }

    #[test]
    fn build_requested_extra_exec_omits_command_when_missing() {
        // BCS rejects a present-but-null `command` (interaction dropped → run
        // parks forever), so when input has no `command` string we MUST NOT emit
        // the key; we synthesize a `description` from `path`/`file_path` instead.
        let extra = build_requested_extra("Read", &json!({"path":"/a"}));
        assert_eq!(extra["title"], json!("Read"));
        assert!(
            extra.get("command").is_none(),
            "command key must be absent when input has no command string"
        );
        assert_eq!(extra["description"], json!("/a"), "description synthesized from path");
    }

    #[test]
    fn build_requested_extra_exec_emits_bcs_valid_shape() {
        // BCS-side validation rule for what we emit on an exec `requested` extra:
        // if the `command` key is present it must be a non-empty string, and
        // `options` must be a non-empty array where each option has a string
        // `decision` and `label`. Holds for every shape we build.
        fn assert_bcs_exec_valid(extra: &Value) {
            if let Some(cmd) = extra.get("command") {
                assert!(cmd.is_string(), "command must be a string when present: {extra}");
                let s = cmd.as_str().unwrap();
                assert!(!s.is_empty(), "command must be non-empty when present: {extra}");
            }
            let options = extra
                .get("options")
                .and_then(|o| o.as_array())
                .expect("options must be a non-empty array");
            assert!(!options.is_empty(), "options must be non-empty: {extra}");
            for o in options {
                assert!(
                    o.get("decision").and_then(|v| v.as_str()).is_some(),
                    "each option needs a string decision: {extra}"
                );
                assert!(
                    o.get("label").and_then(|v| v.as_str()).is_some(),
                    "each option needs a string label: {extra}"
                );
            }
        }

        // Non-empty string command → command key present.
        assert_bcs_exec_valid(&build_requested_extra("Bash", &json!({"command":"ls -la"})));
        // Missing command → description shape (no command key).
        assert_bcs_exec_valid(&build_requested_extra("Read", &json!({"path":"/a"})));
        // Empty-string command → treated as no command (description shape).
        assert_bcs_exec_valid(&build_requested_extra("Bash", &json!({"command":""})));
        // Null command → treated as no command (description shape).
        assert_bcs_exec_valid(&build_requested_extra("Bash", &json!({"command":null})));
        // No path/file_path either → description falls back to compact JSON.
        let extra = build_requested_extra("Bash", &json!({"foo":"bar"}));
        assert!(extra.get("command").is_none(), "no command key when command absent");
        assert_bcs_exec_valid(&extra);
        assert!(extra["description"].as_str().unwrap().contains("bar"));
    }

    #[test]
    fn synthesize_exec_description_prefers_path_then_falls_back() {
        // path wins over file_path and JSON fallback.
        assert_eq!(synthesize_exec_description(&json!({"path":"/a/b","file_path":"/c"})), "/a/b");
        // file_path used when path absent.
        assert_eq!(synthesize_exec_description(&json!({"file_path":"/c"})), "/c");
        // empty path string falls through to file_path, then JSON.
        assert_eq!(synthesize_exec_description(&json!({"path":"","file_path":"/c"})), "/c");
        // empty path + no file_path → JSON fallback keeps the empty `path` key.
        assert_eq!(synthesize_exec_description(&json!({"path":""})), r#"{"path":""}"#);
        assert_eq!(synthesize_exec_description(&json!({})), "{}");
        // UTF-8 safe truncation at 200 chars on multibyte text (no mid-char slice).
        let big = json!({ "k": "你".repeat(300) });
        let compact = serde_json::to_string(&big).unwrap();
        let desc = synthesize_exec_description(&big);
        assert!(desc.chars().count() <= 200, "desc must be at most 200 chars: {}", desc.chars().count());
        // desc is a char-boundary prefix of the compact JSON — a multi-byte char
        // is never split (Rust str invariant + `char_indices` truncation).
        assert!(compact.starts_with(&desc), "desc must be a char-boundary prefix: {desc:?}");
    }

    #[test]
    fn build_requested_extra_ask_user_maps_questions_label_to_value() {
        let input = json!({
            "questions": [
                { "header": "lang", "question": "Pick a language", "multiSelect": false,
                  "options": [ {"label":"Rust"}, {"label":"Go"} ] },
                { "question": "Free text?", "options": [ {"label":"yes"} ] },
            ]
        });
        let extra = build_requested_extra("AskUserQuestion", &input);
        let qs = &extra["questions"];
        assert_eq!(qs[0]["questionId"], json!("lang"), "header → questionId");
        assert_eq!(qs[0]["question"], json!("Pick a language"));
        assert_eq!(qs[0]["multiSelect"], json!(false));
        assert_eq!(qs[0]["options"][0], json!({"label":"Rust","value":"Rust"}));
        // 缺 header → question_N（1-based）
        assert_eq!(qs[1]["questionId"], json!("question_2"));
        assert_eq!(qs[1]["options"][0], json!({"label":"yes","value":"yes"}));
    }

    #[test]
    fn ask_user_question_with_secret_at_question_is_refused() {
        let input = json!({"questions":[{"secret":true,"question":"pwd"}]});
        assert!(ask_user_has_secret(&input));
    }

    #[test]
    fn ask_user_question_with_is_secret_at_call_level_is_refused() {
        let input = json!({"isSecret":true,"questions":[{"question":"pwd"}]});
        assert!(ask_user_has_secret(&input));
    }

    #[test]
    fn ask_user_question_without_secret_is_not_refused() {
        let input = json!({"questions":[{"question":"name","options":[{"label":"a"}]}]});
        assert!(!ask_user_has_secret(&input));
    }

    #[test]
    fn resolution_to_behavior_maps_exec_decisions_and_ask_user_actions() {
        // exec: only the offered allow-once decision → allow; everything else (deny,
        // unrecognized/garbage, a bare `allow` not in the allowlist) → deny.
        // Conservative: never infer allow from an unknown decision.
        assert_eq!(resolution_to_behavior(&json!({"decision":"allow-once"})), "allow");
        for decision in ["allow_once", "allow_session", "allow_persistent", "allow_always", "allow-always"] {
            assert_eq!(resolution_to_behavior(&json!({"decision":decision})), "deny");
        }
        assert_eq!(resolution_to_behavior(&json!({"decision":"deny"})), "deny");
        assert_eq!(
            resolution_to_behavior(&json!({"decision":"yes"})),
            "deny",
            "unknown decision → deny"
        );
        assert_eq!(
            resolution_to_behavior(&json!({"decision":"allow"})),
            "deny",
            "bare 'allow' (not in allowlist) → deny"
        );
        assert_eq!(resolution_to_behavior(&json!({"decision":"garbage"})), "deny");
        // ask_user follows the BCS workbench contract: submit with a
        // questionId -> {values} object allows (empty values entries are
        // explicit skips BCS already accepted); cancel and anything else deny.
        assert_eq!(
            resolution_to_behavior(&json!({
                "action":"submit",
                "answers":{"技术栈":{"values":[]},"规模频率":{"values":["大规模长期爬取"]}}
            })),
            "allow",
            "a real workbench submission, including an unanswered (empty) question"
        );
        assert_eq!(
            resolution_to_behavior(&json!({"action":"submit","answers":{}})),
            "deny",
            "submit without an answers object is not a valid submission"
        );
        assert_eq!(
            resolution_to_behavior(&json!({"action":"submit"})),
            "deny",
            "missing answers → deny"
        );
        assert_eq!(resolution_to_behavior(&json!({"action":"cancel"})), "deny");
        assert_eq!(
            resolution_to_behavior(&json!({"action":"answer","answers":[{"q":"a"}]})),
            "deny",
            "the never-shipped v1 shape must not silently allow"
        );
        assert_eq!(resolution_to_behavior(&json!(null)), "deny", "unknown shape → deny");
    }

    #[test]
    fn non_can_use_tool_control_request_is_ignore() {
        let line = r#"{"type":"control_request","request":{"subtype":"other"}}"#;
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn assistant_tool_use_carries_id_name_and_input() {
        let line = r#"{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_9","name":"Read","input":{"path":"/a"}}]}}"#;
        match map_cc_line(line, "r-9") {
            CcMap::Events(evs) => {
                assert_eq!(evs.len(), 1);
                match &evs[0] {
                    StreamEvent::Agent(a) => match &a.data {
                        AgentData::Tool(t) => {
                            assert_eq!(t.phase, ToolPhase::Start);
                            assert_eq!(t.name.as_deref(), Some("Read"));
                            assert_eq!(t.tool_call_id.as_deref(), Some("toolu_9"));
                            assert_eq!(t.args.as_ref().unwrap()["path"], serde_json::json!("/a"));
                        }
                        other => panic!("expected Tool, got {other:?}"),
                    },
                    other => panic!("expected Agent(Tool), got {other:?}"),
                }
            }
            other => panic!("expected Events, got {other:?}"),
        }
    }

    #[test]
    fn user_tool_result_carries_tool_use_id_and_content_zero_loss() {
        let line = r#"{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_9","content":[{"type":"text","text":"ok"}]}]}}"#;
        match map_cc_line(line, "r-9") {
            CcMap::Events(evs) => {
                assert_eq!(evs.len(), 1);
                match &evs[0] {
                    StreamEvent::Agent(a) => match &a.data {
                        AgentData::Tool(t) => {
                            assert_eq!(t.phase, ToolPhase::Result);
                            assert_eq!(t.tool_call_id.as_deref(), Some("toolu_9"));
                            assert_eq!(t.result.as_ref().unwrap()[0]["text"], serde_json::json!("ok"));
                        }
                        other => panic!("expected Tool, got {other:?}"),
                    },
                    other => panic!("expected Agent(Tool), got {other:?}"),
                }
            }
            other => panic!("expected Events, got {other:?}"),
        }
    }

    #[test]
    fn assistant_without_tool_use_is_ignore() {
        let line = r#"{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}"#;
        // 文本块由 stream_event 的 deltas 承载；assistant 非工具块不重复映射。
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn user_tool_result_preserves_error_flags_and_defaults_only_when_absent() {
        for (flag, expected) in [
            (None, Some(false)),
            (Some(json!(false)), Some(false)),
            (Some(json!(true)), Some(true)),
            (Some(json!(null)), None),
            (Some(json!("false")), None),
            (Some(json!(0)), None),
        ] {
            let mut item = json!({"type":"tool_result","tool_use_id":"toolu_9","content":"ok"});
            if let Some(flag) = &flag {
                item["is_error"] = flag.clone();
            }
            let line = json!({"type":"user","message":{"content":[item]}}).to_string();
            let CcMap::Events(events) = map_cc_line(&line, "r-9") else {
                panic!("expected tool result");
            };
            let StreamEvent::Agent(agent) = &events[0] else { panic!("expected agent"); };
            let AgentData::Tool(tool) = &agent.data else {
                panic!("expected tool");
            };
            assert_eq!(tool.is_error, expected, "source is_error: {flag:?}");
            assert_eq!(tool.result, Some(json!("ok")));
        }
    }

    fn mapped_tools(mapper: &mut CcTurnMapper<'_>, line: Value) -> Vec<ToolData> {
        let CcMap::Events(events) = mapper.map_line(&line.to_string()) else {
            panic!("expected tool events");
        };
        events.into_iter().map(|event| {
            let StreamEvent::Agent(agent) = event else { panic!("expected agent"); };
            let AgentData::Tool(tool) = agent.data else {
                panic!("expected tool");
            };
            tool
        }).collect()
    }

    fn tool_start(id: &str, name: &str) -> Value {
        json!({"type":"assistant","message":{"content":[
            {"type":"tool_use","id":id,"name":name,"input":{}}
        ]}})
    }

    fn tool_result(id: &str) -> Value {
        json!({"type":"user","message":{"content":[
            {"type":"tool_result","tool_use_id":id,"content":{"tool":"untrusted-name"}}
        ]}})
    }

    #[test]
    fn tool_names_are_isolated_between_runs_even_when_ids_are_reused() {
        let mut first = CcTurnMapper::new("run-1");
        let mut second = CcTurnMapper::new("run-2");
        mapped_tools(&mut first, tool_start("same-id", "first-tool"));
        assert_eq!(mapped_tools(&mut second, tool_result("same-id"))[0].name, None);
        mapped_tools(&mut second, tool_start("same-id", "second-tool"));
        assert_eq!(mapped_tools(&mut first, tool_result("same-id"))[0].name.as_deref(), Some("first-tool"));
        assert_eq!(mapped_tools(&mut second, tool_result("same-id"))[0].name.as_deref(), Some("second-tool"));
    }

    #[test]
    fn orphan_and_duplicate_results_do_not_inherit_another_tool_name() {
        let mut mapper = CcTurnMapper::new("run-1");
        mapped_tools(&mut mapper, tool_start("known-id", "known-tool"));
        assert_eq!(mapped_tools(&mut mapper, tool_result("unknown-id"))[0].name, None);
        assert_eq!(mapped_tools(&mut mapper, tool_result("known-id"))[0].name.as_deref(), Some("known-tool"));
        assert_eq!(mapped_tools(&mut mapper, tool_result("known-id"))[0].name, None);
    }

    #[test]
    fn conflicting_tool_names_for_an_id_are_not_guessed() {
        let mut mapper = CcTurnMapper::new("run-1");
        mapped_tools(&mut mapper, tool_start("same-id", "first-tool"));
        mapped_tools(&mut mapper, tool_start("same-id", "second-tool"));
        mapped_tools(&mut mapper, tool_start("same-id", "first-tool"));
        assert_eq!(mapped_tools(&mut mapper, tool_result("same-id"))[0].name, None);
    }

    #[test]
    fn repeated_matching_starts_preserve_the_name_but_blank_fields_do_not() {
        let mut mapper = CcTurnMapper::new("run-1");
        mapped_tools(&mut mapper, tool_start("same-id", "known-tool"));
        mapped_tools(&mut mapper, tool_start("same-id", "known-tool"));
        assert_eq!(mapped_tools(&mut mapper, tool_result("same-id"))[0].name.as_deref(), Some("known-tool"));
        for (id, name) in [("", "known-tool"), (" ", "known-tool"), ("id", ""), ("id", " ")] {
            mapped_tools(&mut mapper, tool_start(id, name));
            assert_eq!(mapped_tools(&mut mapper, tool_result(id))[0].name, None);
        }
    }

    #[test]
    fn system_non_init_is_ignore() {
        let line = r#"{"type":"system","subtype":"other","session_id":"s"}"#;
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn stream_event_non_text_delta_is_ignore() {
        let line = r#"{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"input_json_delta","partial_json":"{"}}}"#;
        assert!(matches!(map_cc_line(line, "r-1"), CcMap::Ignore));
    }

    #[test]
    fn stream_event_thinking_delta_emits_thinking_event() {
        let line = r#"{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"分析中"}}}"#;
        match map_cc_line(line, "r-1") {
            CcMap::Events(events) => match &events[0] {
                StreamEvent::Agent(agent) => match &agent.data {
                    AgentData::Thinking(thinking) => {
                        assert_eq!(thinking.delta.as_deref(), Some("分析中"));
                    }
                    other => panic!("expected Thinking, got {other:?}"),
                },
                other => panic!("expected Agent, got {other:?}"),
            },
            other => panic!("expected Events, got {other:?}"),
        }
    }

    // -- 后台任务（async subagent / background bash）的 turn 续接 ------------

    struct NullObserver;
    #[async_trait::async_trait]
    impl SessionObserver for NullObserver {
        async fn established(&self, _: &str) -> Result<(), String> { Ok(()) }
    }

    /// Drive a full `run_turn` against a mock Claude Code script.

#[path = "background_tests.rs"]
mod background;
