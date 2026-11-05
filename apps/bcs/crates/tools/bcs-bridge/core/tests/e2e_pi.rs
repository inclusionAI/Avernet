//! End-to-end pi/omp engine tests over the webhook surface, using the
//! `mock_pi.py` / `mock_omp.py` JSONL peers (see fixtures for the
//! prompt-prefix modes each test drives).

use axum::http::StatusCode;
use serde_json::{json, Value};

mod support;

fn chat_send(id: &str, session: &str, text: &str) -> Value {
    json!({
        "type": "req", "id": id, "method": "chat.send",
        "session_id": session,
        "to_bot": {"provider_id": "bridge-1", "provider_bot_ref": "worker-1"},
        "message": {"role": "user", "content": [{"type": "text", "text": text}]}
    })
}

/// Parse the `data:` frames of one SSE body.
fn sse_frames(body: &str) -> Vec<Value> {
    body.lines()
        .filter_map(|line| line.strip_prefix("data: "))
        .filter_map(|data| serde_json::from_str(data).ok())
        .collect()
}

/// The assistant text of the terminal chat frame (`state == "final"`).
fn final_text(body: &str) -> String {
    sse_frames(body)
        .into_iter()
        .find(|frame| frame["state"] == "final")
        .and_then(|frame| frame.pointer("/message/content/0/text").cloned())
        .and_then(|text| text.as_str().map(str::to_string))
        .unwrap_or_default()
}

#[tokio::test]
async fn pi_turn_streams_deltas_tools_thinking_and_final() {
    let url = support::spawn_app_with_mock("mock_pi.py", "pi").await;
    let resp = reqwest::Client::builder().no_proxy().build().unwrap()
        .post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("pi-run-1", "s-pi-1", "第一轮"))
        .send().await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let text = resp.text().await.unwrap();
    assert!(text.contains("event: agent"), "missing agent events: {text}");
    assert!(text.contains("\"stream\":\"tool\""), "missing tool stream: {text}");
    assert!(text.contains("\"phase\":\"start\""), "missing tool start: {text}");
    assert!(text.contains("\"phase\":\"result\""), "missing tool result: {text}");
    assert!(text.contains("\"stream\":\"thinking\""), "missing thinking stream: {text}");
    assert!(text.contains("\"deltaText\":\"你好 \""), "missing streamed delta: {text}");
    assert!(text.contains("file-a\\nfile-b"), "missing flattened tool result: {text}");
    assert_eq!(final_text(&text), "Hello pi: 第一轮");
    let seqs = support::extract_seqs(&text);
    assert!(!seqs.is_empty(), "no seq frames: {text}");
    assert!(seqs.windows(2).all(|w| w[0] < w[1]), "seq not monotonic: {seqs:?}");
}

#[tokio::test]
async fn pi_resumes_the_persisted_session_id_on_followup_turns() {
    // mock_pi.py answers `get_state` with a per-process session id unless the
    // bridge passes `--session-id`; the driver additionally refuses a
    // mismatched id, so the second turn can only succeed when the persisted
    // id is replayed on the command line.
    let url = support::spawn_app_with_mock("mock_pi.py", "pi").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();

    let first = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("pi-run-1", "s-resume", "首轮")).send().await.unwrap();
    let first_text = first.text().await.unwrap();
    assert_eq!(final_text(&first_text), "Hello pi: 首轮");

    let second = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("pi-run-2", "s-resume", "续轮")).send().await.unwrap();
    assert_eq!(second.status(), StatusCode::OK);
    let second_text = second.text().await.unwrap();
    assert_eq!(final_text(&second_text), "Hello pi: 续轮",
        "resume turn must succeed with the persisted session id: {second_text}");
}

#[tokio::test]
async fn pi_handled_prompt_completes_with_empty_final() {
    let url = support::spawn_app_with_mock("mock_pi.py", "pi").await;
    let resp = reqwest::Client::builder().no_proxy().build().unwrap()
        .post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("pi-handled", "s-handled", "HANDLED: /command"))
        .send().await.unwrap();
    let text = resp.text().await.unwrap();
    assert_eq!(final_text(&text), "", "locally handled prompt settles with empty final: {text}");
    assert!(text.contains("\"state\":\"final\""), "expected a final frame: {text}");
}

#[tokio::test]
async fn pi_provider_error_and_retry_failure_surface_as_chat_errors() {
    let url = support::spawn_app_with_mock("mock_pi.py", "pi").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();

    let resp = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("pi-err", "s-err", "ERROR_RUN: boom")).send().await.unwrap();
    let text = resp.text().await.unwrap();
    assert!(text.contains("\"state\":\"error\""), "expected an error frame: {text}");
    assert!(text.contains("provider 500"), "missing provider error: {text}");

    let resp = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("pi-retry", "s-retry", "RETRY_FAIL: boom")).send().await.unwrap();
    let text = resp.text().await.unwrap();
    assert!(text.contains("\"state\":\"error\""), "expected an error frame: {text}");
    assert!(text.contains("529 overloaded"), "missing final retry error: {text}");
}

