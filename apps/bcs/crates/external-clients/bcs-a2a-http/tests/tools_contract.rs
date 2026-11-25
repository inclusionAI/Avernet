use a2a::StreamResponse;
use bcs_a2a_http::{projection::{Output, Projection, ProjectionError}, tools::{EXTENSION, EXTENSION_V2}};
use serde_json::{json, Value};

fn initial(enabled: bool) -> Projection {
    initial_versions(enabled, false)
}
fn initial_versions(v1: bool, v2: bool) -> Projection {
    Projection::default().with_tools_versions(v1, v2).stage(serde_json::from_value(json!({"task":{
        "id":"task","contextId":"context","status":{"state":"TASK_STATE_WORKING"}}
    })).unwrap()).unwrap().0
}
fn update(record: Value) -> StreamResponse {
    serde_json::from_value(json!({"statusUpdate":{"taskId":"task","contextId":"context",
        "status":{"state":"TASK_STATE_WORKING","message":{"messageId":"observation","role":"ROLE_AGENT",
            "metadata":{EXTENSION:{"version":"1"}},"parts":[{"data":record}]}}}})).unwrap()
}
fn update_v2(record: Value) -> StreamResponse {
    serde_json::from_value(json!({"statusUpdate":{"taskId":"task","contextId":"context",
        "status":{"state":"TASK_STATE_WORKING","message":{"messageId":"observation-v2","role":"ROLE_AGENT",
            "extensions":[EXTENSION_V2],"parts":[{"data":record,"metadata":{"serverId":"mcp-1"}}]}}}})).unwrap()
}
fn start() -> Value { json!({"phase":"start","toolCallId":"call","name":"query_order","args":{"id":"order"}}) }
fn result() -> Value { json!({"phase":"result","toolCallId":"call","name":"query_order","isError":false,
    "result":{"content":[{"type":"text","text":"ok"}]}}) }
fn tools(outputs: &[Output]) -> Vec<&Value> {
    outputs.iter().filter_map(|output|if let Output::Tool(value)=output {Some(value)} else {None}).collect()
}

#[test]
fn tools_require_negotiation_and_validate_versions_and_payloads() {
    assert!(matches!(initial(false).stage(update(start())),Err(ProjectionError::Tool(_))));
    let mut malformed = start(); malformed["args"]=json!([]);
    assert!(matches!(initial(true).stage(update(malformed)),Err(ProjectionError::Tool(_))));
    let mut wrong_role = serde_json::to_value(update(start())).unwrap();
    wrong_role["statusUpdate"]["status"]["message"]["role"]=json!("ROLE_USER");
    assert!(initial(true).stage(serde_json::from_value(wrong_role).unwrap()).is_err());
}

#[test]
fn staged_observations_deduplicate_without_fabricating_start() {
    let projection=initial(true);
    let (_, failed_ingest)=projection.stage(update(start())).unwrap();
    let (projection,retried)=projection.stage(update(start())).unwrap();
    assert_eq!(failed_ingest,retried);
    assert_eq!(tools(&retried)[0]["state"],"tool_call_start");
    assert!(tools(&projection.stage(update(start())).unwrap().1).is_empty());
    let mut conflict=start(); conflict["args"]=json!({"id":"another"});
    assert!(projection.stage(update(conflict)).is_err());
    let (_,recovered)=initial(true).stage(update(result())).unwrap();
    assert_eq!(tools(&recovered).len(),1);
    assert_eq!(tools(&recovered)[0]["state"],"tool_call_end");
    assert_eq!(tools(&recovered)[0]["data"]["isError"],false);
}

#[test]
fn text_cursor_survives_tools_and_remote_routing_is_never_forwarded() {
    let chunk=|text,append|serde_json::from_value(json!({"artifactUpdate":{"taskId":"task","contextId":"context",
        "artifact":{"artifactId":"text","parts":[{"text":text}]},"append":append}})).unwrap();
    let (projection,_)=initial(true).stage(chunk("before",false)).unwrap();
    let mut record=start(); record["run_id"]=json!("spoof"); record["routing"]=json!({"target":"victim"});
    let (projection,outputs)=projection.stage(update(record)).unwrap();
    assert!(tools(&outputs)[0].get("run_id").is_none());
    assert!(tools(&outputs)[0].get("routing").is_none());
    let (_,outputs)=projection.stage(chunk("after",true)).unwrap();
    assert_eq!(outputs,vec![Output::Delta("after".into())]);
}

#[test]
fn tools_v2_uses_message_extensions_and_optional_fields() {
    let minimal_start = json!({"phase":"start","toolCallId":"call-v2","name":"search"});
    let minimal_result = json!({"phase":"result","toolCallId":"call-v2","name":"search"});
    assert!(matches!(initial_versions(true, false).stage(update_v2(minimal_start.clone())), Err(ProjectionError::Tool(_))));
    assert!(matches!(initial_versions(false, true).stage(update(start())), Err(ProjectionError::Tool(_))));
    let (projection, outputs) = initial_versions(false, true).stage(update_v2(minimal_start.clone())).unwrap();
    assert_eq!(tools(&outputs).len(), 1);
    assert_eq!(tools(&outputs)[0]["state"], "tool_call_start");
    assert!(tools(&outputs)[0]["data"].get("args").is_none());
    assert!(tools(&projection.stage(update_v2(minimal_start)).unwrap().1).is_empty());
    let (_, outputs) = projection.stage(update_v2(minimal_result)).unwrap();
    assert_eq!(tools(&outputs)[0]["state"], "tool_call_end");
    assert!(tools(&outputs)[0]["data"].get("result").is_none());
    assert!(tools(&outputs)[0]["data"].get("isError").is_none());
    let mut bad_args = json!({"phase":"start","toolCallId":"call-bad","name":"search","args":[]});
    assert!(matches!(initial_versions(false, true).stage(update_v2(bad_args.clone())), Err(ProjectionError::Tool(_))));
    bad_args["args"] = json!({"q":"ok"});
    let (_, outputs) = initial_versions(false, true).stage(update_v2(bad_args)).unwrap();
    assert_eq!(tools(&outputs)[0]["data"]["args"], json!({"q":"ok"}));
    let ambiguous: StreamResponse = serde_json::from_value(json!({"statusUpdate":{"taskId":"task","contextId":"context",
        "status":{"state":"TASK_STATE_WORKING","message":{"messageId":"both","role":"ROLE_AGENT",
            "metadata":{EXTENSION:{"version":"1"}},"extensions":[EXTENSION_V2],"parts":[{"data":start()}]}}}})).unwrap();
    assert!(matches!(initial_versions(true, true).stage(ambiguous), Err(ProjectionError::Tool(_))));
}
