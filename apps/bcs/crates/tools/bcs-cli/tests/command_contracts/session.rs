use super::fixture::{Cli, stdout};
use serde_json::{Value, json};
use wiremock::{Mock, ResponseTemplate, matchers::{body_json, header, method, path, query_param}};

#[tokio::test]
async fn session_commands_preserve_requests_and_both_output_modes() {
    let cases: Vec<(Vec<&str>, &str, &str, Option<Value>, Value, &str)> = vec![
        (vec!["session", "create", "--group", "g", "--title", "Review", "--kind", "chat", "--input", "{\"topic\":1}", "--meta", "{\"source\":\"cli\"}", "--group-context-delivery", "inject"],
         "POST", "/groups/g/sessions", Some(json!({"session_title":"Review","session_kind":"chat","input":{"topic":1},"meta":{"source":"cli"},"group_context_delivery":"inject"})),
         json!({"session_id":"s","session_kind":"chat","status":"running"}), "Session created: s (kind=chat, status=running)"),
        (vec!["session", "list", "--group", "g", "--status", "running", "--q", "Review", "--participant", "bot-peer", "--offset", "2", "--limit", "3"],
         "GET", "/groups/g/sessions", None, json!({"items":[{"session_id":"s","status":"running","session_title":"Review"}]}), "s [running] Review"),
        (vec!["session", "get", "s"], "GET", "/sessions/s", None, json!({"session_id":"s","status":"running"}), "\"session_id\": \"s\""),
        (vec!["session", "chat", "--session", "s", "--message", "你好"], "POST", "/sessions/s/chat", Some(json!({"message":"你好"})),
         json!({"delivered_count":2,"failed_count":1,"mentions":["bot-peer"]}), "@mentions: bot-peer"),
        (vec!["session", "messages", "s", "--view-bot", "bot-peer", "--limit", "4", "--before", "100"], "GET", "/sessions/s/messages", None,
         json!([{"ts":10,"sender":"peer","content":"你好"},{"timestamp":11,"from":"other","message":"short"}]), "[10] peer: 你好"),
        (vec!["session", "patch", "s", "--title", "Approved"], "PATCH", "/sessions/s", Some(json!({"session_title":"Approved"})),
         json!({"session_id":"s","session_title":"Approved"}), "Patched: s title=\"Approved\""),
        (vec!["session", "complete", "s", "--output", "{\"done\":true}", "--error", "reported error"], "POST", "/sessions/s/complete", Some(json!({"output":{"done":true},"error":"reported error"})),
         json!({"session_id":"s","status":"completed"}), "Completed: s status=completed"),
        (vec!["session", "add-member", "s", "--bot-uuid", "bot-peer", "--role", "consultant"], "POST", "/sessions/s/members", Some(json!({"bot_uuid":"bot-peer","role":"consultant"})),
         json!({"participants":[{"bot_uuid":"bot-peer","role":"consultant"}]}), "Added member bot-peer to s (role=consultant)"),
        (vec!["session", "remove-member", "s", "bot-peer"], "DELETE", "/sessions/s/members/bot-peer", None,
         json!({"removed":true}), "Removed member bot-peer from s"),
        (vec!["session", "set-member-mode", "s", "bot-peer", "--mode", "muted"], "PATCH", "/sessions/s/members/bot-peer", Some(json!({"mode":"muted"})),
         json!({"mode":"muted"}), "Mode set bot-peer@s -> muted"),
        (vec!["session", "invite-link", "s", "--ttl-seconds", "60"], "POST", "/sessions/s/invite-link", Some(json!({"ttl_seconds":60})),
         json!({"link":"https://example.test/invite","expires_at":123}), "Expires: 123 (Unix ms)"),
    ];
    for (args, verb, endpoint, body, response, text) in cases {
        for structured in [false, true] {
            let cli = Cli::new().await;
            let mut mock = Mock::given(method(verb)).and(path(endpoint))
                .and(header("authorization", "Bearer test-command-token"));
            if let Some(body) = &body { mock = mock.and(body_json(body)); }
            if args[1] == "list" {
                mock = mock.and(query_param("status", "running")).and(query_param("q", "Review"))
                    .and(query_param("participant", "bot-peer")).and(query_param("offset", "2")).and(query_param("limit", "3"));
            }
            if args[1] == "messages" {
                mock = mock.and(query_param("view_bot_id", "bot-peer")).and(query_param("limit", "4")).and(query_param("before", "100"));
            }
            mock.respond_with(ResponseTemplate::new(200).set_body_json(&response)).expect(1).mount(&cli.server).await;
            let flags: &[&str] = if structured { &["--json"] } else { &[] };
            let output = cli.command(flags, &args).env("BCS_DEBUG", "true").assert().success().get_output().clone();
            if structured { assert_eq!(serde_json::from_slice::<Value>(&output.stdout).unwrap(), response); }
            else { assert!(stdout(&output).contains(text), "args={args:?}, output={}", stdout(&output)); }
        }
    }
}