#[tokio::test]
async fn omp_turn_binds_a_bridge_directory_and_resumes_it() {
    let url = support::spawn_app_with_mock("mock_omp.py", "omp").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();

    let first = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("omp-run-1", "s-omp", "第一轮")).send().await.unwrap();
    let first_text = first.text().await.unwrap();
    assert!(first_text.contains("dir=/tmp/.bcs-bridge/omp-sessions/bcs-"),
        "session must bind to a bridge-owned directory: {first_text}");
    assert!(first_text.contains("resumed=false"), "first turn must open a fresh session: {first_text}");
    let first_dir = bound_dir(&first_text);

    let second = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("omp-run-2", "s-omp", "续轮")).send().await.unwrap();
    assert_eq!(second.status(), StatusCode::OK);
    let second_text = second.text().await.unwrap();
    assert!(second_text.contains("resumed=true"),
        "second turn must resume the same directory: {second_text}");
    assert_eq!(bound_dir(&second_text), first_dir,
        "both turns must bind the same bridge-owned directory");
    assert_eq!(final_text(&second_text), "Hello omp: 续轮");
}

/// Extract the `dir=...` marker the mock streams as its first delta.
fn bound_dir(body: &str) -> String {
    body.lines()
        .filter_map(|line| line.strip_prefix("data: "))
        .filter_map(|data| serde_json::from_str::<Value>(data).ok())
        .find_map(|frame| frame["deltaText"].as_str().map(str::to_string))
        .and_then(|delta| delta.strip_prefix("dir=").map(|dir| dir.trim_end_matches(" resumed=false").trim_end_matches(" resumed=true").to_string()))
        .expect("mock streams its bound directory as the first delta")
}

#[tokio::test]
async fn omp_engine_side_abort_surfaces_as_chat_error() {
    let url = support::spawn_app_with_mock("mock_omp.py", "omp").await;
    let resp = reqwest::Client::builder().no_proxy().build().unwrap()
        .post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("omp-abort", "s-abort", "ABORT: 中止")).send().await.unwrap();
    let text = resp.text().await.unwrap();
    assert!(text.contains("\"state\":\"error\""), "engine-side abort must surface a terminal error: {text}");
    assert!(text.contains("omp aborted the prompt"), "missing abort reason: {text}");
}

#[tokio::test]
async fn omp_background_run_waits_for_session_settled() {
    // prompt_result arrives with sessionSettled=false while background work
    // still streams; the turn must not finalize on the first completed run.
    let url = support::spawn_app_with_mock("mock_omp.py", "omp").await;
    let resp = reqwest::Client::builder().no_proxy().build().unwrap()
        .post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("omp-bg", "s-bg", "BG: 后台任务")).send().await.unwrap();
    let text = resp.text().await.unwrap();
    assert_eq!(final_text(&text), "后台结论",
        "final must come from the background follow-up run, not the first yield: {text}");
    assert!(text.contains("dir=/tmp/.bcs-bridge/omp-sessions/bcs-"),
        "the first run must still stream before the background follow-up: {text}");
}

#[tokio::test]
async fn omp_local_and_failed_prompts_map_to_final_and_error() {
    let url = support::spawn_app_with_mock("mock_omp.py", "omp").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();

    let resp = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("omp-local", "s-local", "LOCAL: /model")).send().await.unwrap();
    let text = resp.text().await.unwrap();
    assert_eq!(final_text(&text), "", "locally finished command settles with empty final: {text}");
    assert!(text.contains("\"state\":\"final\""), "expected a final frame: {text}");

    let resp = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("omp-error", "s-error", "ERROR: boom")).send().await.unwrap();
    let text = resp.text().await.unwrap();
    assert!(text.contains("\"state\":\"error\""), "expected an error frame: {text}");
    assert!(text.contains("provider timeout"), "missing prompt_result error: {text}");
}

#[tokio::test]
async fn abort_active_pi_run_emits_aborted_terminal() {
    let url = support::spawn_app_with_mock("mock_pi.py", "pi").await;
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let send = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .header("X-BCN-Protocol-Version", "2.0")
        .json(&chat_send("pi-slow", "s-slow", "SLOW: 慢任务"));
    let sse = tokio::spawn(async move { send.send().await.unwrap().text().await.unwrap() });
    tokio::time::sleep(std::time::Duration::from_millis(300)).await;

    let resp = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
        .json(&json!({"type":"req","id":"abort-1","method":"chat.abort",
            "session_id":"s-slow",
            "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"}}))
        .send().await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(resp.json::<Value>().await.unwrap()["aborted"], json!(true));

    // The mock sleeps inside its turn loop and cannot answer the abort
    // command; the driver's bounded grace window must still tear the turn
    // down (SIGTERM group kill) well before the 30s sleep ends.
    let sse_text = tokio::time::timeout(std::time::Duration::from_secs(20), sse)
        .await.expect("abort must bound the teardown").unwrap();
    assert!(sse_text.contains("\"state\":\"aborted\""), "missing aborted terminal: {sse_text}");
}
