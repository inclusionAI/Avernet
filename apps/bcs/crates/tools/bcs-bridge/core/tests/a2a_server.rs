#[path = "support/a2a.rs"]
mod support;
use support::*;
use a2a::TaskState;
use serde_json::{json, Value};
use futures::StreamExt;

#[tokio::test]
async fn cards_are_opt_in_scoped_sdk_compatible_and_do_not_trust_host_headers() {
    let disabled = Server::new(&[("p", &["worker"])], false).await;
    assert_eq!(disabled.client.get(format!("{}/.well-known/agent-card.json", disabled.url)).send().await.unwrap().status(), 404);
    disabled.close().await;
    let server = Server::new(&[("p", &["worker"])], true).await;
    let card: a2a::AgentCard = server.client.get(format!("{}/.well-known/agent-card.json", server.url))
        .header("host", "untrusted.example").header("forwarded", "host=untrusted.example;proto=https")
        .send().await.unwrap().json().await.unwrap();
    assert_eq!(card.name, "worker");
    assert_eq!(card.capabilities.streaming, Some(true));
    assert_eq!(card.capabilities.push_notifications, Some(false));
    assert!(card.capabilities.extensions.is_none());
    assert_eq!(card.supported_interfaces.len(), 2);
    assert_eq!(card.supported_interfaces[0].protocol_binding, "HTTP+JSON");
    assert_eq!(card.supported_interfaces[1].protocol_binding, "JSONRPC");
    for interface in &card.supported_interfaces {
        assert_eq!(interface.protocol_version, "1.0");
        assert_eq!(interface.url, server.endpoint("p", "worker"));
    }
    assert!(card.security_requirements.unwrap()[0].contains_key("bearer"));
    let raw = server.get("p", "worker", "/.well-known/agent-card.json").send().await.unwrap().text().await.unwrap();
    assert!(!raw.contains("token-p") && !raw.contains(&server.dir.path().display().to_string()));
    server.close().await;
    let multiple = Server::new(&[("p", &["one", "two"])], true).await;
    assert_eq!(multiple.client.get(format!("{}/.well-known/agent-card.json", multiple.url)).send().await.unwrap().status(), 404);
    assert!(multiple.get("p", "two", "/.well-known/agent-card.json").send().await.unwrap().status().is_success());
    multiple.close().await;
}

#[tokio::test]
async fn rest_send_history_resume_idempotency_and_webhook_compatibility() {
    let server = Server::new(&[("p", &["worker"])], true).await;
    let mut first = request("m1", "你好"); first["configuration"] = json!({"historyLength":1});
    let result: a2a::SendMessageResponse = server.post("p", "worker", "/message:send").json(&first).send().await.unwrap().json().await.unwrap();
    let a2a::SendMessageResponse::Task(task) = result else { panic!("Task expected") };
    assert_eq!(task.status.state, TaskState::Completed);
    assert_eq!(task.artifacts.as_ref().unwrap()[0].parts[0].as_text(), Some("答复: 你好"));
    assert_eq!(task.history.as_ref().unwrap()[0].message_id, "m1");
    assert_eq!(task.history.as_ref().unwrap()[0].task_id.as_deref(), Some(task.id.as_str()));
    let repeated: Value = server.post("p", "worker", "/message:send").json(&first).send().await.unwrap().json().await.unwrap();
    assert_eq!(repeated["task"]["id"], task.id);
    assert_eq!(server.mock.turns.lock().unwrap().len(), 1);
    let conflict = server.post("p", "worker", "/message:send").json(&request("m1", "different")).send().await.unwrap();
    assert_eq!(conflict.status(), 400);
    let mut next = request("m2", "继续"); next["message"]["contextId"] = json!(task.context_id);
    let result: Value = server.post("p", "worker", "/message:send").json(&next).send().await.unwrap().json().await.unwrap();
    assert_eq!(result["task"]["contextId"], task.context_id);
    assert_eq!(result["task"]["status"]["state"], "TASK_STATE_COMPLETED");
    assert_ne!(result["task"]["id"], task.id);
    assert!(server.mock.turns.lock().unwrap()[1].1.is_some());
    let query = server.task("p", "worker", &task.id).await;
    assert!(query.history.is_none());
    assert!(server.states[0].runs.get(&task.id).is_none(), "completed A2A owner must release replay memory");
    let ping = server.client.post(format!("{}/webhook", server.url)).bearer_auth("token-p")
        .json(&json!({"type":"req","id":"ping","method":"bot.ping","to_bot":{"provider_id":"p","provider_bot_ref":"worker"}}))
        .send().await.unwrap();
    assert_eq!(ping.status(), 200);
    server.close().await;
}

