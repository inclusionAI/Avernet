use super::*;

#[tokio::test]
async fn loop_get_rerun_node_graph_and_pending_preserve_shared_projection() {
    let mut fixture: Value = serde_json::from_str(include_str!("../../../../../../tests/fixtures/fixed_loop_api.json")).unwrap();
    fixture["graph"]["loops"]["rounds"]["continue_display_name"] = serde_json::json!("继续修订");
    fixture["graph"]["nodes"][0]["assignee_display_name"] = serde_json::json!("资料研究员");
    for edge in fixture["graph"]["edges"].as_array_mut().unwrap() {
        edge["display_name"] = serde_json::json!("自定义连线");
    }
    let service = Arc::new(FakeRuntimeService {
        next_run: Mutex::new(Some(serde_json::from_value(fixture["run"].clone()).unwrap())),
        next_rerun: Mutex::new(Some(RerunStateMachineOutcome { view: serde_json::from_value(fixture["run"].clone()).unwrap(), created: true })),
        next_node: Mutex::new(Some(serde_json::from_value(fixture["node"].clone()).unwrap())),
        next_graph: Mutex::new(Some(serde_json::from_value(fixture["graph"].clone()).unwrap())),
        next_pending: Mutex::new(serde_json::from_value(fixture["pending_later"].clone()).unwrap()),
        ..Default::default()
    });
    let app = test_router(service.clone());
    for (method, suffix, key, status) in [
        ("GET", "", "run", StatusCode::OK),
        ("POST", "/reruns", "run", StatusCode::CREATED),
        ("GET", "/nodes/historical-work-3", "node", StatusCode::OK),
        ("GET", "/graph", "graph", StatusCode::OK),
        ("GET", "/pending-human-nodes", "pending_later", StatusCode::OK),
    ] {
        let response = app.clone().oneshot(Request::builder().method(method)
            .uri(format!("/api/v1/collaboration/state-machine-runs/run-1{suffix}"))
            .header("x-test-auth", "yes").body(Body::empty()).unwrap()).await.unwrap();
        assert_eq!(response.status(), status, "{suffix}");
        let body = response_json(response).await;
        assert_eq!(body["code"], 20000);
        let value = &body["data"];
        match key {
            "run" => assert_eq!(value["node_execution_metadata"], fixture[key]["node_execution_metadata"]),
            "node" => assert_eq!(value["execution"], fixture[key]["execution"]),
            "graph" => {
                assert_eq!(value["loops"], fixture[key]["loops"]);
                assert_eq!(value["definition"], fixture[key]["definition"]);
                assert_eq!(value["nodes"], fixture[key]["nodes"]);
                assert_eq!(value["edges"], fixture[key]["edges"]);
            }
            _ => assert_eq!(value, &fixture[key]),
        }
    }
    *service.next_pending.lock().unwrap() = serde_json::from_value(fixture["pending_first"].clone()).unwrap();
    let response = app.oneshot(Request::builder().uri("/api/v1/collaboration/state-machine-runs/run-1/pending-human-nodes")
        .header("x-test-auth", "yes").body(Body::empty()).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response_json(response).await["data"], fixture["pending_first"]);
}