#[tokio::test]
async fn session_errors_and_utf8_history_are_not_hidden() {
    let cli = Cli::new().await;
    for (args, needle) in [
        (vec!["session","create","--group","g","--input","bad"], "Invalid --input JSON"),
        (vec!["session","create","--group","g","--meta","bad"], "Invalid --meta JSON"),
        (vec!["session","complete","s","--output","@"], "'@' must be followed"),
    ] { cli.fail(&[], &args, needle); }
    assert!(cli.server.received_requests().await.unwrap().is_empty());
    Mock::given(method("GET")).and(path("/sessions/missing"))
        .respond_with(ResponseTemplate::new(404).set_body_string("not found")).expect(1).mount(&cli.server).await;
    cli.fail(&[], &["session","get","missing"], "Get session failed (404 Not Found)");
    Mock::given(method("GET")).and(path("/sessions/s/messages"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!([{"ts":5,"sender":"peer","content":"中".repeat(81)}])))
        .expect(1).mount(&cli.server).await;
    let out = stdout(&cli.ok(&[], &["session","messages","s"]));
    assert!(out.contains(&"中".repeat(80))); assert!(!out.contains(&"中".repeat(81)));
    Mock::given(method("POST")).and(path("/sessions/s/complete"))
        .and(body_json(json!({}))).respond_with(ResponseTemplate::new(200).set_body_json(json!({"already_completed":true})))
        .expect(1).mount(&cli.server).await;
    assert!(stdout(&cli.ok(&[], &["session","complete","s"])).contains("Already completed: s"));
    Mock::given(method("POST")).and(path("/sessions/s/invite-link"))
        .and(body_json(json!({}))).respond_with(ResponseTemplate::new(200).set_body_json(json!({"invite_token":"test-invite"})))
        .expect(1).mount(&cli.server).await;
    assert!(stdout(&cli.ok(&[], &["session","invite-link","s"])).contains("test-invite"));
}

#[tokio::test]
async fn service_commands_preserve_submission_status_wait_and_output() {
    for flags in [&[][..], &["--json"][..]] {
        let cli = Cli::new().await;
        let status = json!({"session_id":"g:s","group_id":"g","session_kind":"service_invocation","status":"completed","reused":false,"output":{"answer":"ready"},"state_machine_run_id":"run-1"});
        Mock::given(method("POST")).and(path("/services/g/sessions"))
            .and(body_json(json!({"input":{"question":"review"},"meta":{"source":"cli"},"caller_id":"operator","session_title":"Review","session_id":"g:s"})))
            .and(header("authorization", "Bearer test-command-token"))
            .respond_with(ResponseTemplate::new(202).set_body_json(&status)).expect(2).mount(&cli.server).await;
        Mock::given(method("GET")).and(path("/services/g/sessions/g%3As"))
            .respond_with(ResponseTemplate::new(200).set_body_json(&status)).expect(4).mount(&cli.server).await;
        let input = cli.home.path().join("input.json"); std::fs::write(&input, "{\"question\":\"review\"}").unwrap();
        let input_arg = format!("@{}", input.display());
        let invoke = ["service","invoke","--group","g","--input",&input_arg,"--meta","{\"source\":\"cli\"}","--caller-id","operator","--title","Review","--session-id","g:s"];
        for detached in [true, false] {
            let mut args = invoke.to_vec(); if detached { args.push("--detach"); }
            let output = cli.command(flags, &args).env("BCS_DEBUG", "true").assert().success().get_output().clone();
            if flags.is_empty() { assert!(stdout(&output).contains(if detached { "Invocation submitted: g:s" } else { "Invocation completed: g:s" })); }
            else { assert_eq!(serde_json::from_slice::<Value>(&output.stdout).unwrap(), status); }
        }
        for args in [vec!["service","status","g:s"],vec!["service","status","g:s","--group","g"],vec!["service","wait","g:s","--timeout-ms","100"]] {
            let output = cli.command(flags, &args).env("BCS_DEBUG", "true").assert().success().get_output().clone();
            if flags.is_empty() { assert!(stdout(&output).contains("g:s")); assert!(stdout(&output).contains("ready")); }
            else { assert_eq!(serde_json::from_slice::<Value>(&output.stdout).unwrap(), status); }
        }
    }
}

#[tokio::test]
async fn service_rejects_bad_input_and_bounded_wait_reports_timeout() {
    let cli = Cli::new().await;
    for args in [vec!["service","status","missing-prefix"], vec!["service","status",":empty-prefix"]] {
        cli.fail(&[], &args, "Cannot infer group");
    }
    for (input,needle) in [("@","'@' must be followed"),("not-json","Invalid JSON"),("@/missing/cli-contract-input","Failed to read")] {
        cli.fail(&[], &["service","invoke","--group","g","--input",input], needle);
    }
    cli.fail(&[], &["service","invoke","--group","g","--meta","bad"], "--meta:");
    assert!(cli.server.received_requests().await.unwrap().is_empty());
    Mock::given(method("GET")).and(path("/services/g/sessions/g%3As"))
        .respond_with(ResponseTemplate::new(200).set_delay(std::time::Duration::from_millis(10)).set_body_json(json!({"session_id":"g:s","status":"running"})))
        .expect(1).mount(&cli.server).await;
    cli.fail(&[], &["service","wait","g:s","--timeout-ms","1"], "Timed out after 1 ms");
}
