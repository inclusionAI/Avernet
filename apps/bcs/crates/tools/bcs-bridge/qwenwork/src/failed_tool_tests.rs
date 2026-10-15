use super::*;

#[tokio::test]
async fn failed_tool_result_is_forwarded_and_query_end_finishes_the_run() {
    let f = Fixture::new();
    let (mut socket, _) = f.connect("test-secret").await;
    let (task, mut events) = f.turn("failed-tool", None, Arc::new(Observer::default()), CancellationToken::new());
    let request = frame(&mut socket).await;
    f.correlate(request["body"]["chatid"].as_str().unwrap(), "failure-session");
    f.begin("failure-session", "query", "hello").await;
    f.hook("failure-session", "PreToolUse", "test-hooks").await;
    assert!(f.raw_hook(json!({"session_id":"failure-session", "hook_event_name":"PostToolUseFailure",
        "tool_use_id":"tool-1", "tool_name":"Read", "error":"file not found"}), "test-hooks").await.status().is_success());
    reply(&mut socket, request["headers"]["req_id"].as_str().unwrap(), "The file is missing.", true).await;
    f.end("failure-session", "query").await;
    let result = tokio::time::timeout(Duration::from_secs(3), task).await.unwrap().unwrap().unwrap();
    assert_eq!(result.final_text.as_deref(), Some("The file is missing."));
    let mut tools = Vec::new();
    while let Some(event) = events.recv().await {
        if let StreamEvent::Agent(event) = event { if let AgentData::Tool(tool) = event.data { tools.push(tool); } }
    }
    assert_eq!(tools.len(), 2);
    assert_eq!(tools[1].phase, ToolPhase::Result);
    assert_eq!(tools[1].is_error, Some(true));
}
