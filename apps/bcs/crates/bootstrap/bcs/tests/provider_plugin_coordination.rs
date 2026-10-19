mod helpers;

use std::{net::SocketAddr, time::Duration};
use bcs::{BcsConfig, BcsServer};
use bcs_service_api::{BotRunContext, CoordinationMode};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, tungstenite::Message};

type Socket = WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>;

async fn receive(ws: &mut Socket) -> Value {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match ws.next().await.expect("socket open").expect("frame") {
                Message::Text(text) => return serde_json::from_str(&text).unwrap(),
                Message::Ping(data) => ws.send(Message::Pong(data)).await.unwrap(),
                _ => {}
            }
        }
    }).await.expect("frame within deadline")
}

async fn connect(addr: SocketAddr, kind: &str, version: Option<u32>, bot: &Value) -> (Socket, Value) {
    let (mut ws, _) = tokio_tungstenite::connect_async(format!("ws://{addr}/ws/bot"))
        .await.unwrap();
    let mut params = json!({"client_kind": kind, "bot_id":bot["bot_uuid"], "token":bot["bot_runtime_token"]});
    if let Some(version) = version { params["protocol_version"] = json!(version); }
    ws.send(Message::Text(json!({"type":"req", "id":"connect", "method":"bot.connect",
        "params":params}).to_string().into())).await.unwrap();
    loop {
        let frame = receive(&mut ws).await;
        if frame["id"] == "connect" {
            assert_eq!(frame["ok"], true, "{frame}");
            return (ws, frame["payload"].clone());
        }
    }
}

fn configured(dir: &std::path::Path, profiles: &[&str]) -> BcsConfig {
    static AUTH: std::sync::Once = std::sync::Once::new();
    AUTH.call_once(|| {
        // SAFETY: one-time setup before this test executable starts any server.
        unsafe { std::env::set_var("BCS_AUTH_MOCK", "1") };
    });
    // Exercise the production top-level configuration schema, not test-only metadata.
    let mut raw = serde_json::to_value(helpers::create_test_config(&dir.to_path_buf())).unwrap();
    raw["uplink"] = json!({"allowed_profiles": profiles});
    serde_json::from_value(raw).unwrap()
}

