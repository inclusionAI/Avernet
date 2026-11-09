use super::fixture::{Cli, stdout};
use serde_json::{Value, json};
use wiremock::{Mock, ResponseTemplate, matchers::{body_json, header, method, path, query_param}};

fn delivery(status: &str) -> Value {
    json!({"delivery_id": "delivery-1", "message_id": "message-1", "status": status,
        "wait_reason": "waiting_for_bot", "state_version": 3})
}

fn submit(state: &str) -> Value {
    json!({"run_id": "run-1", "bot_uuid": "bot-target", "session_id": "session-1", "status": state,
        "delivery": delivery("queued")})
}

fn status(state: &str) -> Value {
    json!({"run_id": "run-1", "bot_uuid": "bot-target", "from_bot_id": "bot-source", "session_id": "session-1",
        "state": state, "response": {"content": "The final answer: ✓"}, "error_message": null,
        "created_at_ms": 1, "updated_at_ms": 2, "completed_at_ms": 3, "expires_at_ms": 1000,
        "version": 9, "content_truncated": true, "is_terminal": matches!(state, "completed" | "failed" | "cancelled"),
        "delivery": delivery(if state == "completed" { "completed" } else { "running" })})
}

async fn submission(cli: &Cli, result: ResponseTemplate, detach: bool, budget: u64, body: Value) {
    Mock::given(method("POST")).and(path("/bots/bot-target/chat-async"))
        .and(header("authorization", "Bearer test-command-token"))
        .and(header("X-BCS-CHAT-VERSION", "3"))
        .and(header("X-BCS-Client-Detach", detach.to_string()))
        .and(header("X-BCS-Client-Wait-Timeout-Ms", budget.to_string()))
        .and(body_json(body)).respond_with(result).expect(1).mount(&cli.server).await;
}

fn default_body() -> Value {
    json!({"message": "Hello", "from": null, "response_mode": "after-last-tool-call"})
}

async fn poll(cli: &Cli, result: ResponseTemplate) {
    Mock::given(method("GET")).and(path("/chat/runs/run-1"))
        .and(header("authorization", "Bearer test-command-token"))
        .and(header("X-BCS-CHAT-VERSION", "3"))
        .and(query_param("since_version", "0")).and(query_param("wait_ms", "7"))
        .respond_with(result).expect(1).mount(&cli.server).await;
}

#[tokio::test]
async fn chat_encodes_composite_bot_uuid_as_a_path_segment() {
    let cli = Cli::new().await;
    Mock::given(method("POST"))
        .and(path("/bots/default%3A197262/chat-async"))
        .and(header("authorization", "Bearer test-command-token"))
        .and(header("X-BCS-CHAT-VERSION", "3"))
        .and(header("X-BCS-Client-Detach", "true"))
        .and(header("X-BCS-Client-Wait-Timeout-Ms", "60000"))
        .and(body_json(default_body()))
        .respond_with(ResponseTemplate::new(202).set_body_json(submit("pending")))
        .expect(1)
        .mount(&cli.server)
        .await;

    let output = cli.ok(
        &["--json"],
        &[
            "chat",
            "--bot-uuid",
            "default:197262",
            "--message",
            "Hello",
            "--detach",
        ],
    );

    let value: Value = serde_json::from_str(&stdout(&output)).unwrap();
    assert_eq!(value["submitted"], true);
    assert_eq!(value["run_id"], "run-1");
    cli.server.verify().await;
}

fn assert_ack(output: &std::process::Output, json_mode: bool) {
    let ack = "[chat] submitted run_id=run-1 session_id=session-1";
    if json_mode {
        assert!(String::from_utf8_lossy(&output.stderr).contains(ack));
        assert!(!stdout(output).contains(ack), "stdout must remain one JSON object");
    } else {
        assert!(stdout(output).contains(ack));
        assert!(!String::from_utf8_lossy(&output.stderr).contains(ack));
    }
}

#[tokio::test]
async fn chat_detach_admits_queued_runs_without_polling_and_preserves_request_metadata() {
    for state in ["pending", "submitted", "running", "completed"] {
        let cli = Cli::new().await;
        submission(&cli, ResponseTemplate::new(202).set_body_json(submit(state)), true, 60_000,
            json!({"message": "Hello", "from": null, "session_id": "stable-session", "tags": ["worker", "worker", "reviewer"],
                "response_mode": "full", "organization_code": "org-1"})).await;
        let output = cli.ok(&["--json"], &["invoke", "--bot-uuid", "bot-target", "--message", "Hello", "--detach",
            "--session-id", "stable-session", "--tag", " worker ", "--tag", "worker", "--tag", "reviewer",
            "--response-mode", "full", "--organization-code", " org-1 "]);
        let value: Value = serde_json::from_str(&stdout(&output)).unwrap();
        assert_eq!(value, json!({"submitted": true, "delivered": matches!(state, "running" | "completed"),
            "bot_uuid": "bot-target", "run_id": "run-1", "session_id": "session-1", "state": state,
            "delivery_status": "queued", "wait_reason": "waiting_for_bot", "error_message": null}));
        assert_ack(&output, true);
        cli.server.verify().await;
        assert_eq!(cli.server.received_requests().await.unwrap().len(), 1, "detach must not poll");
    }
}

