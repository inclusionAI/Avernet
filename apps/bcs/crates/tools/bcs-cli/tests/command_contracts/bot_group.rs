use super::fixture::{Cli, stdout};
use serde_json::{Value, json};
use wiremock::matchers::{body_json, body_partial_json, header, method, path, query_param};
use wiremock::{Mock, MockBuilder, Request, ResponseTemplate};

fn api(verb: &str, route: &str) -> MockBuilder {
    Mock::given(method(verb)).and(path(route))
        .and(header("authorization", "Bearer test-command-token"))
}

fn connected(is_new: bool) -> Value {
    json!({"is_new": is_new, "token": "new-test-token", "bot_uuid": "bot-connected"})
}

fn group(session: Option<&str>) -> Value {
    json!({"id": "group-a", "driver_bot": "bot-source",
        "participants": ["bot-source", "bot-worker"], "session_id": session,
        "chat_url": "https://example.test/chat/group-a"})
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn health_reports_human_json_and_unhealthy_exit() {
    for healthy in [true, false] {
        let cli = Cli::new().await;
        Mock::given(method("GET")).and(path("/health"))
            .and(header("cookie", "contract=health"))
            .and(|r: &Request| !r.headers.contains_key("authorization"))
            .respond_with(ResponseTemplate::new(if healthy { 200 } else { 503 }))
            .expect(2).mount(&cli.server).await;
        for human in [false, true] {
            let mut flags = vec!["--cookie", "contract=health"];
            if human { flags.push("--no-json"); }
            let output = cli.command(&flags, &["health"]).output().unwrap();
            assert_eq!(output.status.success(), healthy);
            let text = stdout(&output);
            if human {
                assert!(text.contains(if healthy { "✓ BCS is healthy at" } else { "✗ BCS health check failed" }), "{text}");
            } else {
                let result: Value = serde_json::from_str(&text).unwrap();
                assert_eq!(result["status"], if healthy { "healthy" } else { "unhealthy" });
                assert!(result["message"].as_str().unwrap().contains(&cli.server.uri()));
            }
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn connect_sends_selected_credentials_and_persists_negotiated_session() {
    for human in [false, true] {
        let cli = Cli::new().await;
        Mock::given(method("POST")).and(path("/bots/connect"))
            .and(header("authorization", "Bearer explicit-test-token"))
            .and(header("cookie", "contract=connect"))
            .and(body_partial_json(json!({"token": "explicit-test-token", "bot_id": "configured-bot",
                "protocol_version": bcs_protocol::BCS_PROTOCOL_VERSION})))
            .respond_with(ResponseTemplate::new(200).set_body_json(connected(true)))
            .expect(1).mount(&cli.server).await;
        let flags = if human { vec!["--no-json", "--cookie", "contract=connect"] }
            else { vec!["--json", "--cookie", "contract=connect"] };
        let text = stdout(&cli.ok(&flags, &["connect", "--token", "explicit-test-token", "--bot-id", "configured-bot"]));
        if human {
            assert!(text.contains("✓ New bot connected to BCS network:"));
            assert!(text.contains("Bot UUID: bot-connected"));
            assert!(text.contains("Token: new-test..."));
            assert!(text.contains("Save this token for reconnection!"));
            assert!(!text.contains("new-test-token"));
        } else {
            let result: Value = serde_json::from_str(&text).unwrap();
            assert_eq!(result["bot_uuid"], "bot-connected");
            assert_eq!(result["token"], "new-test-token");
            assert_eq!(result["is_new"], true);
        }
        let saved: Value = serde_json::from_slice(&std::fs::read(cli.home.path().join(".bcs/session.json")).unwrap()).unwrap();
        assert_eq!(saved["bot_uuid"], "bot-connected");
        assert_eq!(saved["token"], "new-test-token");
        assert_eq!(saved["api_base_url"], cli.server.uri());
        assert_eq!(saved["bcs_url"], cli.server.uri().replacen("http://", "ws://", 1) + "/ws/bot");
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn connect_reconnect_and_actor_scoped_session_preservation() {
    let cli = Cli::new().await;
    api("POST", "/bots/connect")
        .and(body_partial_json(json!({"token": "test-command-token"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(connected(false)))
        .expect(1).mount(&cli.server).await;
    let text = stdout(&cli.ok(&["--no-json"], &["connect"]));
    assert!(text.contains("Bot already registered, reconnecting:"));

    let cli = Cli::new().await;
    let session_path = cli.home.path().join(".bcs/session.json");
    let before = std::fs::read(&session_path).unwrap();
    api("POST", "/bots/connect")
        .and(header("x-bcn-actor-bot-id", "actor-bot"))
        .and(body_partial_json(json!({"bot_id": "actor-bot", "token": "test-command-token"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(connected(false)))
        .expect(1).mount(&cli.server).await;
    let output = cli.command(&["--no-json"], &["connect"])
        .env("BCN_ACTOR_BOT_ID", "actor-bot").assert().success().get_output().clone();
    assert!(stdout(&output).contains("✓ Bot reconnected to BCS network:"));
    assert_eq!(std::fs::read(session_path).unwrap(), before);
    let output = cli.command(&[], &["connect", "--bot-id", "other-bot"])
        .env("BCN_ACTOR_BOT_ID", "actor-bot").assert().failure().get_output().clone();
    assert!(String::from_utf8_lossy(&output.stderr).contains("--bot-id must match BCN_ACTOR_BOT_ID"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn connect_reports_http_and_gateway_failures_without_replacing_session() {
    for (status, response, needle) in [
        (403, json!({"error": "connection denied"}), "connection denied"),
        (200, json!({"buserviceErrorCode": "USER_NOT_LOGIN", "bizOwner": "contract-owner"}), "Gateway error [USER_NOT_LOGIN]"),
        (200, json!({"unexpected": true}), "Invalid connect response"),
    ] {
        let cli = Cli::new().await;
        let session_path = cli.home.path().join(".bcs/session.json");
        let before = std::fs::read(&session_path).unwrap();
        api("POST", "/bots/connect").and(body_partial_json(json!({"token": "test-command-token"})))
            .respond_with(ResponseTemplate::new(status).set_body_json(response))
            .expect(2).mount(&cli.server).await;
        for flags in [&["--no-json"][..], &["--json"][..]] { cli.fail(flags, &["connect"], needle); }
        assert_eq!(std::fs::read(session_path).unwrap(), before);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn onboard_parses_all_skill_formats_and_renders_binding_results() {
    for (skills, expected) in [
        ("sql, debug, ", json!([{"name": "sql"}, {"name": "debug"}])),
        (r#"["sql", "debug"]"#, json!([{"name": "sql"}, {"name": "debug"}])),
        (r#"[{"name":"sql","description":"SQL analysis"},7,{"bad":true}]"#,
            json!([{"name": "sql", "description": "SQL analysis"}])),
    ] {
        let cli = Cli::new().await;
        let result = json!({"bot_uuid": "bot-source", "onboarded": true, "name": "SQL helper",
            "binding_results": {"chat": {"status": "success"}, "mail": {"status": "conflict", "message": "already bound"}},
            "unbound": ["old-channel"]});
        api("POST", "/bots/onboard").and(body_json(json!({
            "name": "SQL helper", "summary": "Query planning", "skills": expected,
            "domains": ["data", "ops"], "scopes": ["read", "write"],
            "binding_channels": {"chat": {"binding_key": "test-chat"}}
        }))).respond_with(ResponseTemplate::new(200).set_body_json(&result))
            .expect(2).mount(&cli.server).await;
        let args = ["onboard", "--name", "SQL helper", "--summary", "Query planning", "--skills", skills,
            "--domains", "data, ops", "--scopes", "read, write", "--binding-channels", r#"{"chat":{"binding_key":"test-chat"}}"#];
        let human = stdout(&cli.ok(&["--no-json"], &args));
        for expected in ["Bot ID: bot-source", "Name: SQL helper", "✓ chat: success", "✗ mail: conflict - already bound", "old-channel"] {
            assert!(human.contains(expected), "missing {expected:?}: {human}");
        }
        assert_eq!(serde_json::from_str::<Value>(&stdout(&cli.ok(&["--json"], &args))).unwrap(), result);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn onboard_defaults_and_invalid_binding_json() {
    let cli = Cli::new().await;
    api("POST", "/bots/onboard").and(body_json(json!({"name": "Minimal", "summary": null,
        "skills": [], "domains": [], "scopes": [], "binding_channels": null})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"bot_uuid": "bot-source", "onboarded": true, "name": "Minimal"})))
        .expect(1).mount(&cli.server).await;
    assert!(stdout(&cli.ok(&[], &["onboard", "--name", "Minimal"])).contains("Name: Minimal"));
    cli.fail(&[], &["onboard", "--name", "Minimal", "--binding-channels", "not-json"], "Invalid binding_channels JSON");
    assert_eq!(cli.server.received_requests().await.unwrap().len(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn onboard_web_uses_server_url_and_encoded_query_parameters() {
    let cli = Cli::new().await;
    api("GET", "/onboard/url").and(query_param("token", "test-command-token"))
        .and(query_param("name", "SQL & ops")).and(query_param("summary", "plan + review"))
        .and(query_param("skills", "sql,debug")).and(query_param("domains", "data,ops"))
        .and(query_param("scopes", "read,write")).and(header("cookie", "contract=onboard"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"onboard_url": "https://example.test/register?ticket=contract"})))
        .expect(2).mount(&cli.server).await;
    let args = ["onboard", "--web", "--name", "SQL & ops", "--summary", "plan + review", "--skills", "sql, debug",
        "--domains", "data, ops", "--scopes", "read, write"];
    let text = stdout(&cli.ok(&["--no-json", "--cookie", "contract=onboard"], &args));
    assert_eq!(text, "Registration URL:\nhttps://example.test/register?ticket=contract\n");
    let value: Value = serde_json::from_str(&stdout(&cli.ok(&["--json", "--cookie", "contract=onboard"], &args))).unwrap();
    assert_eq!(value, json!({"url": "https://example.test/register?ticket=contract"}));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn onboard_web_explains_unsupported_and_misconfigured_servers() {
    for (status, body, needle) in [
        (404, json!({}), "BCS server does not support onboard URL generation"),
        (400, json!({"error": "missing registration frontend"}), "Server configuration error"),
        (500, json!({"error": "onboard unavailable"}), "500"),
    ] {
        let cli = Cli::new().await;
        api("GET", "/onboard/url").and(query_param("name", "Minimal"))
            .and(query_param("token", "test-command-token"))
            .respond_with(ResponseTemplate::new(status).set_body_json(body))
            .expect(1).mount(&cli.server).await;
        cli.fail(&[], &["onboard", "--web", "--name", "Minimal"], needle);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn list_and_get_render_capabilities_and_default_visibility() {
    let cli = Cli::new().await;
    let bot = json!({"bot_uuid": "bot-worker", "capabilities": {"name": "SQL helper", "summary": "Query planning",
        "skills": [{"name": "sql"}, {"name": "debug"}], "domains": ["data", "ops"], "visibility": "public"}});
    api("GET", "/bots").and(query_param("onboarded", "true"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!([bot, {"bot_uuid": "bot-unnamed"}])))
        .expect(2).mount(&cli.server).await;
    api("GET", "/bots/bot-worker").respond_with(ResponseTemplate::new(200).set_body_json(&bot))
        .expect(2).mount(&cli.server).await;
    api("GET", "/bots/bot-unnamed")
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"bot_uuid": "bot-unnamed", "capabilities": {"visibility": ""}})))
        .expect(1).mount(&cli.server).await;
    // These legacy commands remain human-readable even with explicit --json.
    for flags in [&["--no-json"][..], &["--json"][..]] {
        assert_eq!(stdout(&cli.ok(flags, &["list"])), "Bots in network (2):\n  - bot-worker (SQL helper)\n    Query planning\n  - bot-unnamed (unnamed)\n");
        assert_eq!(stdout(&cli.ok(flags, &["get", "bot-worker"])), "Bot: bot-worker\n  Name: SQL helper\n  Summary: Query planning\n  Skills: sql, debug\n  Domains: data, ops\n  Visibility: public\n");
    }
    assert_eq!(stdout(&cli.ok(&[], &["get", "bot-unnamed"])), "Bot: bot-unnamed\n  Visibility: protected\n");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn discover_filters_and_formats_friend_provider_metadata() {
    let cli = Cli::new().await;
    let bots = json!({"bots": [{"bot_uuid": "bot-worker", "capabilities": {"name": "SQL helper"},
        "visibility": "public", "is_friend": true, "agent_code": "agent-sql",
        "provider_info": {"provider_id": "provider-a", "provider_name": "Example"}},
        {"bot_uuid": "bot-unnamed", "capabilities": {}, "visibility": "protected", "is_friend": false}], "count": 2});
    api("GET", "/bots/discover").and(query_param("q", "SQL & ops"))
        .and(query_param("skill", "sql")).and(query_param("skill", "debug"))
        .and(query_param("visibility", "public")).and(query_param("collaborate_bot", "bot-source"))
        .and(query_param("organization_code", "example org")).and(query_param("role", "reviewer"))
        .respond_with(ResponseTemplate::new(200).set_body_json(&bots))
        .expect(3).mount(&cli.server).await;
    let args = ["discover", "--query", "SQL & ops", "--skill", "sql", "--skill", "debug", "--visibility", "public",
        "--collaborate-bot", "bot-source", "--organization-code", " example org ", "--role", " reviewer "];
    let result: Value = serde_json::from_str(&stdout(&cli.ok(&[], &args))).unwrap();
    assert_eq!(result["count"], 2);
    assert_eq!(result["bots"][0]["provider_info"], bots["bots"][0]["provider_info"]);
    let human = stdout(&cli.ok(&["--no-json"], &args));
    assert!(human.contains("Discovered 2 bots:"));
    assert!(human.contains("bot-worker (SQL helper) [public] ★friend provider=Example/provider-a agent_code=agent-sql"));
    assert!(human.contains("bot-unnamed (unnamed) [protected]"));
    let mut local_human = args.to_vec();
    local_human.push("--no-json");
    assert_eq!(stdout(&cli.ok(&[], &local_human)), human);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn update_status_sends_dynamic_fields_and_reports_update_outcome() {
    for updated in [true, false] {
        let cli = Cli::new().await;
        api("POST", "/bots/status").and(body_partial_json(json!({"bot_uuid": "", "status": {
            "status": "busy", "dynamic_summary": "Reviewing", "load": 0.5}})))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"updated": updated,
                "bot_uuid": "bot-source", "status": {"status": "busy"}})))
            .expect(1).mount(&cli.server).await;
        let text = stdout(&cli.ok(&[], &["update-status", "--status", "busy", "--summary", "Reviewing", "--load", "0.5"]));
        assert_eq!(text, if updated { "✓ Status updated\n" } else { "✗ Failed to update status\n" });
        let requests = cli.server.received_requests().await.unwrap();
        let payload: Value = requests[0].body_json().unwrap();
        assert!(payload["status"]["updated_at"].as_u64().unwrap() > 0);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn bot_commands_propagate_backend_errors() {
    for (verb, route, args, query) in [
        ("POST", "/bots/onboard", vec!["onboard", "--name", "Minimal"], None),
        ("GET", "/bots", vec!["list"], Some(("onboarded", "true"))),
        ("GET", "/bots/missing", vec!["get", "missing"], None),
        ("GET", "/bots/discover", vec!["discover"], None),
        ("POST", "/bots/status", vec!["update-status", "--status", "busy"], None),
    ] {
        let cli = Cli::new().await;
        let mut mock = api(verb, route);
        if let Some((name, value)) = query { mock = mock.and(query_param(name, value)); }
        mock.respond_with(ResponseTemplate::new(503).set_body_json(json!({"error": "registry unavailable"})))
            .expect(2).mount(&cli.server).await;
        for flags in [&["--no-json"][..], &["--json"][..]] { cli.fail(flags, &args, "registry unavailable"); }
    }
    let cli = Cli::new().await;
    api("GET", "/bots/missing").respond_with(ResponseTemplate::new(200).set_body_json(json!({"error": "unknown bot", "status": 404})))
        .expect(1).mount(&cli.server).await;
    cli.fail(&[], &["get", "missing"], "unknown bot");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn create_regular_group_preserves_uuid_tags_context_and_session() {
    let cli = Cli::new().await;
    api("POST", "/groups").and(header("cookie", "contract=group"))
        .and(body_partial_json(json!({"driver_bot": "bot-source", "participants": [
            {"bot_uuid": "bot-source", "role": null, "tags": ["tenant-a"]},
            {"bot_uuid": "provider:100005", "role": null, "tags": ["worker-a", "worker-b"]}],
            "context": "Plan deployment", "topic": "Release", "create_initial_session": true})))
        .respond_with(ResponseTemplate::new(200).set_body_json(group(Some("session-initial"))))
        .expect(1).mount(&cli.server).await;
    let text = stdout(&cli.ok(&["--cookie", "contract=group"], &["create-group", "--driver", "bot-source",
        "--participants", " provider:100005 ", "--participant-tag", "bot-source=tenant-a",
        "--participant-tag", "provider:100005=worker-a", "--participant-tag", "provider:100005=worker-b",
        "--context", "Plan deployment", "--topic", "Release"]));
    for expected in ["Group created:", "ID: group-a", "Driver: bot-source", "Participants: bot-source, bot-worker",
        "Chat URL: https://example.test/chat/group-a", "Session: session-initial"] { assert!(text.contains(expected), "{text}"); }
    assert_eq!(cli.server.received_requests().await.unwrap().len(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn create_manager_group_assigns_roles_without_duplicating_manager() {
    for participants in ["bot-worker", "bot-source, bot-worker"] {
        let cli = Cli::new().await;
        api("POST", "/groups").and(body_partial_json(json!({"driver_bot": "bot-source",
            "group_strategy": "manager_worker", "create_initial_session": false,
            "participants": [{"bot_uuid": "bot-source", "role": "manager", "tags": ["lead"]},
                {"bot_uuid": "bot-worker", "role": "worker"}]})))
            .respond_with(ResponseTemplate::new(200).set_body_json(group(None)))
            .expect(1).mount(&cli.server).await;
        let text = stdout(&cli.ok(&["--json"], &["create-group", "--manager", "bot-source", "--participants", participants,
            "--participant-tag", "bot-source=lead", "--no-session"]));
        assert!(text.contains("Group created:"));
        assert!(!text.contains("Session:"));
        assert_eq!(cli.server.received_requests().await.unwrap().len(), 1);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn create_group_legacy_session_lookup_and_best_effort_failure() {
    for (status, session) in [(200, Some("legacy-session")), (503, None)] {
        let cli = Cli::new().await;
        api("POST", "/groups").and(body_partial_json(json!({"driver_bot": "bot-source", "create_initial_session": true})))
            .respond_with(ResponseTemplate::new(200).set_body_json(group(None)))
            .expect(1).mount(&cli.server).await;
        api("GET", "/groups/group-a/sessions").and(query_param("limit", "1"))
            .respond_with(ResponseTemplate::new(status).set_body_json(json!({"items": [{"id": "legacy-session"}]})))
            .expect(1).mount(&cli.server).await;
        let output = cli.ok(&[], &["create-group", "--driver", "bot-source", "--participants", "bot-worker"]);
        if let Some(session) = session { assert!(stdout(&output).contains(&format!("Session: {session}"))); }
        else {
            assert!(stdout(&output).contains("Group created:"));
            assert!(String::from_utf8_lossy(&output.stderr).contains("created but session lookup failed"));
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn create_group_refuses_silent_no_session_downgrade() {
    let cli = Cli::new().await;
    api("POST", "/groups").and(body_partial_json(json!({"create_initial_session": false})))
        .respond_with(ResponseTemplate::new(200).set_body_json(group(Some("unexpected-session"))))
        .expect(1).mount(&cli.server).await;
    cli.fail(&[], &["create-group", "--driver", "bot-source", "--participants", "bot-worker", "--no-session"],
        "the group and session have not been deleted");
    assert_eq!(cli.server.received_requests().await.unwrap().len(), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn group_input_validation_happens_before_http() {
    let cli = Cli::new().await;
    for (tag, needle) in [("missing-separator", "expected BOT_UUID=TAG"), ("=tag", "must not be empty"),
        ("bot-worker=", "must not be empty"), ("unknown=tag", "is not a group participant")] {
        cli.fail(&[], &["create-group", "--driver", "bot-source", "--participants", "bot-worker", "--participant-tag", tag], needle);
    }
    cli.fail(&[], &["create-group", "--participants", "bot-worker"], "--driver");
    cli.fail(&[], &["create-group", "--driver", "bot-source", "--manager", "bot-source", "--participants", "bot-worker"], "cannot be used with");
    cli.fail(&[], &["list-groups", "--batch-size", "0"], "Batch size must be greater than 0");
    assert!(cli.server.received_requests().await.unwrap().is_empty());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn group_get_status_terminate_and_add_member_render_real_responses() {
    let cli = Cli::new().await;
    let detail = json!({"id": "group-a", "status": "active", "driver_bot": "bot-source"});
    // The existing get-group client uses an unauthenticated read.
    Mock::given(method("GET")).and(path("/groups/group-a"))
        .and(|r: &Request| !r.headers.contains_key("authorization")).respond_with(ResponseTemplate::new(200).set_body_json(&detail))
        .expect(2).mount(&cli.server).await;
    let status = json!({"id": "group-a", "status": "closed"});
    api("PUT", "/groups/group-a/status").and(body_json(json!({"status": "closed", "reason": "Resolved"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(&status)).expect(2).mount(&cli.server).await;
    let terminated = json!({"id": "group-a", "status": "completed"});
    api("POST", "/groups/group-a/terminate").and(|r: &Request| r.body.is_empty())
        .respond_with(ResponseTemplate::new(200).set_body_json(&terminated)).expect(2).mount(&cli.server).await;
    api("POST", "/groups/group-a/members").and(body_json(json!({"bot_uuid": "bot-worker"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"member": {"role": "worker"}})))
        .expect(1).mount(&cli.server).await;
    assert_eq!(serde_json::from_str::<Value>(&stdout(&cli.ok(&["--json"], &["get-group", "--id", "group-a"]))).unwrap(), detail);
    assert!(stdout(&cli.ok(&["--no-json"], &["get-group", "--id", "group-a"])).starts_with("Group: {"));
    let args = ["group-status", "--group", "group-a", "--status", "closed", "--reason", "Resolved"];
    assert_eq!(serde_json::from_str::<Value>(&stdout(&cli.ok(&["--json"], &args))).unwrap(), status);
    assert_eq!(stdout(&cli.ok(&["--no-json"], &args)), "✓ Group status updated:\n  Group: group-a\n  Status: closed\n  Reason: Resolved\n");
    let args = ["terminate-group", "--group", "group-a"];
    assert_eq!(serde_json::from_str::<Value>(&stdout(&cli.ok(&["--json"], &args))).unwrap(), terminated);
    assert_eq!(stdout(&cli.ok(&["--no-json"], &args)), "✓ Group terminated:\n  Group: group-a\n  Status: completed\n");
    assert_eq!(stdout(&cli.ok(&[], &["add-member", "--group", "group-a", "--bot-uuid", "bot-worker"])),
        "✓ Member added to group:\n  Group: group-a\n  Bot: bot-worker\n  Role: worker\n");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn fuse_trims_participants_and_forwards_focus_and_session_id() {
    let cli = Cli::new().await;
    // Characterize the existing fuse transport, including its lack of auth headers.
    Mock::given(method("POST")).and(path("/groups/group-a/fuse"))
        .and(body_partial_json(json!({"question": "What changed?", "participants": ["bot-source", "bot-worker"],
            "focus": "risks", "session_id": "group-a"})))
        .and(|r: &Request| !r.headers.contains_key("authorization") && !r.headers.contains_key("cookie"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"perspectives": [],
            "recommendation": "Review before release", "key_insights": ["No unresolved conflicts"]})))
        .expect(1).mount(&cli.server).await;
    let text = stdout(&cli.ok(&["--cookie", "contract=fuse"], &["fuse", "--group", "group-a", "--question", "What changed?",
        "--participants", " bot-source, bot-worker ", "--focus", "risks"]));
    let result: Value = serde_json::from_str(text.strip_prefix("Fusion result:\n").unwrap()).unwrap();
    assert_eq!(result["recommendation"], "Review before release");
    assert_eq!(result["key_insights"], json!(["No unresolved conflicts"]));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn list_groups_pagination_outputs_next_command_and_collects_all_pages() {
    let cli = Cli::new().await;
    api("GET", "/groups/my").and(query_param("offset", "0")).and(query_param("limit", "1"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"actor_id": "bot-source", "offset": 0, "limit": 1, "total": 2,
            "items": [{"id": "group-a", "mode": "agent", "driver_bot": "bot-source"}]})))
        .expect(3).mount(&cli.server).await;
    api("GET", "/groups/my").and(query_param("offset", "1")).and(query_param("limit", "1"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"actor_id": "bot-source", "offset": 1, "limit": 1, "total": 2,
            "items": [{"group_id": "group-b", "group_strategy": "manager_worker", "coordinator_bot": "bot-worker"}]})))
        .expect(2).mount(&cli.server).await;
    let args = ["list-groups", "--batch-size", "1"];
    let result: Value = serde_json::from_str(&stdout(&cli.ok(&[], &args))).unwrap();
    assert_eq!(result["returned"], 1);
    assert_eq!(result["total"], 2);
    assert_eq!(result["has_more"], true);
    assert_eq!(result["next_offset"], 1);
    assert_eq!(result["next_command"], "bcs-cli list-groups --offset 1 --batch-size 1");
    let human = stdout(&cli.ok(&["--no-json"], &args));
    assert!(human.contains("Groups for current actor bot-source (1/2):"));
    assert!(human.contains("group-a [agent] driver=bot-source"));
    assert!(human.contains("Has more: true\nNext: bcs-cli list-groups --offset 1 --batch-size 1"));
    let result: Value = serde_json::from_str(&stdout(&cli.ok(&[], &["list-groups", "--batch-size", "1", "--all"]))).unwrap();
    assert_eq!(result["items"].as_array().unwrap().len(), 2);
    assert_eq!(result["returned"], 2);
    assert_eq!(result["has_more"], false);
    assert!(result.get("next_offset").is_none());
    let human = stdout(&cli.ok(&["--no-json"], &["list-groups", "--offset", "1", "--batch-size", "1"]));
    assert!(human.contains("group-b [manager_worker] driver=bot-worker"));
    assert!(human.contains("Has more: false"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn list_groups_rejects_invalid_pagination_and_actor_drift() {
    for (offset, actor, items, total, needle) in [
        (1, "bot-source", json!([]), 0, "requested offset=0, received offset=1"),
        (0, " ", json!([]), 0, "did not identify the authenticated actor"),
        (0, "bot-source", json!([]), 1, "pagination made no progress"),
    ] {
        let cli = Cli::new().await;
        api("GET", "/groups/my").and(query_param("offset", "0")).and(query_param("limit", "1"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"actor_id": actor, "offset": offset, "limit": 1, "total": total, "items": items})))
            .expect(1).mount(&cli.server).await;
        cli.fail(&[], &["list-groups", "--batch-size", "1", "--all"], needle);
    }
    let cli = Cli::new().await;
    for (offset, actor) in [(0, "bot-source"), (1, "wrong-actor")] {
        api("GET", "/groups/my").and(query_param("offset", offset.to_string())).and(query_param("limit", "1"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"actor_id": actor, "offset": offset, "limit": 1, "total": 2, "items": [{"id": "group-a"}]})))
            .expect(1).mount(&cli.server).await;
    }
    cli.fail(&[], &["list-groups", "--batch-size", "1", "--all"], "Current actor changed during pagination");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn group_commands_propagate_backend_errors() {
    for (verb, route, args) in [
        ("POST", "/groups", vec!["create-group", "--driver", "bot-source", "--participants", "bot-worker"]),
        ("GET", "/groups/group-a", vec!["get-group", "--id", "group-a"]),
        ("GET", "/groups/my", vec!["list-groups"]),
        ("POST", "/groups/group-a/members", vec!["add-member", "--group", "group-a", "--bot-uuid", "bot-worker"]),
        ("PUT", "/groups/group-a/status", vec!["group-status", "--group", "group-a", "--status", "closed"]),
        ("POST", "/groups/group-a/terminate", vec!["terminate-group", "--group", "group-a"]),
    ] {
        let cli = Cli::new().await;
        let mock = if route == "/groups/group-a" {
            Mock::given(method(verb)).and(path(route))
                .and(|r: &Request| !r.headers.contains_key("authorization"))
        } else { api(verb, route) };
        mock.respond_with(ResponseTemplate::new(403).set_body_json(json!({"error": "group access denied"})))
            .expect(2).mount(&cli.server).await;
        for flags in [&["--no-json"][..], &["--json"][..]] { cli.fail(flags, &args, "group access denied"); }
    }
    let cli = Cli::new().await;
    Mock::given(method("POST")).and(path("/groups/group-a/fuse"))
        .and(body_partial_json(json!({"question": "Why?", "participants": ["bot-worker"]})))
        .respond_with(ResponseTemplate::new(503).set_body_json(json!({"error": "fusion unavailable"})))
        .expect(1).mount(&cli.server).await;
    cli.fail(&[], &["fuse", "--group", "group-a", "--question", "Why?", "--participants", "bot-worker"], "fusion unavailable");
}