#[tokio::test]
async fn provider_plugin_dispatches_with_exact_mapping_without_global_uplink_permission() {
    for (kind, tool, mode) in [
        ("native_mcp", "mcp__bcs-mcp__bcs_assign_task", CoordinationMode::NativeMcp),
    ] {
        let dir = tempfile::tempdir().unwrap();
        let (addr, server, state) = BcsServer::new_allowing_private_outbound_for_tests(
            configured(dir.path(), &[]))
            .run_on_random_port_with_state().await.unwrap();
        let http = reqwest::Client::new();
        let (_provider, bot) = provider_bot(&http, addr, &state, "native_mcp").await;
        let (mut manager, payload) = connect(addr, kind, Some(3), &bot).await;
        assert_eq!(payload["capabilities"]["tool_result_task_intent"], true);
        let bot_id = payload["bot_uuid"].as_str().unwrap();
        let token = payload["token"].as_str().unwrap();
        let client = bcs_cli::BcsClient::with_token(format!("http://{addr}"), token);
        client.onboard("Manager", Some("Manager"), None, None, None, None).await.unwrap();
        client.set_visibility(bot_id, "public").await.unwrap();
        let mut worker = helpers::MockBot::connect_v3(addr).await;
        worker.register("Worker", &["review"], addr).await;
        let surface = state.services.registry.resolve_coordination_surface(bot_id).await.unwrap();
        assert_eq!(surface.mode, mode);
        assert_eq!(surface.mcp_server.as_deref(), Some("bcs-mcp"));
        assert_eq!(surface.mcporter_command.as_deref(), (kind == "mcporter_mcp").then_some("mcporter"));
        let response = reqwest::Client::new().post(format!("http://{addr}/groups"))
            .bearer_auth(token).json(&json!({
                "driver_bot":bot_id, "group_strategy":"manager_worker",
                "participants":[{"bot_uuid":bot_id,"role":"manager"},
                    {"bot_uuid":worker.bot_id,"role":"worker"}]
            })).send().await.unwrap();
        let status = response.status();
        let group: Value = response.json().await.unwrap();
        assert!(status.is_success(), "{group}");
        let group_id = group["id"].as_str().unwrap();
        let session_id = group["session_id"].as_str().expect("initial session");
        // Both profiles are enabled globally; the recipient's negotiated kind
        // (not the allowlist order) must select its actual group prompt.
        loop {
            let frame = receive(&mut manager).await;
            let context = frame.to_string();
            if frame["method"] == "chat.send" && context.contains("<GroupContext>") {
                if kind == "native_mcp" {
                    assert!(context.contains("MCP server `bcs-mcp`"), "{context}");
                    assert!(!context.contains("mcporter call bcs.bcs_assign_task"));
                } else {
                    assert!(context.contains("mcporter call bcs.bcs_assign_task"), "{context}");
                    assert!(!context.contains("你当前平台原生提供 BCS MCP 工具"));
                }
                break;
            }
        }
        while worker.recv_frame_short().await.is_some() {}
        // The server owns this context; no group/session aliases are accepted from events.
        state.services.bot_run_context.put_context(BotRunContext {
            run_id: "profile-run".into(), bot_id: bot_id.into(), group_id: group_id.into(),
            bcs_session_id: Some(session_id.into()), deadline_ms: u64::MAX, terminal: false,
        }).await;
        let echo = json!({"__bcs_coordination__":true,"v":1,"tool":"bcs_assign_task",
            "arguments":{"target_bot":worker.bot_id,"message":"profile task"},
            "status":"received"}).to_string();
        // A cross-profile source must not dispatch, even with a valid envelope.
        let wrong_tool = if kind == "native_mcp" { "exec" } else { "mcp__bcs__bcs_assign_task" };
        for (name, call, first_seq) in [(wrong_tool, "wrong-call", 1), (tool, "valid-call", 3)] {
            for (offset, phase) in [(0, "start"), (1, "result")] {
                let mut event = json!({"runId":"profile-run", "sessionId":session_id,
                    "seq":first_seq + offset,"ts":123,"stream":"tool","phase":phase,
                    "name":name,"toolCallId":call});
                if phase == "start" {
                    event["args"] = json!({"command":"mcporter call bcs.bcs_assign_task"});
                } else {
                    event["isError"] = json!(false);
                    event["result"] = if kind == "mcporter_mcp" { json!(echo) }
                        else { json!({"content":[{"type":"text","text":echo}]}) };
                }
                manager.send(Message::Text(json!({"type":"event","event":"agent",
                    "payload":event}).to_string().into())).await.unwrap();
            }
            if call == "wrong-call" {
                // A response after these frames is a processing barrier, avoiding
                // a timing-only assertion while task dispatch might still be pending.
                manager.send(Message::Text(json!({"type":"req","id":"barrier",
                    "method":"bot.status","params":{"status":"idle"}}).to_string().into()))
                    .await.unwrap();
                loop {
                    let frame = receive(&mut manager).await;
                    if frame["id"] == "barrier" {
                        assert_eq!(frame["ok"], true);
                        break;
                    }
                }
                assert!(worker.recv_frame_short().await.is_none(), "cross-profile source dispatched");
            }
        }
        let dispatch = worker.recv_frame().await.expect("task dispatched over WS");
        assert_eq!(dispatch["method"], "chat.send", "{dispatch}");
        assert!(dispatch.to_string().contains("profile task"), "{dispatch}");
        manager.close(None).await.unwrap();
        tokio::time::timeout(Duration::from_secs(5), async {
            while state.services.registry.get_bot_info(bot_id, "client_kind").await.is_some() {
                tokio::task::yield_now().await;
            }
        }).await.expect("disconnect clears negotiated profile");
        assert_eq!(state.services.registry.resolve_coordination_surface(bot_id).await.unwrap().mode,
            CoordinationMode::NativeMcp);
        worker.disconnect().await;
        server.abort();
    }
}