#[tokio::test]
async fn chat_detach_rejected_delivery_is_not_a_successful_admission() {
    for delivery_status in ["rejected_capacity", "failed", "expired", "cancelled"] {
        let cli = Cli::new().await;
        let mut result = submit("pending");
        result["delivery"] = delivery(delivery_status);
        submission(&cli, ResponseTemplate::new(202).set_body_json(result), true, 60_000, default_body()).await;
        let output = cli.command(&[], &["chat", "-b", "bot-target", "-m", "Hello", "--detach"])
            .assert().failure().get_output().clone();
        let value: Value = serde_json::from_str(&stdout(&output)).unwrap();
        assert_eq!(value["delivery_status"], delivery_status);
        assert_eq!(value["submitted"], true);
        assert_eq!(value["delivered"], false);
        assert_eq!(value["run_id"], "run-1");
        assert_ack(&output, true);
        cli.server.verify().await;
        assert_eq!(cli.server.received_requests().await.unwrap().len(), 1);
    }
}

#[tokio::test]
async fn chat_detach_human_mode_distinguishes_queued_admission_from_delivery() {
    for state in ["pending", "running"] {
        let cli = Cli::new().await;
        submission(&cli, ResponseTemplate::new(202).set_body_json(submit(state)), true, 1234, default_body()).await;
        let output = cli.ok(&["--json", "--no-json"], &["chat", "-b", "bot-target", "-m", "Hello",
            "--detach", "--timeout-ms", "1234"]);
        let text = stdout(&output);
        assert_eq!(text.contains("Message submitted to bot-target"), state == "running");
        for expected in ["Run: run-1", "Session: session-1", &format!("State: {state}"),
            "Delivery: queued", "Waiting: waiting_for_bot"] {
            assert!(text.contains(expected), "{text}");
        }
        assert_ack(&output, false);
        cli.server.verify().await;
    }
}

#[tokio::test]
async fn chat_completed_blocking_flow_has_exact_json_or_human_response_and_ack_channel() {
    for human in [false, true] {
        let cli = Cli::new().await;
        submission(&cli, ResponseTemplate::new(202).set_body_json(submit("pending")), false, 1_800_000, default_body()).await;
        poll(&cli, ResponseTemplate::new(200).set_body_json(status("completed"))).await;
        let output = cli.ok(if human { &["--no-json"] } else { &[] },
            &["chat", "-b", "bot-target", "-m", "Hello", "--poll-wait-ms", "7"]);
        if human {
            let text = stdout(&output);
            for expected in ["Response from bot-target:", "The final answer: ✓", "Run: run-1", "Session: session-1",
                "State: completed", "Delivery: completed", "Waiting: waiting_for_bot"] {
                assert!(text.contains(expected), "{text}");
            }
        } else {
            assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), json!({
                "delivered": true, "submitted": true, "bot_uuid": "bot-target", "run_id": "run-1", "session_id": "session-1",
                "state": "completed", "delivery_status": "completed", "wait_reason": "waiting_for_bot",
                "response": {"content": "The final answer: ✓"}, "error_message": null, "content_truncated": true}));
        }
        assert_ack(&output, !human);
        cli.server.verify().await;
    }
}

#[tokio::test]
async fn chat_wait_until_running_returns_on_running_or_already_completed_without_response_content() {
    for state in ["running", "completed"] {
        for human in [false, true] {
            let cli = Cli::new().await;
            submission(&cli, ResponseTemplate::new(202).set_body_json(submit("pending")), true, 60_000, default_body()).await;
            poll(&cli, ResponseTemplate::new(200).set_body_json(status(state))).await;
            let output = cli.ok(if human { &["--no-json"] } else { &[] }, &["chat", "-b", "bot-target", "-m", "Hello",
                "--wait-until", "running", "--poll-wait-ms", "7"]);
            assert!(!stdout(&output).contains("The final answer"));
            if human {
                assert!(stdout(&output).contains("Response from bot-target:"));
                assert!(stdout(&output).contains(&format!("State: {state}")));
            } else {
                let value: Value = serde_json::from_str(&stdout(&output)).unwrap();
                assert_eq!(value["delivered"], true);
                assert_eq!(value["submitted"], true);
                assert_eq!(value["state"], state);
                assert_eq!(value["response"]["content"], "");
                assert_eq!(value["content_truncated"], false);
            }
            assert_ack(&output, !human);
            cli.server.verify().await;
        }
    }
}

