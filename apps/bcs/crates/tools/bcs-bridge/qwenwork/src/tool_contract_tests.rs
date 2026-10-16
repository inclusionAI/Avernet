use super::*;
use bcs_bridge_core::{encoder::PluginEncoder, run::RunEvent};
use bcs_protocol::{BcsFrame, stream::parse_run_event_v3};

#[tokio::test]
async fn wrapped_mcp_calls_keep_the_start_identity_across_reordered_and_duplicate_results() {
    for canonical in ["bcs_assign_task", "bcs_send_task_message", "bcs_task_complete"] {
        for result_first in [false, true] {
            let fixture = Fixture::new();
            let (mut socket, _) = fixture.connect("test-secret").await;
            let (task, mut events) = fixture.turn("wrapped-run", None, Arc::new(Observer::default()), CancellationToken::new());
            let request = frame(&mut socket).await;
            fixture.correlate(request["body"]["chatid"].as_str().unwrap(), "wrapped-session");
            fixture.begin("wrapped-session", "wrapped-query", "hello").await;
            let actual_name = format!("mcp__bcs-mcp__{canonical}");
            let arguments = json!({"target_bot":"worker-1","message":"test"});
            let start = json!({"session_id":"wrapped-session", "hook_event_name":"PreToolUse",
                "tool_use_id":"wrapped-call", "tool_name":"mcp__qw-builtin__qwenwork_mcp_tool_call",
                "tool_input":{"toolName":actual_name,"arguments":arguments}});
            let output = json!({"__bcs_coordination__":true,"v":2,"tool":canonical,
                "intent_id":"synthetic-intent","status":"stored"}).to_string();
            // Result names and embedded arguments cannot redefine the start's tool identity.
            let result = json!({"session_id":"wrapped-session", "hook_event_name":"PostToolUse",
                "tool_use_id":"wrapped-call", "tool_name":"mcp__untrusted__another_tool",
                "tool_input":{"toolName":"mcp__untrusted__another_tool","arguments":{}},"tool_response":output});
            let hooks = if result_first { [result.clone(), start.clone(), start, result] } else { [start.clone(), result.clone(), start, result] };
            for hook in hooks { assert!(fixture.raw_hook(hook, "test-hooks").await.status().is_success()); }
            reply(&mut socket, request["headers"]["req_id"].as_str().unwrap(), "Done", true).await;
            fixture.end("wrapped-session", "wrapped-query").await;
            tokio::time::timeout(Duration::from_secs(3), task).await.unwrap().unwrap().unwrap();
            let encoder = PluginEncoder::for_protocol(true, "provider", "desktop", "group", "group:session");
            let mut tools = Vec::new();
            let mut seq = 0;
            while let Some(event) = events.recv().await {
                seq += 1;
                let encoded = encoder.encode(&RunEvent { run_id:"wrapped-run".into(),seq,ts:1_792_054_900_000,event }).unwrap().unwrap();
                let BcsFrame::Event(frame) = encoded else { panic!("expected event") };
                if let StreamEvent::Agent(agent) = parse_run_event_v3(&frame.event, frame.payload.unwrap()).unwrap() {
                    if let AgentData::Tool(tool) = agent.data { tools.push(tool); }
                }
            }
            assert_eq!(tools.len(), 2);
            assert_eq!(tools[0].phase, ToolPhase::Start);
            assert_eq!(tools[1].phase, ToolPhase::Result);
            for tool in &tools {
                assert_eq!(tool.name.as_deref(), Some(actual_name.as_str()));
                assert_eq!(tool.tool_call_id.as_deref(), Some("wrapped-call"));
            }
            assert_eq!(tools[0].args, Some(arguments));
            assert_eq!(tools[1].result, Some(json!(output)));
            assert_eq!(tools[1].is_error, Some(false));
        }
    }
}