#[tokio::test]
async fn provider_policy_overrides_global_profile_grants_and_requires_matching_v3_kind() {
    let dir = tempfile::tempdir().unwrap();
    let (addr, server, state) = BcsServer::new_allowing_private_outbound_for_tests(
        configured(dir.path(), &["native_mcp", "mcporter_mcp"]))
        .run_on_random_port_with_state().await.unwrap();
    let http = reqwest::Client::new();
    let (_, bot) = provider_bot(&http, addr, &state, "disabled").await;
    let (mut ws, payload) = connect(addr, "native_mcp", Some(3), &bot).await;
    assert_eq!(payload["capabilities"]["tool_result_task_intent"], false);
    assert_eq!(state.services.registry.resolve_coordination_surface(bot["bot_uuid"].as_str().unwrap()).await.unwrap().mode,
        CoordinationMode::Disabled);
    ws.close(None).await.unwrap();
    let (_, bot) = provider_bot(&http, addr, &state, "native_mcp").await;
    let (mut ws, payload) = connect(addr, "mcporter_mcp", Some(3), &bot).await;
    assert_eq!(payload["capabilities"]["tool_result_task_intent"], false);
    ws.close(None).await.unwrap();
    let (_, bot) = provider_bot(&http, addr, &state, "native_mcp").await;
    let (mut ws, payload) = connect(addr, "native_mcp", Some(2), &bot).await;
    assert_eq!(payload["capabilities"]["tool_result_task_intent"], false);
    ws.close(None).await.unwrap();
    server.abort();
}

async fn provider_bot(http: &reqwest::Client, addr: SocketAddr, state: &bcs::server::BcsServerState, mode: &str) -> (Value, Value) {
    let mut coordination = json!({"mode":mode});
    if mode == "native_mcp" {
        coordination["mcp_server"] = json!("bcs-mcp");
        coordination["tool_name_mapping"] = json!({
            "mcp__bcs-mcp__bcs_assign_task":"bcs_assign_task",
            "mcp__bcs-mcp__bcs_send_task_message":"bcs_send_task_message",
            "mcp__bcs-mcp__bcs_task_complete":"bcs_task_complete"
        });
    }
    let response = http.post(format!("http://{addr}/providers"))
        .header("X-Mock-User-Id", "11111111")
        .json(&json!({"name":"QwenWork", "auth":{"mode":"static_bearer"}, "coordination":coordination}))
        .send().await.unwrap();
    let status = response.status();
    let provider: Value = response.json().await.unwrap();
    assert!(status.is_success(), "{status}: {provider}");
    // Plugin registration is independent of the legacy admin HTTP route's
    // switch-provider allowlist. Exercise the same application service here.
    let bot = state.services.provider_management.register_provider_bot(bcs_service_api::RegisterProviderBotCommand {
        provider_id:provider["provider_id"].as_str().unwrap().into(),
        provider_admin_token:provider["provider_admin_token"].as_str().unwrap().into(),
        name:"QwenWork".into(), summary:None, owners:vec!["11111111".into()],
        provider_bot_ref:"qwenwork-desktop".into(), bot_slug:None, webhook_url:None,
        domains:vec![], skills:vec![], scopes:vec![], bot_uuid:None,
        reject_existing_bot_uuid:false, connection_mode:bcs_service_api::ProviderBotConnectionMode::Plugin,
    }).await.unwrap();
    (provider,json!({"bot_uuid":bot.bot_uuid,"bot_runtime_token":bot.bot_runtime_token}))
}
