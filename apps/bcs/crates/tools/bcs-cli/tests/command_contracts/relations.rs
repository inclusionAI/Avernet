use super::fixture::{Cli, stdout};
use serde_json::{Value, json};
use wiremock::matchers::{body_json, header, method, path, query_param};
use wiremock::{Mock, MockBuilder, Request, ResponseTemplate};

fn api(verb: &str, route: &str) -> MockBuilder {
    Mock::given(method(verb)).and(path(route))
        .and(header("authorization", "Bearer test-command-token"))
}

fn json_output(cli: &Cli, args: &[&str]) -> Value {
    serde_json::from_str(&stdout(&cli.ok(&["--json"], args))).unwrap()
}

fn bind_args() -> Vec<&'static str> {
    vec!["channel", "bind", "--account", "robot-account", "--target-id", "group-a",
        "--robot-code", "test-robot", "--client-id", "test-client", "--client-secret", "test-secret"]
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn friend_request_routes_token_cookie_and_serializes_only_target() {
    let cli = Cli::new().await;
    let response = json!({"success": true, "data": {"id": "request-a", "to_bot": "bot-target"}});
    Mock::given(method("POST")).and(path("/friends/request"))
        .and(header("authorization", "Bearer explicit-test-token"))
        .and(header("cookie", "contract=friend"))
        .and(body_json(json!({"to_bot": "bot-target"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(response)).expect(2).mount(&cli.server).await;
    let args = ["friend", "--token", "explicit-test-token", "request", "--bot-uuid", "bot-target"];
    let human = stdout(&cli.ok(&["--no-json", "--cookie", "contract=friend"], &args));
    assert_eq!(human, "✓ Friend request sent to bot-target\n  Request ID: request-a\n");
    let result: Value = serde_json::from_str(&stdout(&cli.ok(&["--json", "--cookie", "contract=friend"], &args))).unwrap();
    assert_eq!(result, json!({"success": true, "data": {"id": "request-a", "to_bot": "bot-target"}, "message": null}));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn friend_request_renders_server_message_and_business_failure() {
    for (body, expected) in [
        (json!({"success": true, "message": "Already friends"}), "✓ Already friends\n"),
        (json!({"success": false, "error": "cannot add self"}), "✗ Failed: cannot add self\n"),
    ] {
        let cli = Cli::new().await;
        api("POST", "/friends/request").and(body_json(json!({"to_bot": "bot-target"})))
            .respond_with(ResponseTemplate::new(200).set_body_json(body)).expect(1).mount(&cli.server).await;
        // HTTP-200 business failures currently print failure but retain exit 0.
        assert_eq!(stdout(&cli.ok(&["--no-json"], &["friend", "request", "--bot-uuid", "bot-target"])), expected);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn friend_accept_reject_have_empty_bodies_and_human_json_modes() {
    for (action, past) in [("accept", "accepted"), ("reject", "rejected")] {
        let cli = Cli::new().await;
        api("POST", &format!("/friends/requests/request-a/{action}"))
            .and(|r: &Request| r.body.is_empty())
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"success": true})))
            .expect(2).mount(&cli.server).await;
        let args = ["friend", action, "--request-id", "request-a"];
        assert_eq!(stdout(&cli.ok(&["--no-json"], &args)), format!("✓ Friend request {past}\n"));
        assert_eq!(json_output(&cli, &args), json!({"success": true}));
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn friend_accept_reject_surface_business_failure_in_both_modes() {
    for action in ["accept", "reject"] {
        let cli = Cli::new().await;
        api("POST", &format!("/friends/requests/request-a/{action}"))
            .and(|r: &Request| r.body.is_empty())
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"success": false, "error": "request expired"})))
            .expect(2).mount(&cli.server).await;
        let args = ["friend", action, "--request-id", "request-a"];
        assert_eq!(stdout(&cli.ok(&["--no-json"], &args)), "✗ Failed: request expired\n");
        assert_eq!(json_output(&cli, &args), json!({"success": false}));
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn friend_list_resolves_session_identity_and_displays_presence() {
    let cli = Cli::new().await;
    let friends = json!([{"bot_uuid": "bot-online", "name": "Reviewer", "is_online": true},
        {"bot_uuid": "bot-offline", "is_online": false}]);
    api("GET", "/bots/bot-source/friends")
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"success": true, "data": friends})))
        .expect(2).mount(&cli.server).await;
    assert_eq!(stdout(&cli.ok(&["--no-json"], &["friend", "list"])),
        "Friends (2):\n  🟢 Reviewer (bot-online)\n  ⚪ unnamed (bot-offline)\n");
    assert_eq!(json_output(&cli, &["friend", "list"]), friends);
    assert_eq!(cli.server.received_requests().await.unwrap().len(), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn friend_lists_handle_explicit_identity_and_empty_nonarray_data() {
    for (data, expected) in [(json!([]), "Friends (0):\n"), (json!({}), "No friends found.\n")] {
        let cli = Cli::new().await;
        api("GET", "/bots/explicit-bot/friends")
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"success": true, "data": data})))
            .expect(2).mount(&cli.server).await;
        let args = ["friend", "list", "--bot-uuid", "explicit-bot"];
        assert_eq!(stdout(&cli.ok(&["--no-json"], &args)), expected);
        assert_eq!(json_output(&cli, &args), data);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn friend_requests_filters_direction_status_and_unwraps_data() {
    let cli = Cli::new().await;
    let requests = json!([{"id": "request-a", "from_bot": "bot-source", "to_bot": "bot-target", "status": "pending"}]);
    api("GET", "/friends/requests").and(query_param("direction", "sent")).and(query_param("status", "pending"))
        .and(|r: &Request| !r.url.query_pairs().any(|(key, _)| key == "bot_uuid"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"success": true, "data": requests})))
        .expect(2).mount(&cli.server).await;
    let args = ["friend", "requests", "--direction", "sent", "--status", "pending"];
    assert_eq!(stdout(&cli.ok(&["--no-json"], &args)), "Friend requests (1):\n  bot-source → bot-target [pending] (id: request-a)\n");
    assert_eq!(json_output(&cli, &args), requests);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn friend_requests_default_direction_and_nonarray_or_absent_data() {
    for (data, expected) in [(json!([]), "Friend requests (0):\n"), (json!({}), "No friend requests found.\n"),
        (Value::Null, "")] {
        let cli = Cli::new().await;
        api("GET", "/friends/requests").and(query_param("direction", "received"))
            .and(|r: &Request| r.url.query_pairs().count() == 1)
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"success": true, "data": data})))
            .expect(2).mount(&cli.server).await;
        assert_eq!(stdout(&cli.ok(&["--no-json"], &["friend", "requests"])), expected);
        assert_eq!(json_output(&cli, &["friend", "requests"]), data);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn visibility_uses_session_and_explicit_target_and_preserves_envelope() {
    let cli = Cli::new().await;
    let response = json!({"success": true, "data": {"visibility": "protected"}});
    api("GET", "/bots/bot-source/visibility").respond_with(ResponseTemplate::new(200).set_body_json(&response))
        .expect(2).mount(&cli.server).await;
    let updated = json!({"success": true, "data": {"visibility": "public"}});
    api("PUT", "/bots/explicit-bot/visibility").and(body_json(json!({"visibility": "public"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(&updated)).expect(2).mount(&cli.server).await;
    assert_eq!(stdout(&cli.ok(&["--no-json"], &["visibility", "get"])), "Visibility: protected\n");
    assert_eq!(json_output(&cli, &["visibility", "get"]), response);
    let args = ["visibility", "set", "--value", "public", "--bot-uuid", "explicit-bot"];
    assert_eq!(stdout(&cli.ok(&["--no-json"], &args)), "✓ Visibility set to 'public'\n");
    assert_eq!(json_output(&cli, &args), updated);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn visibility_explicit_get_and_actor_scoped_set_route_headers() {
    let cli = Cli::new().await;
    api("GET", "/bots/explicit-bot/visibility")
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"success": true, "data": {}})))
        .expect(1).mount(&cli.server).await;
    assert_eq!(stdout(&cli.ok(&[], &["visibility", "get", "--bot-uuid", "explicit-bot"])), "Visibility: unknown\n");
    api("PUT", "/bots/actor-bot/visibility").and(header("x-bcn-actor-bot-id", "actor-bot"))
        .and(header("cookie", "contract=visibility")).and(body_json(json!({"visibility": "private"})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"success": true, "data": {"visibility": "private"}})))
        .expect(1).mount(&cli.server).await;
    let output = cli.command(&["--cookie", "contract=visibility"], &["visibility", "set", "--value", "private"])
        .env("BCN_ACTOR_BOT_ID", "actor-bot").assert().success().get_output().clone();
    assert_eq!(stdout(&output), "✓ Visibility set to 'private'\n");
    assert_eq!(cli.server.received_requests().await.unwrap().len(), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn identity_fallback_uses_selected_token_not_unrelated_session() {
    let cli = Cli::new().await;
    Mock::given(method("GET")).and(path("/api/v1/collaboration/bots/me"))
        .and(header("authorization", "Bearer different-test-token"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"code": 20000, "data": {"bot": {"bot_id": "authenticated-bot"}}})))
        .expect(2).mount(&cli.server).await;
    for suffix in ["friends", "visibility"] {
        Mock::given(method("GET")).and(path(format!("/bots/authenticated-bot/{suffix}")))
            .and(header("authorization", "Bearer different-test-token"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"success": true,
                "data": if suffix == "friends" { json!([]) } else { json!({"visibility": "public"}) }})))
            .expect(1).mount(&cli.server).await;
    }
    assert_eq!(stdout(&cli.ok(&[], &["friend", "--token", "different-test-token", "list"])), "Friends (0):\n");
    assert_eq!(stdout(&cli.ok(&[], &["visibility", "--token", "different-test-token", "get"])), "Visibility: public\n");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn visibility_business_failures_have_human_and_json_representations() {
    for (verb, args) in [("GET", vec!["visibility", "get"]), ("PUT", vec!["visibility", "set", "--value", "public"])] {
        let cli = Cli::new().await;
        api(verb, "/bots/bot-source/visibility")
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"success": false, "error": "visibility locked"})))
            .expect(2).mount(&cli.server).await;
        assert_eq!(stdout(&cli.ok(&["--no-json"], &args)), "✗ Failed: visibility locked\n");
        assert_eq!(json_output(&cli, &args), json!({"success": false, "data": null}));
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn relation_http_errors_exit_nonzero_instead_of_success_output() {
    for (verb, route, args, needle) in [
        ("POST", "/friends/request", vec!["friend", "request", "--bot-uuid", "bot-target"], "pending request exists"),
        ("POST", "/friends/requests/request-a/accept", vec!["friend", "accept", "--request-id", "request-a"], "Accept friend request failed"),
        ("POST", "/friends/requests/request-a/reject", vec!["friend", "reject", "--request-id", "request-a"], "Reject friend request failed"),
        ("GET", "/friends/requests", vec!["friend", "requests"], "List friend requests failed"),
        ("GET", "/bots/bot-source/friends", vec!["friend", "list"], "List friends failed"),
        ("GET", "/bots/bot-source/visibility", vec!["visibility", "get"], "Get visibility failed"),
        ("PUT", "/bots/bot-source/visibility", vec!["visibility", "set", "--value", "public"], "Set visibility failed"),
    ] {
        let cli = Cli::new().await;
        api(verb, route).respond_with(ResponseTemplate::new(409).set_body_json(json!({"error": "pending request exists"})))
            .expect(2).mount(&cli.server).await;
        for flags in [&["--no-json"][..], &["--json"][..]] { cli.fail(flags, &args, needle); }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn channel_bind_default_group_payload_and_human_json_outputs() {
    let cli = Cli::new().await;
    let binding = json!({"id": "binding-a", "account_ref": "robot-account", "status": "active"});
    api("POST", "/channels/bindings").and(header("cookie", "contract=channel"))
        .and(body_json(json!({"channel_type": "dingtalk", "account_ref": "robot-account",
            "target": {"group": {"group_id": "group-a"}}, "outbound_visibility": "lead_only", "env": "dev",
            "config": {"channel_type": "dingtalk", "robot_code": "test-robot", "client_id": "test-client",
                "client_secret": "test-secret", "send_mode": {"mode": "normal", "message_type": "markdown"}}})))
        .respond_with(ResponseTemplate::new(200).set_body_json(&binding)).expect(2).mount(&cli.server).await;
    let args = bind_args();
    let output = cli.ok(&["--no-json", "--cookie", "contract=channel"], &args);
    assert_eq!(stdout(&output), "✓ Channel binding created\n  ID: binding-a\n  Account: robot-account\n");
    assert!(!stdout(&output).contains("test-secret"));
    assert!(!String::from_utf8_lossy(&output.stderr).contains("test-secret"));
    let result: Value = serde_json::from_str(&stdout(&cli.ok(&["--json", "--cookie", "contract=channel"], &args))).unwrap();
    assert_eq!(result, binding);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn channel_bind_bot_streaming_card_payload_and_explicit_options() {
    let cli = Cli::new().await;
    api("POST", "/channels/bindings").and(body_json(json!({"channel_type": "dingtalk", "account_ref": "robot-account",
        "target": {"bot": {"bot_id": "group-a"}}, "outbound_visibility": "full_transcript", "env": "staging",
        "group_chat_scope": "per_sender", "config": {"channel_type": "dingtalk", "robot_code": "test-robot",
            "client_id": "test-client", "client_secret": "test-secret", "send_mode": {
                "mode": "streaming_card", "card_template_id": "template-a", "fallback_message_type": "text"}}})))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({"id": "binding-b"})))
        .expect(1).mount(&cli.server).await;
    let mut args = bind_args();
    args.extend(["--target-kind", "bot", "--visibility", "full_transcript", "--env", "staging",
        "--group-chat-scope", "per_sender", "--send-mode", "streaming_card", "--card-template-id", "template-a", "--message-type", "text"]);
    assert_eq!(json_output(&cli, &args), json!({"id": "binding-b"}));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn channel_bind_rejects_invalid_targets_modes_and_missing_card_template_before_http() {
    let cli = Cli::new().await;
    for (extra, needle) in [
        (vec!["--target-kind", "session"], "unsupported target-kind session; expected group or bot"),
        (vec!["--send-mode", "unknown"], "unsupported send-mode unknown; expected normal or streaming_card"),
        (vec!["--send-mode", "streaming_card"], "--card-template-id is required"),
    ] {
        let mut args = bind_args();
        args.extend(extra);
        cli.fail(&["--json"], &args, needle);
    }
    assert!(cli.server.received_requests().await.unwrap().is_empty());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn channel_list_renders_items_and_preserves_json_response() {
    let cli = Cli::new().await;
    let result = json!({"items": [{"id": "binding-a", "account_ref": "robot-account", "status": "active"}, {}], "total": 2});
    api("GET", "/channels/bindings").respond_with(ResponseTemplate::new(200).set_body_json(&result))
        .expect(2).mount(&cli.server).await;
    assert_eq!(stdout(&cli.ok(&["--no-json"], &["channel", "list"])), "Channel bindings (2):\n  binding-a robot-account [active]\n  ? ? [?]\n");
    assert_eq!(json_output(&cli, &["channel", "list"]), result);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn channel_conversation_lookup_encodes_session_and_pins_dingtalk_registry_key() {
    let cli = Cli::new().await;
    let result = json!({"items": [{"conversation_id": "conversation-a", "binding_id": "binding-a", "session_scope": "per_sender"}, {}]});
    api("GET", "/channels/conversations/by-session").and(query_param("bcs_session_id", "group-a:session +/"))
        .and(query_param("channel_type", "dingtalk"))
        .respond_with(ResponseTemplate::new(200).set_body_json(&result)).expect(2).mount(&cli.server).await;
    let args = ["channel", "conversation-id", "--session", "group-a:session +/"];
    assert_eq!(stdout(&cli.ok(&["--no-json"], &args)),
        "DingTalk conversations for session group-a:session +/ (2):\n  conversation-a (binding: binding-a, scope: per_sender)\n  ? (binding: ?, scope: ?)\n");
    assert_eq!(json_output(&cli, &args), result);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn channel_lists_fallback_to_pretty_json_for_non_item_responses() {
    for (route, args) in [("/channels/bindings", vec!["channel", "list"]),
        ("/channels/conversations/by-session", vec!["channel", "conversation-id", "--session", "session-a"])] {
        let cli = Cli::new().await;
        let result = json!({"message": "legacy response", "count": 0});
        api("GET", route).respond_with(ResponseTemplate::new(200).set_body_json(&result))
            .expect(1).mount(&cli.server).await;
        assert_eq!(serde_json::from_str::<Value>(&stdout(&cli.ok(&["--no-json"], &args))).unwrap(), result);
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn channel_unbind_encodes_binding_id_and_reports_deleted_binding() {
    let cli = Cli::new().await;
    let response = json!({"deleted": true, "id": "binding:a b"});
    api("DELETE", "/channels/bindings/binding%3Aa%20b").and(|r: &Request| r.body.is_empty())
        .respond_with(ResponseTemplate::new(200).set_body_json(&response)).expect(2).mount(&cli.server).await;
    let args = ["channel", "unbind", "--id", "binding:a b"];
    assert_eq!(stdout(&cli.ok(&["--no-json"], &args)), "✓ Channel binding deleted: binding:a b\n");
    assert_eq!(json_output(&cli, &args), response);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn channel_http_errors_propagate_backend_details_in_both_output_modes() {
    for (verb, route, args) in [
        ("POST", "/channels/bindings", bind_args()),
        ("GET", "/channels/bindings", vec!["channel", "list"]),
        ("GET", "/channels/conversations/by-session", vec!["channel", "conversation-id", "--session", "session-a"]),
        ("DELETE", "/channels/bindings/binding-a", vec!["channel", "unbind", "--id", "binding-a"]),
    ] {
        let cli = Cli::new().await;
        api(verb, route).respond_with(ResponseTemplate::new(403).set_body_json(json!({"error": "channel access denied"})))
            .expect(2).mount(&cli.server).await;
        for flags in [&["--no-json"][..], &["--json"][..]] { cli.fail(flags, &args, "channel access denied"); }
    }
}
