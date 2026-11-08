use super::*;
use bcs_protocol::stream::AgentData;

fn data(event: StreamEvent) -> ToolData {
    let StreamEvent::Agent(agent) = event else { panic!("expected agent event") };
    let AgentData::Tool(data) = agent.data else { panic!("expected tool event") };
    data
}

fn notification(item: Value) -> Value {
    json!({"params": {"item": item}})
}

fn snapshot(items: Vec<Value>) -> Value {
    json!({"params": {"turn": {"status": "completed", "items": items}}})
}

#[test]
fn web_search_completion_keeps_query_action_and_results() {
    // Live Codex web.search completion: no generic `result`/`status` fields.
    let item = json!({"type":"webSearch", "id":"search-1", "query":"Codex events",
        "action":{"type":"search", "query":"Codex events"},
        "results":[{"type":"text_result", "title":"Codex docs"}]});
    let event = data(map_item_completed(&notification(item.clone()), "run", Path::new("/tmp")).unwrap());
    assert_eq!(event.phase, ToolPhase::Result);
    assert_eq!(event.tool_call_id.as_deref(), Some("search-1"));
    assert_eq!(event.is_error, Some(false));
    let result = event.result.unwrap();
    assert_eq!(result["query"], item["query"]);
    assert_eq!(result["action"], item["action"]);
    assert_eq!(result["results"], item["results"]);
}

#[test]
fn final_snapshot_closes_all_three_started_searches_without_duplicate_results() {
    let mut tools = ToolEvents::default();
    let cwd = Path::new("/tmp");
    let items: Vec<_> = ["search", "openPage", "findInPage"].iter().enumerate().map(|(i, action)| {
        json!({"type":"webSearch", "id":format!("search-{i}"), "query":"Codex",
            "action":{"type":action}, "results":[]})
    }).collect();
    for item in &items {
        let start = json!({"type":"webSearch", "id":item["id"], "query":"", "action":null, "results":null});
        assert_eq!(data(tools.started(&notification(start), "run", cwd).unwrap()).phase, ToolPhase::Start);
    }
    // One tool completed normally, two missed their individual completion notification.
    tools.completed(&notification(items[0].clone()), "run", cwd).unwrap();
    let final_turn = snapshot(items);
    let events: Vec<_> = tools.finish(&final_turn, "run", cwd).into_iter().map(data).collect();
    assert_eq!(events.len(), 2);
    assert_eq!(events[0].tool_call_id.as_deref(), Some("search-1"));
    assert_eq!(events[1].tool_call_id.as_deref(), Some("search-2"));
    assert!(events.iter().all(|e| e.phase == ToolPhase::Result && e.is_error == Some(false)));
    assert!(tools.finish(&final_turn, "run", cwd).is_empty());
}

#[test]
fn snapshot_does_not_invent_completion_for_missing_unstarted_or_running_items() {
    let mut tools = ToolEvents::default();
    let cwd = Path::new("/tmp");
    for id in ["missing", "running"] {
        tools.started(&notification(json!({"type":"commandExecution", "id":id})), "run", cwd).unwrap();
    }
    assert!(tools.finish(&snapshot(vec![
        json!({"type":"commandExecution", "id":"running", "status":"inProgress"}),
        json!({"type":"webSearch", "id":"unstarted", "query":"unknown"}),
    ]), "run", cwd).is_empty());
    assert_eq!(tools.pending.len(), 2);
}

#[test]
fn failed_or_interrupted_turn_does_not_mark_search_successful() {
    let cwd = Path::new("/tmp");
    for status in ["failed", "interrupted", "inProgress"] {
        let mut tools = ToolEvents::default();
        let item = json!({"type":"webSearch", "id":"search", "query":"Codex"});
        tools.started(&notification(item.clone()), "run", cwd).unwrap();
        let mut turn = snapshot(vec![item]);
        turn["params"]["turn"]["status"] = json!(status);
        assert!(tools.finish(&turn, "run", cwd).is_empty());
    }
}

#[test]
fn completed_snapshot_preserves_command_failure() {
    let mut tools = ToolEvents::default();
    let cwd = Path::new("/tmp");
    tools.started(&notification(json!({"type":"commandExecution", "id":"cmd"})), "run", cwd).unwrap();
    let events = tools.finish(&snapshot(vec![json!({"type":"commandExecution", "id":"cmd",
        "status":"failed", "exitCode":1, "aggregatedOutput":"failed command"})]), "run", cwd);
    let result = data(events.into_iter().next().unwrap());
    assert_eq!(result.is_error, Some(true));
    assert_eq!(result.result, Some(json!("failed command")));
}

#[test]
fn denied_mcp_call_with_null_result_keeps_error_and_passes_v3_validation() {
    use crate::encoder::PluginEncoder;
    use crate::run::RunEvent;
    use bcs_protocol::{BcsFrame, stream::parse_run_event_v3};
    let mut tools = ToolEvents::default();
    let cwd = Path::new("/tmp");
    let item = json!({"type":"mcpToolCall", "id":"denied-mcp",
        "server":"mcp_ant_agentclawscs_bcs_mcp", "tool":"bcs_assign_task",
        "arguments":{"target_bot":"worker"}, "status":"failed", "result":null,
        "error":{"message":"MCP tool call requires approval, but approval policy is never"}});
    tools.started(&notification(item.clone()), "run", cwd).unwrap();
    let event = tools.completed(&notification(item), "run", cwd).unwrap();
    let encoder = PluginEncoder::for_protocol(true, "provider", "bot", "group", "group:session");
    let encoded = encoder.encode(&RunEvent {run_id:"run".into(), seq:2, ts:1, event}).unwrap().unwrap();
    let BcsFrame::Event(frame) = encoded else { panic!("expected event") };
    let tool = data(parse_run_event_v3(&frame.event, frame.payload.unwrap()).unwrap());
    assert_eq!(tool.phase, ToolPhase::Result);
    assert_eq!(tool.is_error, Some(true));
    assert_eq!(tool.tool_call_id.as_deref(), Some("denied-mcp"));
    assert!(tool.result.unwrap()["message"].as_str().unwrap().contains("approval policy is never"));
}

#[test]
fn empty_terminal_output_is_not_encoded_as_a_missing_result() {
    for item in [
        json!({"type":"mcpToolCall", "id":"mcp", "status":"completed", "result":null, "error":null}),
        json!({"type":"fileChange", "id":"file", "status":"completed"}),
        json!({"type":"dynamicToolCall", "id":"dynamic", "status":"completed", "contentItems":null}),
    ] {
        let result = data(map_item_completed(&notification(item), "run", Path::new("/tmp")).unwrap());
        assert_eq!(result.result, Some(json!("")));
        assert_eq!(result.is_error, Some(false));
    }
}

#[test]
fn mcp_error_signals_are_not_lost_when_status_is_completed() {
    for (result, error) in [
        (json!({"isError":true, "content":[{"type":"text", "text":"denied"}]}), Value::Null),
        (Value::Null, json!({"message":"denied"})),
    ] {
        let event = data(map_item_completed(&notification(json!({"type":"mcpToolCall", "id":"mcp",
            "status":"completed", "result":result, "error":error})), "run", Path::new("/tmp")).unwrap());
        assert_eq!(event.is_error, Some(true));
        assert!(!event.result.unwrap().is_null());
    }
}