#[tokio::test]
async fn chat_submission_rejection_and_unreadable_ack_have_nonzero_structured_or_human_output() {
    for (response, state, error) in [
        (ResponseTemplate::new(403).set_body_string("bot access denied"), "submit_failed", "chat_async failed (403): bot access denied"),
        (ResponseTemplate::new(202).set_body_json(json!({"run_id": "run-with-missing-fields"})),
            "submit_indeterminate", "chat_async response unreadable:"),
    ] {
        for human in [false, true] {
            let cli = Cli::new().await;
            submission(&cli, response.clone(), false, 1_800_000, default_body()).await;
            let output = cli.command(if human { &["--no-json"] } else { &[] },
                &["chat", "-b", "bot-target", "-m", "Hello"]).assert().failure().get_output().clone();
            if human {
                let text = stdout(&output);
                assert!(text.contains("Run: none\nSession: none"), "{text}");
                assert!(text.contains(&format!("State: {state}")), "{text}");
                assert!(text.contains(&format!("Error: {error}")), "{text}");
            } else {
                let value: Value = serde_json::from_str(&stdout(&output)).unwrap();
                assert_eq!(value["state"], state);
                assert_eq!(value["delivered"], false);
                assert_eq!(value["submitted"], false);
                assert_eq!(value["bot_uuid"], "bot-target");
                assert!(value["run_id"].is_null());
                assert!(value["session_id"].is_null());
                assert!(value["error_message"].as_str().unwrap().contains(error));
                if state == "submit_indeterminate" {
                    assert!(value["error_message"].as_str().unwrap().contains("run may have been created server-side, ID unknown"));
                }
            }
            assert!(!String::from_utf8_lossy(&output.stderr).contains("[chat] submitted"));
            cli.server.verify().await;
            assert_eq!(cli.server.received_requests().await.unwrap().len(), 1);
        }
    }
}

#[tokio::test]
async fn chat_transport_failure_is_indeterminate_not_a_confirmed_rejection() {
    let cli = Cli::new().await;
    // Same-origin redirect exhaustion deterministically fails in send(), without
    // DNS, external connectivity, an unused port, or waiting for an HTTP timeout.
    let destination = format!("{}/bots/bot-target/chat-async", cli.server.uri());
    Mock::given(method("POST")).and(path("/bots/bot-target/chat-async"))
        .and(header("authorization", "Bearer test-command-token"))
        .and(header("X-BCS-CHAT-VERSION", "3"))
        .and(header("X-BCS-Client-Detach", "false"))
        .and(header("X-BCS-Client-Wait-Timeout-Ms", "1800000"))
        .and(body_json(default_body()))
        .respond_with(ResponseTemplate::new(307).insert_header("Location", destination.as_str()))
        .expect(10).mount(&cli.server).await;
    let output = cli.command(&[], &["chat", "-b", "bot-target", "-m", "Hello"])
        .assert().failure().get_output().clone();
    let value: Value = serde_json::from_str(&stdout(&output)).unwrap();
    assert_eq!(value["state"], "submit_indeterminate");
    assert_eq!(value["submitted"], false);
    assert!(value["run_id"].is_null());
    let error = value["error_message"].as_str().unwrap();
    assert!(error.contains("chat_async transport error:"), "{error}");
    assert!(error.contains("run may have been created server-side, ID unknown"));
    cli.server.verify().await;
}

#[tokio::test]
async fn chat_poll_error_preserves_ack_ids_and_is_a_nonzero_outcome_in_both_wait_modes() {
    for until_running in [false, true] {
        for human in [false, true] {
            let cli = Cli::new().await;
            submission(&cli, ResponseTemplate::new(202).set_body_json(submit("pending")), until_running,
                if until_running { 60_000 } else { 1_800_000 }, default_body()).await;
            poll(&cli, ResponseTemplate::new(503).set_body_string("poll store unavailable")).await;
            let mut args = vec!["chat", "-b", "bot-target", "-m", "Hello", "--poll-wait-ms", "7"];
            if until_running { args.extend(["--wait-until", "running"]); }
            let output = cli.command(if human { &["--no-json"] } else { &[] }, &args)
                .assert().failure().get_output().clone();
            if human {
                for expected in ["Run: run-1", "Session: session-1", "State: poll_error", "Error: chat_run_status failed:",
                    "poll store unavailable"] {
                    assert!(stdout(&output).contains(expected), "{}", stdout(&output));
                }
            } else {
                let value: Value = serde_json::from_str(&stdout(&output)).unwrap();
                assert_eq!(value["state"], "poll_error");
                assert_eq!(value["submitted"], true);
                assert_eq!(value["delivered"], false);
                assert_eq!(value["run_id"], "run-1");
                assert_eq!(value["session_id"], "session-1");
                assert_eq!(value["delivery_status"], "queued");
                assert!(value["error_message"].as_str().unwrap().contains("poll store unavailable"));
            }
            assert_ack(&output, !human);
            cli.server.verify().await;
        }
    }
}

