//! Captured from real Harness + official Java SDK HTTP, not a Rust-authored DTO.
use bcs_a2a_http::projection::{Output, Projection};
use serde_json::{json, Value};

#[test]
fn native_java_structured_input_survives_the_official_rust_sdk() {
    let source:Value=serde_json::from_str(include_str!("fixtures/java-structured-exec-input.json")).unwrap();
    let message:a2a::Message=serde_json::from_value(source.clone()).unwrap();
    assert_eq!(message.role,a2a::Role::Agent);
    let task:a2a::Task=serde_json::from_value(json!({"id":"fixture-task","contextId":"fixture-context",
        "status":{"state":"TASK_STATE_INPUT_REQUIRED","message":message}})).unwrap();
    let (_,outputs)=Projection::default().stage(a2a::StreamResponse::Task(task)).unwrap();
    let projected=outputs.into_iter().find_map(|output|match output {
        Output::StatusMessage(message)=>Some(serde_json::to_value(message).unwrap()),_=>None,
    }).unwrap();
    let si="https://github.com/a2aproject/experimental-ext-structured-input/tree/main/v1";
    assert_eq!(projected["parts"][0]["metadata"][si]["type"],"input_schema");
    assert_eq!(projected["parts"][0]["metadata"][si]["id"],"fixture-request");
    assert_eq!(projected["parts"][0]["data"],source["parts"][0]["data"]);
    // ProtoJSON Struct encodes integer-valued annotations as JSON doubles.
    assert_eq!(projected["parts"][0]["data"]["x-bcs-exec"]["version"].as_f64(),Some(1.0));
    assert_eq!(projected["parts"][0]["data"]["properties"]["decisions"]["minProperties"].as_f64(),Some(1.0));
    assert!(projected["parts"][0]["data"]["properties"]["decisions"]["properties"]["tool-1"]["title"]
        .as_str().unwrap().contains("允许调用工具"));
}
