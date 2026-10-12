use axum::http::StatusCode;
use futures::StreamExt;
use serde_json::json;

mod support; // tests/support/mod.rs：spawn_app(config_toml: &str) -> String(base_url)

#[tokio::test]
async fn ping_requires_auth_and_matching_provider() {
    let url = support::spawn_app(r#"
provider_id = "bridge-1"
listen = "127.0.0.1:0"
bcs_to_provider_token = "tok-b2p"
[[bot]]
provider_bot_ref = "cc-worker"
engine = "claude-code"
cwd = "/tmp"
"#).await;
    // Loopback test traffic must not inherit the host's HTTP proxy.
    let client = reqwest::Client::builder().no_proxy()
        .timeout(std::time::Duration::from_secs(5)).build().unwrap();

    // 无 token → 401
    let resp = client.post(format!("{url}/webhook"))
        .json(&json!({"type":"req","id":"1","method":"bot.ping",
                      "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"cc-worker"}}))
        .send().await.unwrap();
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], json!("unauthorized"));

    // provider_id 不匹配 → 403
    let resp = client.post(format!("{url}/webhook"))
        .bearer_auth("tok-b2p")
        .json(&json!({"type":"req","id":"2","method":"bot.ping",
                      "to_bot":{"provider_id":"other","provider_bot_ref":"cc-worker"}}))
        .send().await.unwrap();
    assert_eq!(resp.status(), StatusCode::FORBIDDEN);

    // 未知 method → 501
    let resp = client.post(format!("{url}/webhook"))
        .bearer_auth("tok-b2p")
        .json(&json!({"type":"req","id":"3","method":"chat.explode",
                      "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"cc-worker"}}))
        .send().await.unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_IMPLEMENTED);

    // ping 正常 → 200
    let resp = client.post(format!("{url}/webhook"))
        .bearer_auth("tok-b2p")
        .json(&json!({"type":"req","id":"4","method":"bot.ping",
                      "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"cc-worker"}}))
        .send().await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(resp.json::<serde_json::Value>().await.unwrap()["ok"], json!(true));
}

#[tokio::test]
async fn chat_send_streams_sse_to_final() {
    // mock_cc.sh：读一行 stdin 后回放 cc_turn.ndjson，完整跑一轮到 result/success。
    let url = support::spawn_app_with_mock("mock_cc.sh", "claude-code").await;
    let resp = reqwest::Client::builder().no_proxy().build().unwrap()
        .post(format!("{url}/webhook"))
        .bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&json!({
            "type": "req", "id": "run-1", "method": "chat.send",
            "session_id": "s-1",
            "to_bot": {"provider_id": "bridge-1", "provider_bot_ref": "worker-1"},
            "message": {"role": "user", "content": [{"type": "text", "text": "你好"}]}
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert!(
        resp.headers()["content-type"]
            .to_str()
            .unwrap()
            .starts_with("text/event-stream")
    );
    let text = resp.text().await.unwrap();
    assert!(text.contains("event: agent"), "missing agent tool event: {text}");
    assert!(text.contains("\"state\":\"delta\""), "missing delta state: {text}");
    assert!(text.contains("\"deltaText\":\"正在\""), "missing delta text: {text}");
    assert!(text.contains("\"state\":\"final\""), "missing final state: {text}");
    assert!(text.contains("完成了"), "missing final assistant text: {text}");
    // seq 单调递增
    let seqs = support::extract_seqs(&text);
    assert!(!seqs.is_empty(), "no seq frames: {text}");
    assert!(seqs.windows(2).all(|w| w[0] < w[1]), "seq not monotonic: {seqs:?}");
}

#[tokio::test]
async fn cc_mcp_results_preserve_correlated_names_and_error_flags_in_sse() {
    let url = support::spawn_app_with_mock("mock_cc_mcp_results.sh", "claude-code").await;
    let response = reqwest::Client::builder().no_proxy()
        .timeout(std::time::Duration::from_secs(5)).build().unwrap()
        .post(format!("{url}/webhook"))
        .bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&json!({"type":"req","id":"mcp-run","method":"chat.send",
            "session_id":"mcp-session",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "message":{"role":"user","content":[{"type":"text","text":"coordinate"}]}}))
        .send().await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = response.text().await.unwrap();
    let events: Vec<serde_json::Value> = body.lines()
        .filter_map(|line| line.strip_prefix("data: "))
        .map(|line| serde_json::from_str(line).unwrap()).collect();
    let results: Vec<_> = events.iter().filter(|event| event["phase"] == "result").collect();
    assert_eq!(results.len(), 3);
    for (result, (id, name, is_error, content)) in results.iter().zip([
        ("toolu_send", "mcp__mcp_ant_agentclawscs_bcs_mcp__bcs_send_message", false,
            r#"{"status":"stored","intent_id":"intent-send"}"#),
        ("toolu_assign", "mcp__mcp_ant_agentclawscs_bcs_mcp__bcs_assign_task", false,
            r#"{"status":"stored","intent_id":"intent-assign"}"#),
        ("toolu_error", "mcp__mcp_ant_agentclawscs_bcs_mcp__bcs_assign_task", true,
            "permission denied"),
    ]) {
        assert_eq!(result["toolCallId"], id);
        assert_eq!(result["name"], name, "missing full MCP name: {result}");
        assert_eq!(result["isError"], is_error);
        assert_eq!(result["result"], json!([{"type":"text","text":content}]));
        let start = events.iter().find(|event|
            event["phase"] == "start" && event["toolCallId"] == id).unwrap();
        assert_eq!(result["name"], start["name"]);
        assert_eq!(result["runId"], json!("mcp-run"));
        assert_eq!(result["runId"], start["runId"]);
    }
    assert!(events.iter().any(|event| event["state"] == "final"));
}

#[tokio::test]
async fn concurrent_send_same_session_gets_429() {
    // mock_cc_slow.sh：读 stdin 后 sleep 30 再吐结果，保证第一个 run 仍在执行。
    let url = support::spawn_app_with_mock("mock_cc_slow.sh", "claude-code").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let body = |id: &str| serde_json::json!({
        "type": "req", "id": id, "method": "chat.send",
        "session_id": "s-1",
        "to_bot": {"provider_id": "bridge-1", "provider_bot_ref": "worker-1"},
        "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}
    });

    let first = client
        .post(format!("{url}/webhook"))
        .bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&body("run-a"))
        .send()
        .await
        .unwrap();
    assert_eq!(first.status(), StatusCode::OK, "first run SSE stream must start");

    let second = client
        .post(format!("{url}/webhook"))
        .bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&body("run-b"))
        .send()
        .await
        .unwrap();
    assert_eq!(second.status(), StatusCode::TOO_MANY_REQUESTS);
    let err: serde_json::Value = second.json().await.unwrap();
    assert_eq!(err["error"]["code"], serde_json::json!("rate_limited"));
    assert_eq!(err["error"]["retryable"], serde_json::json!(true));
}

#[tokio::test]
async fn interaction_roundtrip_over_sse_and_resolve_webhook() {
    permission_roundtrip("bash", "allow-once").await;
}

#[tokio::test]
async fn mcp_permission_is_forwarded_and_allow_resumes_the_original_call() {
    permission_roundtrip("mcp", "allow-once").await;
}

#[tokio::test]
async fn unoffered_underscore_permission_decision_does_not_allow_the_tool() {
    permission_roundtrip("mcp", "allow_once").await;
}

#[tokio::test]
async fn mcp_permission_denial_reaches_the_engine() {
    permission_roundtrip("mcp", "deny").await;
}

async fn permission_roundtrip(scenario: &str, decision: &str) {
    // The peer requires the actual CLI flag and validates the complete reply,
    // including the engine request ID, subtype, and allow/deny payload.
    let url = support::spawn_app_with_mock("mock_cc_approval.py", "claude-code").await;
    let client = reqwest::Client::builder().no_proxy()
        .timeout(std::time::Duration::from_secs(5)).build().unwrap();
    let resp = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&json!({"type":"req","id":"run-1","method":"chat.send",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "message":{"role":"user","content":[{"type":"text","text":scenario}]}}))
        .send().await.unwrap();
    assert_eq!(resp.status(), 200);
    let mut stream = resp.bytes_stream();
    let mut acc = String::new();
    // 读到 interaction/requested 帧为止
    let (iid, requested) = loop {
        let chunk = stream.next().await.unwrap_or_else(|| panic!("stream ended before approval: {acc}")).unwrap();
        acc.push_str(&String::from_utf8_lossy(&chunk));
        let requested = acc.lines()
            .filter_map(|line| line.strip_prefix("data: "))
            .filter_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
            .find(|event| event["phase"] == "requested");
        if let Some(requested) = requested {
            let iid = support::extract_first_interaction_id(&acc);
            break (iid, requested);
        }
    };
    assert!(!acc.contains("\"state\":\"final\""), "engine must wait for permission");
    assert_eq!(requested["kind"], json!("exec"));
    assert_eq!(requested["options"][0]["decision"], json!("allow-once"));
    assert_eq!(requested["options"][1]["decision"], json!("deny"));
    if scenario == "mcp" {
        assert_eq!(requested["title"], json!("mcp__coordination__bcs_assign_task"));
        assert!(requested.get("command").is_none());
        assert!(requested["description"].as_str().unwrap().contains("worker-1"));
    } else {
        assert_eq!(requested["command"], json!("printf approved"));
    }
    // BCS 回程：interaction.resolve
    let ack = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .json(&json!({"type":"req","id":"resolve-1","method":"interaction.resolve",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "params":{"bcsRunId":"run-1","runId":"run-1","interactionId":iid,
                      "kind":"exec","idempotencyKey":"key-1","decision":decision}}))
        .send().await.unwrap();
    assert_eq!(ack.json::<serde_json::Value>().await.unwrap()["ok"], json!(true));
    // 幂等重放同 key
    let dup = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .json(&json!({"type":"req","id":"resolve-2","method":"interaction.resolve",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "params":{"bcsRunId":"run-1","runId":"run-1","interactionId":iid,
                      "kind":"exec","idempotencyKey":"key-1","decision":decision}}))
        .send().await.unwrap();
    assert_eq!(dup.json::<serde_json::Value>().await.unwrap()["ok"], json!(true));
    // 未知 interactionId → 字符串形态 error
    let unknown = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .json(&json!({"type":"req","id":"resolve-3","method":"interaction.resolve",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "params":{"bcsRunId":"run-1","runId":"run-1","interactionId":"int-nope",
                      "kind":"exec","idempotencyKey":"key-9","decision":"deny"}}))
        .send().await.unwrap();
    let body: serde_json::Value = unknown.json().await.unwrap();
    assert_eq!(body["ok"], json!(false));
    assert!(body["error"].is_string());  // 注意：此方法的 error 是字符串（spec §5.1）
    // 流继续：resolved → chat/final
    while let Some(chunk) = stream.next().await {
        acc.push_str(&String::from_utf8_lossy(&chunk.unwrap()));
        if acc.contains("\"state\":\"final\"") { break; }
    }
    assert!(!acc.contains("\"state\":\"error\""), "control protocol failed: {acc}");
    assert!(acc.contains("\"phase\":\"resolved\""), "missing resolution: {acc}");
    assert!(acc.contains("\"state\":\"final\""), "engine did not resume: {acc}");
    let resolved = acc.lines()
        .filter_map(|line| line.strip_prefix("data: "))
        .filter_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
        .find(|event| event["phase"] == "resolved").unwrap();
    assert_eq!(resolved["decision"], json!(decision));
    let expected_result = if decision == "allow-once" { "approved" } else { "denied" };
    assert!(acc.contains(expected_result), "engine did not apply the decision: {acc}");
}

#[tokio::test]
async fn secret_question_is_denied_with_a_valid_control_response() {
    let url = support::spawn_app_with_mock("mock_cc_approval.py", "claude-code").await;
    let client = reqwest::Client::builder().no_proxy()
        .timeout(std::time::Duration::from_secs(5)).build().unwrap();
    let response = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&json!({"type":"req","id":"run-secret","method":"chat.send",
            "session_id":"s-secret",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "message":{"role":"user","content":[{"type":"text","text":"secret"}]}}))
        .send().await.unwrap();
    let body = response.text().await.unwrap();
    assert!(body.contains("\"state\":\"final\""), "invalid denial: {body}");
    assert!(body.contains("denied"));
    assert!(!body.contains("event: interaction"));
}

#[tokio::test]
async fn inject_then_send_prepends_for_codex() {
    // mock_codex_app_server.py emits app-server delta notifications, so the
    // chat.send SSE body carries the assembled prompt text.
    let url = support::spawn_app_with_mock("mock_codex_app_server.py", "codex").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let resp = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .json(&json!({"type":"req","id":"inj-1","method":"chat.inject",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "message":{"role":"user","content":[{"type":"text","text":"观察上下文"}]},
            "from":{"kind":"bot","name":"观察者"}}))
        .send().await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(resp.json::<serde_json::Value>().await.unwrap()["ok"], json!(true));

    let resp = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&json!({"type":"req","id":"run-9","method":"chat.send",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "message":{"role":"user","content":[{"type":"text","text":"正式问题"}]}}))
        .send().await.unwrap();
    let text = resp.text().await.unwrap();
    // The prefix from the injected message is prepended with the `[from:{name}]`
    // envelope, and it must precede the current `正式问题` message body in the
    // assembled prompt. mock_codex_app_server.py echoes it as an
    // `item/agentMessage/delta` notification that the app-server driver maps
    // to a chat_delta SSE event — so both needles appear literally in the SSE
    // stream text (no JSON-escaping of these ASCII-bracket/multi-byte chars).
    assert!(text.contains("[from:观察者] 观察上下文"), "inject prefix missing: {text}");
    let pos_inject = text.find("[from:观察者] 观察上下文")
        .expect("inject prefix position");
    let pos_main = text.find("正式问题").expect("main message position");
    assert!(pos_inject < pos_main, "inject must precede the current message: {text}");
}

#[tokio::test]
async fn codex_app_server_resume_streams_two_turns() {
    // The app-server peer handles both thread/start and thread/resume and
    // emits a real item/agentMessage/delta notification for each turn.
    let url = support::spawn_app_with_mock("mock_codex_app_server.py", "codex").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();

    let send = |id: &str, text: &str| {
        client
            .post(format!("{url}/webhook"))
            .bearer_auth("tok-b2p")
            .header("X-BCN-Protocol-Version", "2.0")
            .json(&json!({
                "type": "req", "id": id, "method": "chat.send",
                "session_id": "s-1",
                "to_bot": {"provider_id": "bridge-1", "provider_bot_ref": "worker-1"},
                "message": {"role": "user", "content": [{"type": "text", "text": text}]}
            }))
    };

    let first = send("codex-run-1", "首轮").send().await.unwrap();
    assert_eq!(first.status(), StatusCode::OK);
    let first_text = first.text().await.unwrap();
    assert!(first_text.contains("首轮"), "first app-server turn failed: {first_text}");
    assert!(first_text.contains("\"state\":\"final\""), "missing first final: {first_text}");

    let second = send("codex-run-2", "续轮").send().await.unwrap();
    assert_eq!(second.status(), StatusCode::OK);
    let second_text = second.text().await.unwrap();
    assert!(second_text.contains("续轮"), "app-server resume turn failed: {second_text}");
    let delta = second_text.find("\"state\":\"delta\"").expect("resume delta");
    let final_ = second_text.find("\"state\":\"final\"").expect("resume final");
    assert!(delta < final_, "streamed delta must precede final: {second_text}");
    assert!(second_text.contains("\"state\":\"final\""), "missing resume final: {second_text}");
}

#[tokio::test]
async fn codex_app_server_emits_thinking_and_tool_events() {
    let url = support::spawn_app_with_mock("mock_codex_app_server.py", "codex").await;
    let resp = reqwest::Client::builder().no_proxy().build().unwrap()
        .post(format!("{url}/webhook"))
        .bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&json!({
            "type": "req", "id": "events-run", "method": "chat.send",
            "session_id": "events-session",
            "to_bot": {"provider_id": "bridge-1", "provider_bot_ref": "worker-1"},
            "message": {"role": "user", "content": [{"type": "text", "text": "事件测试"}]}
        }))
        .send()
        .await
        .unwrap();
    let text = resp.text().await.unwrap();
    assert!(text.contains("event: agent"), "missing agent events: {text}");
    assert!(text.contains("\"stream\":\"tool\""), "missing tool stream: {text}");
    assert!(text.contains("\"phase\":\"start\""), "missing tool start: {text}");
    assert!(text.contains("\"phase\":\"update\""), "missing tool update: {text}");
    assert!(text.contains("\"phase\":\"result\""), "missing tool result: {text}");
    assert!(text.contains("\"stream\":\"thinking\""), "missing thinking stream: {text}");
    assert!(text.contains("事件测试"), "missing chat output: {text}");
    assert!(text.contains("\"state\":\"final\""), "missing final: {text}");
}

#[tokio::test]
async fn trace_records_raw_converted_and_sse_events() {
    let trace_dir = tempfile::tempdir().unwrap();
    let bin = format!(
        "{}/tests/fixtures/mock_codex_app_server.py",
        env!("CARGO_MANIFEST_DIR")
    );
    let (url, _state) = support::spawn_app_with_state(&format!(
        r#"
provider_id = "bridge-1"
listen = "127.0.0.1:0"
bcs_to_provider_token = "tok-b2p"
trace_dir = "{}"
[[bot]]
provider_bot_ref = "worker-1"
engine = "codex"
cwd = "/tmp"
engine_bin = "{}"
"#,
        trace_dir.path().display(),
        bin
    ))
    .await;

    let response = reqwest::Client::builder().no_proxy().build().unwrap()
        .post(format!("{url}/webhook"))
        .bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&json!({
            "type": "req", "id": "trace-run", "method": "chat.send",
            "session_id": "trace-session",
            "to_bot": {"provider_id": "bridge-1", "provider_bot_ref": "worker-1"},
            "message": {"role": "user", "content": [{"type": "text", "text": "trace"}]}
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let _ = response.text().await.unwrap();

    let raw = std::fs::read_to_string(trace_dir.path().join("engine.raw.ndjson")).unwrap();
    let converted = std::fs::read_to_string(trace_dir.path().join("bridge.converted.ndjson")).unwrap();
    let sse = std::fs::read_to_string(trace_dir.path().join("bridge.sse.ndjson")).unwrap();
    assert!(raw.contains("\"engine_to_bridge\""));
    assert!(raw.contains("item/agentMessage/delta"));
    assert!(converted.contains("\"bridge_converted\""));
    assert!(converted.contains("\"stream\":\"tool\""));
    assert!(converted.contains("\"stream\":\"thinking\""));
    assert!(sse.contains("\"bridge_to_bcs\""));
    assert!(sse.contains("event: chat"));
    assert!(sse.contains("event: agent"));
}

#[tokio::test]
async fn wrapped_tool_result_is_unwrapped_in_sse_and_preserved_in_raw_trace() {
    let trace_dir = tempfile::tempdir().unwrap();
    let bin = format!("{}/tests/fixtures/mock_cc_wrapped_result.sh", env!("CARGO_MANIFEST_DIR"));
    let url = support::spawn_app(&format!(r#"
provider_id = "bridge-1"
listen = "127.0.0.1:0"
bcs_to_provider_token = "tok-b2p"
trace_dir = "{}"
[[bot]]
provider_bot_ref = "worker-1"
engine = "claude-code"
cwd = "/tmp"
engine_bin = "{}"
"#, trace_dir.path().display(), bin)).await;
    let client = reqwest::Client::builder().no_proxy()
        .timeout(std::time::Duration::from_secs(5)).build().unwrap();
    let response = client.post(format!("{url}/webhook"))
        .bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&json!({"type":"req","id":"wrapped-run","method":"chat.send",
            "session_id":"wrapped-session",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "message":{"role":"user","content":[{"type":"text","text":"lookup"}]}}))
        .send().await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = response.text().await.unwrap();
    let events: Vec<serde_json::Value> = body.lines()
        .filter_map(|line| line.strip_prefix("data: "))
        .map(|line| serde_json::from_str(line).unwrap()).collect();
    let result = events.iter().find(|event| event["phase"] == "result").unwrap();
    assert_eq!(result["toolCallId"], json!("toolu_wrapped"));
    assert_eq!(result["result"], json!(r#"{"items":[1,2]}"#));
    assert!(events.iter().any(|event| event["state"] == "final"));

    let raw = std::fs::read_to_string(trace_dir.path().join("engine.raw.ndjson")).unwrap();
    let raw_result = raw.lines()
        .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
        .find(|record| record["json"]["type"] == "user").unwrap();
    assert_eq!(raw_result["json"]["message"]["content"][0]["content"],
        json!(r#"{"result":"{\"items\":[1,2]}"}"#));
}

/// Task 14 — chat.abort terminal-state matrix:
/// - Active run present: 200 `{ok, aborted:true, aborted_run_ids:[run_id]}`; the
///   SSE stream for the aborted chat.send must emit a terminal `state=aborted`
///   frame (the 200 abort ACK does not wait for the engine to die — coordination
///   is via the run loop, which the test asserts by reading the SSE body).
/// - Repeating abort on the same now-terminal run: 410 `run_terminated` (stable;
///   the run→session reverse index on RunRegistry remembers the terminal run).
/// - Unknown session: 200 `{ok, aborted:false, aborted_run_ids:[]}`.
#[tokio::test]
async fn abort_active_run_emits_aborted_terminal() {
    let url = support::spawn_app_with_mock("mock_cc_slow.sh", "claude-code").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let send = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&serde_json::json!({"type":"req","id":"run-1","method":"chat.send",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "message":{"role":"user","content":[{"type":"text","text":"慢任务"}]}}));
    // 后台持有 SSE 响应体
    let sse = tokio::spawn(async move { send.send().await.unwrap().text().await.unwrap() });
    tokio::time::sleep(std::time::Duration::from_millis(200)).await; // 等 run 起跑

    let resp = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .json(&serde_json::json!({"type":"req","id":"abort-1","method":"chat.abort",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"}}))
        .send().await.unwrap();
    let status = resp.status().as_u16();
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(status, 200);
    assert_eq!(body["ok"], serde_json::json!(true));
    assert_eq!(body["aborted"], serde_json::json!(true));
    assert_eq!(body["aborted_run_ids"], serde_json::json!(["run-1"]));

    let sse_text = tokio::time::timeout(std::time::Duration::from_secs(5), sse).await.unwrap().unwrap();
    assert!(sse_text.contains("\"state\":\"aborted\""));

    // 对同一 terminal run 重复 abort → 410 run_terminated（稳定）
    let again = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .json(&serde_json::json!({"type":"req","id":"abort-2","method":"chat.abort",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"}}))
        .send().await.unwrap();
    assert_eq!(again.status(), 410);
    assert_eq!(again.json::<serde_json::Value>().await.unwrap()["error"]["code"],
               serde_json::json!("run_terminated"));

    // 无任何记录的 session → aborted:false
    let none = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .json(&serde_json::json!({"type":"req","id":"abort-3","method":"chat.abort",
            "session_id":"s-unknown",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"}}))
        .send().await.unwrap();
    let none_body: serde_json::Value = none.json().await.unwrap();
    assert_eq!(none_body["aborted"], serde_json::json!(false));
    assert_eq!(none_body["aborted_run_ids"], serde_json::json!([]));
}

/// Regression test for the chat.send/abort startup TOCTOU (review fix round 1).
///
/// Invariant: the moment `sessions.active_run(bot, session)` is `Some(run_id)`,
/// `RunRegistry::get(run_id)` MUST also be `Some`. The original bug reserved
/// the session slot (`try_start_run`) BEFORE creating the registry entry
/// (`begin`), leaving a window where a chat.abort could see
/// `active_run=Some` + `runs.get=None` and wrongly return `{"aborted":false}`
/// (matrix branch 3 instead of branch 1). The fix inverts the order in
/// `handle_chat_send`: begin → try_start_run → (on 429) rollback.
///
/// Forces the window deterministically by polling AppState's `sessions` +
/// `runs` directly while a slow-mock chat.send is in flight: the moment
/// `active_run` flips to `Some`, `runs.get(rid)` must be `Some`. mock_cc_slow
/// keeps the run alive long enough to reliably observe the transition. The
/// `(url, state)` helper is what exposes AppState for this check.
#[tokio::test]
async fn chat_send_creates_run_entry_before_session_slot_claim() {
    let (url, state) = support::spawn_app_with_mock_and_state("mock_cc_slow.sh", "claude-code").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let send = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&serde_json::json!({"type":"req","id":"r-order","method":"chat.send",
            "session_id":"s-order",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "message":{"role":"user","content":[{"type":"text","text":"hi"}]}}));
    // 后台持有 SSE 响应体（mock_cc_slow.sh 会读 stdin 后 sleep 30s）
    let sse = tokio::spawn(async move { send.send().await.unwrap().text().await.unwrap() });

    // 轮询 AppState：active_run 一旦变成 Some(r-order)，runs.get(r-order) 必须也是
    // Some —— 这是 begin-first 不变量的直接断言。原 bug 在 try_start_run 与 begin
    // 之间的窗口里该断言会失败（active_run=Some, runs.get=None）。
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
    let mut observed = false;
    loop {
        if let Some(rid) = state
            .sessions
            .active_run("worker-1", "s-order")
            .await
            .as_deref()
        {
            if rid == "r-order" {
                assert!(
                    state.runs.get(rid).is_some(),
                    "TOCTOU regression: sessions.active_run=Some({rid}) but \
                     RunRegistry::get returned None — abort landing now would \
                     return aborted:false instead of aborted:true"
                );
                observed = true;
                break;
            }
        }
        if std::time::Instant::now() > deadline {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(2)).await;
    }
    assert!(
        observed,
        "active_run did not flip to Some(r-order) before the 2s deadline; \
         the slow mock should keep the run in flight — rerun if flaky"
    );

    // Drain the SSE so the slow mock is reclaimed via kill_on_drop (aborted
    // path cancels the engine, the test never waits the 30s sleep).
    let _ = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .json(&serde_json::json!({"type":"req","id":"abort-order","method":"chat.abort",
            "session_id":"s-order",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"}}))
        .send().await;
    let _ = tokio::time::timeout(std::time::Duration::from_secs(5), sse).await;
}

/// Task 16 — protocol regression: idempotent re-attach replays buffered frames
/// then follows the live broadcast (spec §5/§6).
///
/// `mock_cc_burst.sh` emits two `text_delta` lines IMMEDIATELY (突发一, 突发二)
/// then sleeps 30s, so the first chat.send pushes seq 1 and seq 2 into the
/// run's buffer BEFORE the re-attach. A same-id, same-body retry then takes
/// the active re-attach path (`RunRegistry::begin` returns `is_new == false`,
/// fingerprint matches) → 200 (not 429 — `try_start_run` is not re-invoked; not
/// 409 — body matches). The re-attached stream's forwarder snapshots the buffer
/// (seq 1, 2) and subscribes to the broadcast.
///
/// Strengthened per review round 1: the test now genuinely covers the buffer
/// snapshot → replay → live-follow partition —
/// 1. read the FIRST stream until both deltas arrive (proves the buffer is
///    non-empty before the re-attach),
/// 2. re-attach (second POST) → 200,
/// 3. read the SECOND stream until both 突发一 + 突发二 replay — these arrive
///    from the snapshot at seq 1, 2 BEFORE any live frame (asserting them
///    here, before the abort, proves the replay came from the buffer not a
///    live re-emission),
/// 4. chat.abort → the run loop emits a terminal `chat_aborted` frame (seq 3)
///    pushed AFTER the re-attach's subscribe, so it arrives via the live
///    broadcast leg,
/// 5. drain until the aborted terminal at seq 3 — verifying seq continuity
///    1→2→3 across the buffer→broadcast partition.
///
/// `kill_on_drop` reaps the mock subprocess on runtime teardown; the 5s ceilings
/// protect against the 30s sleep (never reached in practice — the abort
/// finalizes within milliseconds).
#[tokio::test]
async fn duplicate_send_reattaches_with_replay() {
    let url = support::spawn_app_with_mock("mock_cc_burst.sh", "claude-code").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let body = serde_json::json!({"type":"req","id":"run-dup","method":"chat.send",
        "session_id":"s-1",
        "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
        "message":{"role":"user","content":[{"type":"text","text":"hi"}]}});

    // 1. First POST — read its chunks until both deltas are buffered. The burst
    //    mock emits both lines immediately, so this resolves within
    //    milliseconds; the 5s ceiling guards against a stalled mock.
    let first = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0").json(&body).send().await.unwrap();
    assert_eq!(first.status(), 200);
    let mut first_stream = first.bytes_stream();
    let mut first_acc = String::new();
    let first_deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    loop {
        if first_acc.contains("突发一") && first_acc.contains("突发二") { break; }
        if std::time::Instant::now() > first_deadline {
            panic!("first stream did not emit both deltas within 5s: {first_acc}");
        }
        match tokio::time::timeout(std::time::Duration::from_millis(500), first_stream.next()).await {
            Ok(Some(chunk)) => first_acc.push_str(&String::from_utf8_lossy(&chunk.unwrap())),
            Ok(None) => panic!("first stream ended before both deltas: {first_acc}"),
            Err(_) => continue,
        }
    }
    // Both deltas are now in the run's buffer (push_raw pushes buffer+broadcast
    // under one mutex), so the re-attach's snapshot will contain them.

    // 2. Same id + same body retry → 200 re-attach (not 429, not 409). The
    //    forwarder snapshots the buffer (seq 1, 2) and subscribes to the
    //    broadcast BEFORE the response is returned, so any later push (the
    //    aborted frame) reaches this stream via the live broadcast leg.
    let second = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0").json(&body).send().await.unwrap();
    assert_eq!(second.status(), 200);
    let mut second_stream = second.bytes_stream();
    let mut second_acc = String::new();

    // 3. Read the re-attached stream until BOTH buffered deltas replay — these
    //    arrive from the snapshot at seq 1, 2 BEFORE any live frame. Asserting
    //    them here (before the abort) proves the replay came from the buffer,
    //    not a live broadcast re-emission. If the buffer-replay loop in
    //    `forward_stream` were deleted, this loop would block past the deadline
    //    (the broadcast is quiet during the 30s sleep) and fail.
    let replay_deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    loop {
        if second_acc.contains("突发一") && second_acc.contains("突发二") { break; }
        if std::time::Instant::now() > replay_deadline {
            panic!("re-attached stream did not replay both deltas within 5s: {second_acc}");
        }
        match tokio::time::timeout(std::time::Duration::from_millis(500), second_stream.next()).await {
            Ok(Some(chunk)) => second_acc.push_str(&String::from_utf8_lossy(&chunk.unwrap())),
            Ok(None) => panic!("re-attached stream ended before both deltas replayed: {second_acc}"),
            Err(_) => continue,
        }
    }
    let replay_seqs = support::extract_seqs(&second_acc);
    assert_eq!(replay_seqs, vec![1, 2],
        "buffered replay covers seq 1 and 2 (replay precedes any live frame): {second_acc}");

    // 4. Abort the run to trigger a terminal frame promptly (the slow mock
    //    would otherwise block 30s). The aborted frame is pushed AFTER the
    //    re-attach's subscribe, so it arrives via the live broadcast leg —
    //    verifying buffer-replay → live-follow continuity.
    let _ = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .json(&serde_json::json!({"type":"req","id":"abort-dup","method":"chat.abort",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"}}))
        .send().await;

    // 5. Drain until the aborted terminal arrives at seq 3 (5s ceiling — the
    //    run loop finalizes within milliseconds of the abort). first_stream is
    //    held until scope end so the broadcast retains a subscriber while the
    //    aborted frame is pushed; kill_on_drop reaps the mock subprocess.
    let abort_deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    loop {
        if second_acc.contains("\"state\":\"aborted\"") { break; }
        if std::time::Instant::now() > abort_deadline {
            panic!("aborted terminal not received within 5s: {second_acc}");
        }
        match tokio::time::timeout(std::time::Duration::from_millis(500), second_stream.next()).await {
            Ok(Some(chunk)) => second_acc.push_str(&String::from_utf8_lossy(&chunk.unwrap())),
            Ok(None) => break,
            Err(_) => continue,
        }
    }
    assert!(second_acc.contains("\"state\":\"aborted\""), "aborted terminal: {second_acc}");
    let seqs = support::extract_seqs(&second_acc);
    assert_eq!(seqs, vec![1, 2, 3],
        "seq continuity 1→2→3 across buffer→broadcast: {second_acc}");
    drop(first_stream);
}

/// Task 16 — protocol regression: same id, different body → 409 conflict
/// (spec §5). `RunRegistry::begin` returns the existing handle with
/// `matches(fp) == false`, so the handler renders `BridgeError::conflict()`.
/// The slow mock keeps the first run active while the conflicting retry
/// arrives; `kill_on_drop` reclaims the engine subprocess on test exit.
#[tokio::test]
async fn same_id_different_body_conflicts() {
    let url = support::spawn_app_with_mock("mock_cc_slow.sh", "claude-code").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let mut body = serde_json::json!({"type":"req","id":"run-x","method":"chat.send",
        "session_id":"s-1",
        "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
        "message":{"role":"user","content":[{"type":"text","text":"hi"}]}});
    let _first = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0").json(&body).send().await.unwrap();
    body["message"]["content"][0]["text"] = serde_json::json!("changed");
    let second = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0").json(&body).send().await.unwrap();
    assert_eq!(second.status(), 409);
    let err: serde_json::Value = second.json().await.unwrap();
    assert_eq!(err["error"]["code"], serde_json::json!("conflict"));
}

/// Task 16 — protocol regression: missing `X-BCN-Protocol-Version: 2.0`
/// header → 400 (spec §5). `handle_chat_send` is the only method that gates on
/// this header; a chat.send without it short-circuits to
/// `BridgeError::invalid_request` after token/provider_id checks pass.
#[tokio::test]
async fn missing_protocol_2_header_rejected() {
    let url = support::spawn_app_with_mock("mock_cc.sh", "claude-code").await;
    let resp = reqwest::Client::builder().no_proxy().build().unwrap().post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .json(&serde_json::json!({"type":"req","id":"r","method":"chat.send",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "message":{"role":"user","content":[{"type":"text","text":"hi"}]}}))
        .send().await.unwrap();
    assert_eq!(resp.status(), 400);
    let body: serde_json::Value = resp.json().await.unwrap();
    assert_eq!(body["error"]["code"], serde_json::json!("invalid_request"));
}

/// Task 16 — protocol regression: UTF-8 deltas stay intact end-to-end (spec
/// §5/§6). `mock_cc_utf8.sh` emits 40 Chinese `text_delta` lines then a
/// terminal result. `reqwest` `resp.text()` requires the whole body to be
/// valid UTF-8 (a half-character byte slice anywhere would fail); additionally
/// every `data:` line must parse as JSON — the SSE encoder and the cc driver's
/// NDJSON reader must never slice multi-byte sequences.
#[tokio::test]
async fn utf8_chinese_deltas_stay_intact() {
    // mock_cc_utf8.sh：逐行吐 40 条中文 delta（每条一个完整 JSON 事件行）
    let url = support::spawn_app_with_mock("mock_cc_utf8.sh", "claude-code").await;
    let resp = reqwest::Client::builder().no_proxy().build().unwrap().post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&serde_json::json!({"type":"req","id":"run-u","method":"chat.send",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "message":{"role":"user","content":[{"type":"text","text":"hi"}]}}))
        .send().await.unwrap();
    let text = resp.text().await.unwrap();  // text() 要求全程合法 UTF-8
    assert!(text.contains("中文增量"), "missing Chinese delta: {text}");
    // 每个 data 行都是合法 JSON（无半个字符截断）
    for line in text.lines().filter_map(|l| l.strip_prefix("data: ")) {
        serde_json::from_str::<serde_json::Value>(line).expect("valid json frame");
    }
    // 40 条 delta 均应在流中（progress check beyond the contains needle）
    let delta_count = text.lines().filter(|l| l.contains("\"deltaText\":\"中文增量\"")).count();
    assert_eq!(delta_count, 40, "expected 40 Chinese deltas, found {delta_count}: {text}");
}

/// Task 16 — protocol regression: an oversize single frame becomes a terminal
/// `chat/error`, never an oversize emission (spec §5/§7). `mock_cc_big.sh`
/// emits one `text_delta` with 9,000,000 chars of padding (~9 MiB JSON frame),
/// exceeding `MAX_FRAME_BYTES` (8 MiB). The run loop's `push_frame` catches
/// `FrameError::FrameTooLarge`, emits a bounded `chat_error` terminal instead,
/// and signals termination — no oversize frame reaches the wire.
#[tokio::test]
async fn oversize_single_frame_becomes_chat_error() {
    // mock_cc_big.sh：吐一条 >8MiB 的 text_delta
    let url = support::spawn_app_with_mock("mock_cc_big.sh", "claude-code").await;
    let resp = reqwest::Client::builder().no_proxy().build().unwrap().post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&serde_json::json!({"type":"req","id":"run-big","method":"chat.send",
            "session_id":"s-1",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
            "message":{"role":"user","content":[{"type":"text","text":"hi"}]}}))
        .send().await.unwrap();
    let text = resp.text().await.unwrap();
    assert!(text.contains("\"state\":\"error\""), "超限 → error 终态: {text}");
    assert!(!text.contains("\"state\":\"final\""), "error path must not also emit final: {text}");
    assert!(text.len() < 9 * 1024 * 1024, "没有超限帧被发出: {}", text.len());
    // The error frame carries the "frame too large" diagnostic from push_frame.
    assert!(text.contains("\"errorMessage\":\"frame too large\""), "error cause: {text}");
}