#[tokio::test]
async fn rest_stream_has_one_task_stable_artifact_and_terminal_after_session_cleanup() {
    let server = Server::new(&[("p", &["worker"])], true).await;
    let response = server.post("p", "worker", "/message:stream").json(&request("stream", "流式")).send().await.unwrap();
    assert!(response.headers()["content-type"].to_str().unwrap().starts_with("text/event-stream"));
    let frames = events(&response.text().await.unwrap());
    assert!(frames[0].get("task").is_some());
    let id = frames[0]["task"]["id"].as_str().unwrap();
    for frame in &frames { let _: a2a::StreamResponse = serde_json::from_value(frame.clone()).unwrap(); }
    let task = server.terminal("p", "worker", id).await;
    assert_eq!(task.status.state, TaskState::Completed);
    assert_eq!(task.artifacts.unwrap()[0].parts[0].as_text(), Some("答复: 流式"));
    assert!(server.states[0].sessions.active_run("worker", &format!("a2a:{}", task.context_id)).await.is_none());
    if frames.len() > 1 {
        assert_eq!(frames.last().unwrap()["statusUpdate"]["status"]["state"], "TASK_STATE_COMPLETED");
        for frame in frames.iter().filter_map(|frame| frame.get("artifactUpdate")) {
            assert_eq!(frame["artifact"]["artifactId"], "answer");
        }
    }
    server.close().await;
}

#[tokio::test]
async fn disconnect_does_not_abort_and_subscription_can_recover_task_snapshot() {
    let server = Server::new(&[("p", &["worker"])], true).await;
    let response = server.post("p", "worker", "/message:stream").json(&request("detached", "hold")).send().await.unwrap();
    let mut chunks = response.bytes_stream();
    let mut prefix = String::new();
    let id = loop {
        prefix.push_str(std::str::from_utf8(&chunks.next().await.unwrap().unwrap()).unwrap());
        if let Some(end) = prefix.find("\n\n") {
            let frames = events(&prefix[..end]);
            if let Some(id) = frames.first().and_then(|frame| frame.pointer("/task/id")).and_then(Value::as_str) { break id.to_owned(); }
        }
    };
    drop(chunks);
    let current = server.task("p", "worker", &id).await;
    assert_eq!(current.status.state, TaskState::Working);
    assert!(!server.states[0].runs.get(&id).unwrap().abort.is_cancelled());
    let response = server.post("p", "worker", &format!("/tasks/{id}:subscribe")).json(&json!({"id":id})).send().await.unwrap();
    server.mock.release.cancel();
    let frames = events(&response.text().await.unwrap());
    assert_eq!(frames[0]["task"]["id"], id);
    assert_eq!(frames.last().unwrap()["statusUpdate"]["status"]["state"], "TASK_STATE_COMPLETED");
    let task = server.terminal("p", "worker", &id).await;
    assert_eq!(task.artifacts.unwrap()[0].parts[0].as_text(), Some("答复: hold"));
    // A terminal subscription is a single authoritative snapshot and closes.
    let response = server.post("p", "worker", &format!("/tasks/{id}:subscribe")).send().await.unwrap();
    let frames = events(&response.text().await.unwrap());
    assert_eq!(frames.len(), 1);
    assert_eq!(frames[0]["task"]["status"]["state"], "TASK_STATE_COMPLETED");
    server.close().await;
}

#[tokio::test]
async fn async_send_cancel_is_exact_confirmed_idempotent_and_fenced_from_other_tasks() {
    let server = Server::new(&[("p", &["worker"])], true).await;
    let task: Value = server.post("p", "worker", "/message:send").json(&asynchronous("wait", "wait")).send().await.unwrap().json().await.unwrap();
    let id = task["task"]["id"].as_str().unwrap();
    let context = task["task"]["contextId"].as_str().unwrap();
    let mut busy = request("busy", "hello"); busy["message"]["contextId"] = json!(context);
    assert_eq!(server.post("p", "worker", "/message:send").json(&busy).send().await.unwrap().status(), 400);
    assert_eq!(server.post("p", "worker", &format!("/tasks/{id}:cancel")).json(&json!({"id":"wrong"})).send().await.unwrap().status(), 400);
    for _ in 0..2 {
        let canceled: a2a::Task = server.post("p", "worker", &format!("/tasks/{id}:cancel")).json(&json!({"id":id})).send().await.unwrap().json().await.unwrap();
        assert_eq!(canceled.status.state, TaskState::Canceled);
    }
    let mut next = asynchronous("next", "wait"); next["message"]["contextId"] = json!(context);
    let next: Value = server.post("p", "worker", "/message:send").json(&next).send().await.unwrap().json().await.unwrap();
    let next_id = next["task"]["id"].as_str().unwrap();
    assert_ne!(next_id, id);
    let _ = server.post("p", "worker", &format!("/tasks/{id}:cancel")).send().await.unwrap();
    assert_eq!(server.task("p", "worker", next_id).await.status.state, TaskState::Working);
    let _ = server.post("p", "worker", &format!("/tasks/{next_id}:cancel")).send().await.unwrap();
    server.close().await;
}