#[tokio::test]
async fn chat_failed_or_cancelled_terminal_run_is_not_delivered() {
    for state in ["failed", "cancelled"] {
        for until_running in [false, true] {
            let cli = Cli::new().await;
            submission(&cli, ResponseTemplate::new(202).set_body_json(submit("pending")), until_running,
                if until_running { 60_000 } else { 1_800_000 }, default_body()).await;
            let mut result = status(state);
            result["error_message"] = json!("execution stopped");
            poll(&cli, ResponseTemplate::new(200).set_body_json(result)).await;
            let mut args = vec!["chat", "-b", "bot-target", "-m", "Hello", "--poll-wait-ms", "7"];
            if until_running { args.extend(["--wait-until", "running"]); }
            let output = cli.command(&[], &args).assert().failure().get_output().clone();
            let value: Value = serde_json::from_str(&stdout(&output)).unwrap();
            assert_eq!(value["state"], state);
            assert_eq!(value["delivered"], false);
            assert_eq!(value["submitted"], true);
            assert_eq!(value["error_message"], "execution stopped");
            assert_eq!(value["response"]["content"], "The final answer: ✓");
            cli.server.verify().await;
        }
    }
}

#[tokio::test]
async fn chat_blocking_poll_advances_since_version_before_completion() {
    let cli = Cli::new().await;
    submission(&cli, ResponseTemplate::new(202).set_body_json(submit("pending")), false, 1_800_000, default_body()).await;
    let mut running = status("running");
    running["version"] = json!(4);
    poll(&cli, ResponseTemplate::new(200).set_body_json(running)).await;
    Mock::given(method("GET")).and(path("/chat/runs/run-1"))
        .and(header("authorization", "Bearer test-command-token")).and(header("X-BCS-CHAT-VERSION", "3"))
        .and(query_param("since_version", "4")).and(query_param("wait_ms", "7"))
        .respond_with(ResponseTemplate::new(200).set_body_json(status("completed")))
        .expect(1).mount(&cli.server).await;
    let output = cli.ok(&[], &["chat", "-b", "bot-target", "-m", "Hello", "--poll-wait-ms", "7"]);
    let value: Value = serde_json::from_str(&stdout(&output)).unwrap();
    assert_eq!(value["state"], "completed");
    assert_eq!(value["response"]["content"], "The final answer: ✓");
    cli.server.verify().await;
    assert_eq!(cli.server.received_requests().await.unwrap().len(), 3);
}

#[tokio::test]
async fn chat_status_json_and_human_modes_send_nonblocking_poll_and_preserve_delivery() {
    for flags in [vec![], vec!["--json"], vec!["--no-json"], vec!["--json", "--no-json"]] {
        let cli = Cli::new().await;
        let result = status("running");
        Mock::given(method("GET")).and(path("/chat/runs/run-1"))
            .and(header("authorization", "Bearer test-command-token")).and(header("X-BCS-CHAT-VERSION", "3"))
            .and(query_param("since_version", "0")).and(query_param("wait_ms", "0"))
            .respond_with(ResponseTemplate::new(200).set_body_json(result.clone()))
            .expect(1).mount(&cli.server).await;
        let output = cli.ok(&flags, &["chat-run", "status", "--run-id", "run-1"]);
        if flags.contains(&"--no-json") { assert_eq!(stdout(&output), "Run: run-1\nState: running\nDelivery: running\n"); }
        else { assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), result); }
        assert!(!String::from_utf8_lossy(&output.stderr).contains("[chat] submitted"));
        cli.server.verify().await;
    }
}

