use super::*;

#[tokio::test]
async fn loop_start_get_rerun_node_graph_and_pending_preserve_shared_projection() {
    let (app, _, runtime, _dir) = test_app_with_collaboration_runtime_and_human_identity().await;
    let mut fixture: Value = serde_json::from_str(include_str!("../../../../../../tests/fixtures/fixed_loop_api.json")).unwrap();
    fixture["graph"]["loops"]["rounds"]["continue_display_name"] = serde_json::json!("继续修订");
    fixture["graph"]["nodes"][0]["assignee_display_name"] = serde_json::json!("资料研究员");
    for edge in fixture["graph"]["edges"].as_array_mut().unwrap() {
        edge["display_name"] = serde_json::json!("自定义连线");
    }
    *runtime.projection_fixture.lock().await = Some(fixture.clone());
    let session_payload = serde_json::json!({"definition_yaml": ONE_SHOT_DEFINITION_YAML, "participant_bindings": {}}).to_string();
    for (method, path, payload, key, status) in [
        ("POST", "/groups/group-1/state-machine-runs", Some("{}"), "run", StatusCode::ACCEPTED),
        ("POST", "/sessions/session-1/state-machine-runs", Some(session_payload.as_str()), "run", StatusCode::ACCEPTED),
        ("GET", "/state-machine-runs/run-1", None, "run", StatusCode::OK),
        ("POST", "/state-machine-runs/run-1/reruns", None, "run", StatusCode::CREATED),
        ("GET", "/state-machine-runs/run-1/nodes/historical-work-3", None, "node", StatusCode::OK),
        ("GET", "/state-machine-runs/run-1/graph", None, "graph", StatusCode::OK),
        ("GET", "/state-machine-runs/run-1/pending-human-nodes", None, "pending_later", StatusCode::OK),
    ] {
        let response = app.clone().oneshot(Request::builder().method(method).uri(path)
            .header("content-type", "application/json").header("authorization", "Bearer driver-token")
            .body(payload.map_or_else(Body::empty, |payload| Body::from(payload.to_owned()))).unwrap()).await.unwrap();
        assert_eq!(response.status(), status, "{path}");
        let value: Value = serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap()).unwrap();
        match key {
            "run" => {
                assert_eq!(value["node_execution_metadata"], fixture[key]["node_execution_metadata"]);
                assert_eq!(value["nodes"].as_array().unwrap().len(), 4);
            }
            "node" => assert_eq!(value["execution"], fixture[key]["execution"]),
            "graph" => {
                assert_eq!(value["loops"], fixture[key]["loops"]);
                assert_eq!(value["definition"], fixture[key]["definition"]);
                assert_eq!(value["nodes"], fixture[key]["nodes"]);
                assert_eq!(value["edges"], fixture[key]["edges"]);
            }
            _ => assert_eq!(value, fixture[key]),
        }
    }
    assert_eq!(&*runtime.queried_node_ids.lock().await, &["historical-work-3"]);
    let mut first = fixture.clone(); first["pending_later"] = fixture["pending_first"].clone();
    *runtime.projection_fixture.lock().await = Some(first);
    let response = app.oneshot(Request::builder().uri("/state-machine-runs/run-1/pending-human-nodes")
        .body(Body::empty()).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let value: Value = serde_json::from_slice(&to_bytes(response.into_body(), usize::MAX).await.unwrap()).unwrap();
    assert_eq!(value, fixture["pending_first"]);
}