#[tokio::test]
async fn authentication_task_and_context_isolation_cover_multiple_bots_and_providers() {
    let server = Server::new(&[("p", &["worker", "other"]), ("q", &["worker"])], true).await;
    let response = server.client.post(format!("{}/message:send", server.endpoint("p", "worker"))).json(&request("none", "hello")).send().await.unwrap();
    assert_eq!(response.status(), 401);
    let response = server.client.post(format!("{}/message:send", server.endpoint("q", "worker"))).bearer_auth("token-p").json(&request("wrong", "hello")).send().await.unwrap();
    assert_eq!(response.status(), 401);
    let mut request = request("same-message", "hello"); request["message"]["contextId"] = json!("same-context");
    let mut ids = Vec::new();
    for (provider, bot) in [("p", "worker"), ("p", "other"), ("q", "worker")] {
        let task: Value = server.post(provider, bot, "/message:send").json(&request).send().await.unwrap().json().await.unwrap();
        ids.push(task["task"]["id"].as_str().unwrap().to_owned());
    }
    assert_ne!(ids[0], ids[1]); assert_ne!(ids[0], ids[2]);
    assert!(server.mock.turns.lock().unwrap().iter().all(|turn| turn.1.is_none()));
    for (provider, bot) in [("p", "other"), ("q", "worker")] {
        for suffix in ["", ":cancel", ":subscribe"] {
            let path = format!("/tasks/{}{suffix}", ids[0]);
            let builder = if suffix.is_empty() { server.get(provider, bot, &path) } else { server.post(provider, bot, &path).json(&json!({})) };
            let response = builder.send().await.unwrap();
            assert_eq!(response.status(), 404);
            let value: Value = response.json().await.unwrap();
            assert_eq!(value["details"][0]["reason"], "TASK_NOT_FOUND");
        }
    }
    server.close().await;
}

#[tokio::test]
async fn malformed_unsupported_and_version_errors_do_not_start_an_engine() {
    let server = Server::new(&[("p", &["worker"])], true).await;
    for (pointer, value) in [
        ("role", json!("ROLE_AGENT")), ("parts", json!([])), ("parts", json!([{"data":{"secret":true}}])),
        ("messageId", json!("")), ("contextId", json!("")), ("extensions", json!(["unsupported"])),
    ] {
        let mut body = request("invalid", "hello"); body["message"][pointer] = value;
        assert_eq!(server.post("p", "worker", "/message:send").json(&body).send().await.unwrap().status(), 400);
    }
    for configuration in [json!({"historyLength":-1}), json!({"acceptedOutputModes":["image/png"]})] {
        let mut body = request("invalid", "hello"); body["configuration"] = configuration;
        assert_eq!(server.post("p", "worker", "/message:send").json(&body).send().await.unwrap().status(), 400);
    }
    let mut tenant = request("tenant", "hello"); tenant["tenant"] = json!("untrusted");
    assert_eq!(server.post("p", "worker", "/message:send").json(&tenant).send().await.unwrap().status(), 400);
    let response = server.post("p", "worker", "/message:send").header("a2a-version", "0.3").json(&request("version", "hello")).send().await.unwrap();
    let value: Value = response.json().await.unwrap(); assert_eq!(value["details"][0]["reason"], "VERSION_NOT_SUPPORTED");
    let response = server.post("p", "worker", "").header("content-type", "application/json").body("{").send().await.unwrap();
    let value: Value = response.json().await.unwrap(); assert_eq!(value["error"]["code"], -32700);
    let response = server.post("p", "worker", "").json(&json!({"jsonrpc":"2.0","id":4,"method":"message/send","params":{}})).send().await.unwrap();
    let value: Value = response.json().await.unwrap(); assert_eq!(value["error"]["code"], -32601); assert_eq!(value["id"], 4);
    let response = server.post("p", "worker", "/message:send").json(&request("oversized", &"x".repeat(1024 * 1024))).send().await.unwrap();
    assert_eq!(response.status(), 413);
    assert!(server.mock.turns.lock().unwrap().is_empty());
    server.close().await;
}

