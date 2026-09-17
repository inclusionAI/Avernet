use super::*;

const LOOP_TEMPLATE: &str = include_str!("../../../../../seeds/collaboration-templates/zh-CN/write-review-loop.yaml");

#[tokio::test]
async fn fixed_loop_opt_in_controls_validation_creation_and_judged_exit() {
    for (enabled, outcomes) in [(false, vec!["approved"]), (true, vec!["approved"]),
        (true, vec!["revise", "approved"]), (true, vec!["revise", "revise", "revise"])] {
        let body_steps = outcomes.len() * 2;
        let steps = body_steps + 2;
        let exits_on_approval = outcomes.last() == Some(&"approved");
        let decisions = Arc::new(tokio::sync::Mutex::new(std::collections::VecDeque::from(outcomes)));
        let judge_app = Router::new().route("/v1/chat/completions", post({
            let decisions = decisions.clone();
            move || {
                let decisions = decisions.clone();
                async move {
                    let outcome = decisions.lock().await.pop_front().expect("unexpected extra review after exit");
                    Json(json!({"choices": [{"message": {"content": json!({
                        "outcome": outcome, "reason": "editorial test decision", "confidence": 1.0,
                        "checked_criteria": [], "retry_instruction": ""
                    }).to_string()}}]}))
                }
            }
        }));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let judge_addr = listener.local_addr().unwrap();
        let judge_handle = tokio::spawn(async move { axum::serve(listener, judge_app).await.unwrap(); });
        let provider = start_provider_webhook().await;
        let bots_dir = create_temp_bots_dir();
        let mut config = create_test_config(&bots_dir.path().to_path_buf());
        config.collaboration.experimental_fixed_loop_execution = enabled;
        config.metrics.enabled = false;
        config.llm.provider_type = LlmProviderType::OpenAiCompatible;
        config.llm.base_url = format!("http://{judge_addr}/v1");
        config.llm.api_key_env = None;
        config.llm.api_key = Some(Secret::new("test-loop-judge-key".into()));
        let (addr, handle) = start_test_server_with_config(config).await;
        let client = reqwest::Client::builder().no_proxy().build().unwrap();
        let mut driver = MockBot::connect(addr).await;
        driver.register("Loop test driver", &["drive"], addr).await;
        let writer = register_provider_bot(&client, addr, provider.url(), "loop-writer", "writer").await;
        let polisher = register_provider_bot(&client, addr, provider.url(), "loop-polisher", "polisher").await;
        let reviewer = register_provider_bot(&client, addr, provider.url(), "loop-reviewer", "reviewer").await;

        let response = client.post(format!("http://{addr}/collaboration/definitions/validate"))
            .json(&json!({"definition_yaml": LOOP_TEMPLATE})).send().await.unwrap();
        assert_eq!(response.status(), reqwest::StatusCode::OK);
        let validation: Value = response.json().await.unwrap();
        assert_eq!(validation["valid"], true, "{validation}");
        assert_eq!(validation["warnings"].as_array().into_iter().flatten()
            .any(|warning| warning["code"] == "VALIDATION_ONLY_FEATURE"), !enabled);
        assert_eq!(validation["graph"]["nodes"].as_array().unwrap().len(), 9);

        let response = client.post(format!("http://{addr}/groups"))
            .bearer_auth(&driver.token)
            .json(&json!({
                "driver_bot": driver.bot_id,
                "group_strategy": "state_machine",
                "participants": [{"bot_uuid": driver.bot_id}, {"bot_uuid": writer.bot_uuid}, {"bot_uuid": reviewer.bot_uuid}, {"bot_uuid": polisher.bot_uuid}],
                "participant_bindings": {
                    "writer": {"source": "manual", "bot_ids": [writer.bot_uuid]},
                    "reviewer": {"source": "manual", "bot_ids": [reviewer.bot_uuid]},
                    "polisher": {"source": "manual", "bot_ids": [polisher.bot_uuid]}
                },
                "collaboration_definition_yaml": LOOP_TEMPLATE
            })).send().await.unwrap();
        let status = response.status();
        let group: Value = response.json().await.unwrap();
        if !enabled {
            assert_eq!(status, reqwest::StatusCode::BAD_REQUEST, "{group}");
            assert!(group.to_string().contains("v2 execution is disabled"), "{group}");
            handle.abort();
            judge_handle.abort();
            continue;
        }
        assert!(status.is_success(), "{status}: {group}");
        let group_id = group["id"].as_str().unwrap();
        provider.capture.clear().await;
        let response = client.post(format!("http://{addr}/groups/{group_id}/state-machine-runs"))
            .json(&json!({"input": {"question": "Write and review a short test report"}}))
            .send().await.unwrap();
        let status = response.status();
        let started: Value = response.json().await.unwrap();
        assert!(status.is_success(), "{status}: {started}");
        let run_id = started["run"]["run_id"].as_str().unwrap();
        assert_eq!(started["nodes"].as_array().unwrap().len(), 9);

        let mut dispatch_ids = std::collections::BTreeSet::new();
        for step in 0..steps {
            let request = provider.capture.wait_for_method("chat.send").await;
            let (bot, role) = if step < body_steps && step % 2 == 1 { (&reviewer, "reviewer") }
                else if step == body_steps && exits_on_approval { (&polisher, "polisher") }
                else { (&writer, "writer") };
            assert_eq!(request.body["to_bot"]["provider_bot_ref"], role);
            let dispatch_id = request.body["id"].as_str().unwrap();
            assert!(dispatch_ids.insert(dispatch_id.to_string()), "duplicate dispatch: {dispatch_id}");
            if step > 0 {
                assert!(request.body["message"].to_string().contains(&format!("loop-test-output-{}", step - 1)),
                    "each task must receive the preceding task output: {}", request.body);
            }
            provider.capture.clear().await;
            let response = client.post(format!("http://{addr}/bot/events"))
                .header("X-BCN-Provider-Id", &bot.provider_id)
                .bearer_auth(&bot.bot_runtime_token)
                .json(&json!({"run_id": dispatch_id, "state": "final",
                    "message": {"text": format!("loop-test-output-{step}")}}))
                .send().await.unwrap();
            let status = response.status();
            let callback: Value = response.json().await.unwrap();
            assert_eq!(status, reqwest::StatusCode::OK, "step {step}: {callback}");
            assert_eq!(callback["ok"], true, "step {step}: {callback}");
        }
        let completed = wait_for_state_machine_run_status(&client, addr, run_id, "completed").await;
        let nodes = completed["nodes"].as_array().unwrap();
        assert_eq!(nodes.iter().filter(|node| node["status"] == "completed").count(), steps, "{completed}");
        assert_eq!(nodes.iter().filter(|node| node["status"] == "skipped").count(), 9 - steps, "{completed}");
        assert!(nodes.iter().any(|node| node["outcome"] == if exits_on_approval { "approved" } else { "revise" }));
        for (id, expected) in [("polish", if exits_on_approval { "completed" } else { "skipped" }),
            ("rewrite", if exits_on_approval { "skipped" } else { "completed" })] {
            assert_eq!(nodes.iter().find(|node| node["node_id"] == id).unwrap()["status"], expected);
        }
        assert!(decisions.lock().await.is_empty());
        assert_eq!(completed["node_execution_metadata"].as_object().unwrap().len(), 6);
        assert!(completed.to_string().contains(&format!("loop-test-output-{}", steps - 1)));
        handle.abort();
        judge_handle.abort();
    }
}