#[tokio::test]
async fn chat_cancel_completed_is_a_deterministic_noop_without_polling_or_resubmission() {
    for human in [false, true] {
        let cli = Cli::new().await;
        let result = json!({"run_id": "run-1", "cancelled": false, "state": "completed",
            "response": {"content": "Already finished"}, "error_message": null, "version": 9,
            "content_truncated": false, "delivery": delivery("completed")});
        Mock::given(method("POST")).and(path("/chat/runs/run-1/cancel"))
            .and(header("authorization", "Bearer test-command-token")).and(header("X-BCS-CHAT-VERSION", "3"))
            .respond_with(ResponseTemplate::new(200).set_body_json(result.clone()))
            .expect(1).mount(&cli.server).await;
        let output = cli.ok(if human { &["--no-json"] } else { &[] }, &["chat-run", "cancel", "--run-id", "run-1"]);
        if human {
            assert_eq!(stdout(&output), "Run: run-1\nState: completed\nDelivery: completed\n");
            assert!(!stdout(&output).contains("Cancellation is not confirmed"));
        } else {
            assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), result);
        }
        cli.server.verify().await;
        assert_eq!(cli.server.received_requests().await.unwrap().len(), 1);
    }
}

#[tokio::test]
async fn chat_cancel_running_contract_distinguishes_confirmed_and_unconfirmed_cancellation() {
    for (state, cancelled, delivery_status) in [
        ("cancelled", true, "cancelled"), ("running", false, "cancelling"), ("running", false, "cancel_unknown"),
    ] {
        for human in [false, true] {
            let cli = Cli::new().await;
            let result = json!({"run_id": "run-1", "cancelled": cancelled, "state": state, "response": {"content": ""},
                "error_message": null, "version": 10, "content_truncated": false, "delivery": delivery(delivery_status)});
            Mock::given(method("POST")).and(path("/chat/runs/run-1/cancel"))
                .and(header("authorization", "Bearer test-command-token")).and(header("X-BCS-CHAT-VERSION", "3"))
                .respond_with(ResponseTemplate::new(200).set_body_json(result.clone()))
                .expect(1).mount(&cli.server).await;
            let output = cli.ok(if human { &["--no-json"] } else { &[] }, &["chat-run", "cancel", "--run-id", "run-1"]);
            if human {
                let suffix = if cancelled { "" } else { "Cancellation is not confirmed; query status again.\n" };
                assert_eq!(stdout(&output), format!("Run: run-1\nState: {state}\nDelivery: {delivery_status}\n{suffix}"));
            } else { assert_eq!(serde_json::from_str::<Value>(&stdout(&output)).unwrap(), result); }
            cli.server.verify().await;
            assert_eq!(cli.server.received_requests().await.unwrap().len(), 1);
        }
    }
}

#[tokio::test]
async fn chat_run_status_and_cancel_propagate_http_or_invalid_response_failures() {
    for operation in ["status", "cancel"] {
        for human in [false, true] {
            for malformed in [false, true] {
                let cli = Cli::new().await;
                let route = if operation == "status" { "/chat/runs/run-1" } else { "/chat/runs/run-1/cancel" };
                Mock::given(method(if operation == "status" { "GET" } else { "POST" })).and(path(route))
                    .and(header("authorization", "Bearer test-command-token")).and(header("X-BCS-CHAT-VERSION", "3"))
                    .respond_with(if malformed { ResponseTemplate::new(200).set_body_string("not-json") }
                        else { ResponseTemplate::new(404).set_body_string("unknown run") })
                    .expect(1).mount(&cli.server).await;
                let output = cli.command(if human { &["--no-json"] } else { &[] }, &["chat-run", operation, "--run-id", "run-1"])
                    .assert().failure().get_output().clone();
                let stderr = String::from_utf8_lossy(&output.stderr);
                let expected = if malformed { format!("Invalid chat_run_{operation} response") }
                    else { format!("chat_run_{operation} failed") };
                assert!(stderr.contains(&expected), "{stderr}");
                if !malformed { assert!(stderr.contains("unknown run"), "{stderr}"); }
                assert!(stdout(&output).is_empty(), "must not print a success result");
                cli.server.verify().await;
            }
        }
    }
}

#[tokio::test]
async fn chat_rejects_conflicting_wait_flags_and_invalid_budgets_without_network() {
    let cli = Cli::new().await;
    for (extra, error) in [
        (vec!["--detach", "--wait-until", "running"], "cannot be used with"),
        (vec!["--timeout-ms", "0"], "invalid value"),
        (vec!["--timeout-ms", "86400001"], "invalid value"),
        (vec!["--response-mode", "summary"], "invalid value"),
    ] {
        let mut args = vec!["chat", "-b", "bot-target", "-m", "Hello"];
        args.extend(extra);
        cli.fail(&[], &args, error);
    }
    assert!(cli.server.received_requests().await.unwrap().is_empty());
}