#[test]
fn unknown_or_malformed_mcp_wrappers_are_preserved_without_guessing_tool_identity() {
    for (outer, input) in [
        ("Bash", json!({"toolName":"mcp__bcs-mcp__bcs_assign_task","arguments":{}})),
        ("mcp__qw-builtin__qwenwork_mcp_tool_list", json!({"toolName":"mcp__bcs-mcp__bcs_assign_task","arguments":{}})),
        ("mcp__qw-builtin__qwenwork_mcp_tool_call", json!({"toolName":"bcs_assign_task","arguments":{}})),
        ("mcp__qw-builtin__qwenwork_mcp_tool_call", json!({"toolName":"mcp____bcs_assign_task","arguments":{}})),
        ("mcp__qw-builtin__qwenwork_mcp_tool_call", json!({"toolName":"mcp__bcs-mcp__bcs_assign_task","arguments":"{}"})),
        ("mcp__qw-builtin__qwenwork_mcp_tool_call", json!({"arguments":{}})),
    ] {
        let mut hook = json!({"tool_name":outer,"tool_input":input});
        let original = hook.clone();
        assert_eq!(turn::unwrap_mcp_call(&mut hook), None);
        assert_eq!(hook, original);
    }
}

// Regression: successful PostToolUse omitted isError, so BCS rejected its V3
// result frame and the workbench kept the matching tool start running forever.
#[tokio::test]
async fn bash_hook_results_satisfy_the_bcs_v3_wire_contract_for_success_and_failure() {
    for failed in [false, true] {
        let fixture = Fixture::new();
        let (mut socket, _) = fixture.connect("test-secret").await;
        let (task, mut events) = fixture.turn("tool-wire-run", None, Arc::new(Observer::default()), CancellationToken::new());
        let request = frame(&mut socket).await;
        fixture.correlate(request["body"]["chatid"].as_str().unwrap(), "tool-wire-session");
        fixture.begin("tool-wire-session", "tool-wire-query", "hello").await;
        let hook = json!({"session_id":"tool-wire-session", "hook_event_name":"PreToolUse",
            "tool_use_id":"bash-call-1", "tool_name":"Bash", "tool_input":{"command":"pwd"}});
        assert!(fixture.raw_hook(hook.clone(), "test-hooks").await.status().is_success());
        let mut result = hook;
        result["hook_event_name"] = json!(if failed { "PostToolUseFailure" } else { "PostToolUse" });
        let output = if failed { json!("command failed") } else { json!({"exitCode":0,"stdout":"/tmp/workspace"}) };
        result[if failed { "error" } else { "tool_response" }] = output.clone();
        assert!(fixture.raw_hook(result, "test-hooks").await.status().is_success());
        reply(&mut socket, request["headers"]["req_id"].as_str().unwrap(), "Done", true).await;
        fixture.end("tool-wire-session", "tool-wire-query").await;
        tokio::time::timeout(Duration::from_secs(3), task).await.unwrap().unwrap().unwrap();

        let encoder = PluginEncoder::for_protocol(true, "provider", "desktop", "group", "group:session");
        let mut tools = Vec::new();
        let mut seq = 0;
        while let Some(event) = events.recv().await {
            seq += 1;
            let run = RunEvent { run_id: "tool-wire-run".into(), seq, ts: 1_792_052_323_000, event };
            let frame = encoder.encode(&run).unwrap().unwrap();
            // Include actual JSON serialization, rather than only checking ToolData in memory.
            let frame: BcsFrame = serde_json::from_slice(&serde_json::to_vec(&frame).unwrap()).unwrap();
            let BcsFrame::Event(frame) = frame else { panic!("expected V3 event") };
            let payload = frame.payload.unwrap();
            assert_eq!(frame.seq, Some(seq));
            assert_eq!(payload["sessionId"], "group:session");
            let parsed = parse_run_event_v3(&frame.event, payload.clone())
                .expect("QwenWork events must pass the same strict V3 parser used by BCS");
            if let StreamEvent::Agent(agent) = parsed {
                if let AgentData::Tool(tool) = agent.data {
                    if tool.phase == ToolPhase::Result {
                        assert_eq!(payload["isError"], json!(failed));
                        assert_eq!(tool.result, Some(output.clone()));
                    }
                    tools.push(tool);
                }
            }
        }
        assert_eq!(tools.len(), 2);
        assert_eq!(tools[0].phase, ToolPhase::Start);
        assert_eq!(tools[1].phase, ToolPhase::Result);
        assert_eq!(tools[0].tool_call_id.as_deref(), Some("bash-call-1"));
        assert_eq!(tools[0].tool_call_id, tools[1].tool_call_id);
        assert_eq!(tools[1].is_error, Some(failed));
    }
}