#[tokio::test]
async fn failures_are_terminal_redacted_and_unsupported_interaction_does_not_hang() {
    let server = Server::new(&[("p", &["worker"])], true).await;
    for prompt in ["fail", "interactive", "large"] {
        let response = server.post("p", "worker", "/message:send").json(&request(prompt, prompt)).send().await.unwrap();
        assert_eq!(response.status(), 200);
        let body = response.text().await.unwrap();
        assert!(!body.contains("private-engine-diagnostic"));
        if prompt == "interactive" { assert_eq!(*server.mock.decisions.lock().unwrap(), vec![json!({"decision":"deny"})]); }
        let value: Value = serde_json::from_str(&body).unwrap();
        assert_eq!(value["task"]["status"]["state"], "TASK_STATE_FAILED", "{body}");
        assert!(server.states[0].sessions.active_run("worker", &format!("a2a:{}", value["task"]["contextId"].as_str().unwrap())).await.is_none());
    }
    server.close().await;
}

#[tokio::test]
async fn jsonrpc_stream_query_list_and_cancel_share_rest_task_identity() {
    let server = Server::new(&[("p", &["worker"])], true).await;
    let response = server.post("p", "worker", "").json(&json!({"jsonrpc":"2.0","id":"rpc-send","method":"SendMessage","params":asynchronous("rpc", "wait")})).send().await.unwrap();
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["id"], "rpc-send");
    let id = body["result"]["task"]["id"].as_str().unwrap();
    let response = server.post("p", "worker", "").json(&json!({"jsonrpc":"2.0","id":7,"method":"GetTask","params":{"id":id}})).send().await.unwrap();
    let body: Value = response.json().await.unwrap(); assert_eq!(body["result"]["id"], id);
    let subscribed = server.post("p", "worker", "").json(&json!({"jsonrpc":"2.0","id":8,"method":"SubscribeToTask","params":{"id":id}})).send().await.unwrap();
    let response = server.post("p", "worker", "").json(&json!({"jsonrpc":"2.0","id":9,"method":"CancelTask","params":{"id":id}})).send().await.unwrap();
    let body: Value = response.json().await.unwrap(); assert_eq!(body["result"]["status"]["state"], "TASK_STATE_CANCELED");
    let frames = events(&subscribed.text().await.unwrap());
    assert!(frames.iter().all(|frame| frame["id"] == 8 && frame["jsonrpc"] == "2.0"));
    assert_eq!(frames.last().unwrap()["result"]["statusUpdate"]["status"]["state"], "TASK_STATE_CANCELED");
    let response = server.post("p", "worker", "").json(&json!({"jsonrpc":"2.0","id":10,"method":"SendStreamingMessage","params":request("rpc-stream", "hello")})).send().await.unwrap();
    let frames = events(&response.text().await.unwrap());
    assert!(frames.iter().all(|frame| frame["id"] == 10 && frame["result"].is_object()));
    let response = server.post("p", "worker", "").json(&json!({"jsonrpc":"2.0","id":11,"method":"ListTasks","params":{"pageSize":1}})).send().await.unwrap();
    let body: Value = response.json().await.unwrap();
    assert_eq!(body["result"]["totalSize"], 2); assert_eq!(body["result"]["tasks"].as_array().unwrap().len(), 1);
    let page_token = body["result"]["nextPageToken"].as_str().unwrap(); assert!(!page_token.is_empty());
    let response = server.get("p", "worker", "/tasks").query(&[("pageSize", "1"), ("pageToken", page_token)]).send().await.unwrap();
    let next: Value = response.json().await.unwrap();
    assert_eq!(next["tasks"][0]["id"], id); assert_eq!(next["nextPageToken"], "");
    server.close().await;
}

#[tokio::test]
async fn concurrent_retries_start_one_engine_and_opaque_bot_refs_remain_one_route_segment() {
    let server = Server::new(&[("p", &["coder/中?#"])], true).await;
    let card: a2a::AgentCard = server.get("p", "coder/中?#", "/.well-known/agent-card.json").send().await.unwrap().json().await.unwrap();
    assert_eq!(card.supported_interfaces[0].url, server.endpoint("p", "coder/中?#"));
    let sends = (0..12).map(|_| async {
        server.post("p", "coder/中?#", "/message:send").json(&asynchronous("identical", "wait"))
            .send().await.unwrap().json::<Value>().await.unwrap()
    });
    let responses = futures::future::join_all(sends).await;
    let id = responses[0]["task"]["id"].as_str().unwrap();
    assert!(responses.iter().all(|response| response["task"]["id"] == id));
    let _ = server.post("p", "coder/中?#", &format!("/tasks/{id}:cancel")).send().await.unwrap();
    assert_eq!(server.mock.turns.lock().unwrap().len(), 1);
    server.close().await;
}
