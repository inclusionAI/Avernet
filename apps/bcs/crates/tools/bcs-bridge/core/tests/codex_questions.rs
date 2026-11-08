use futures::StreamExt;
use serde_json::{Value, json};
mod support;

#[tokio::test]
async fn codex_question_webhook_returns_selected_and_free_text_answers_and_cancellation() {
    for (cancel, asynchronous) in [(false,false),(true,false),(false,true),(true,true)] {
        let url = support::spawn_app_with_mock("mock_codex_app_server.py", "codex").await;
        let client = reqwest::Client::builder().no_proxy().timeout(std::time::Duration::from_secs(5)).build().unwrap();
        let response = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
            .header("X-BCN-Protocol-Version", "2.0")
            .json(&json!({"type":"req","id":"question-run","method":"chat.send","session_id":"s-1",
                "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},
                "message":{"role":"user","content":[{"type":"text","text":if asynchronous {"ASYNC_USER:choose"} else {"ASK_USER:choose"}}]}}))
            .send().await.unwrap();
        assert_eq!(response.status(), 200);
        let mut stream = response.bytes_stream();
        let mut acc = String::new();
        let requested = loop {
            let chunk = stream.next().await.expect("no requested frame").unwrap();
            acc.push_str(&String::from_utf8_lossy(&chunk));
            if let Some(event) = frames(&acc).into_iter().find(|v| v["phase"] == "requested") { break event; }
        };
        assert_eq!(requested["kind"], "ask_user");
        let choice_id = if asynchronous {"question_1"} else {"design"};
        let text_id = if asynchronous {"question_2"} else {"details"};
        assert_eq!(requested["questions"][0]["questionId"], choice_id);
        assert_eq!(requested["questions"][0]["options"][0]["value"], "A");
        assert!(!acc.contains("\"state\":\"final\""));
        let mut params = json!({"bcsRunId":"question-run","runId":"question-run",
            "interactionId":requested["interactionId"],"kind":"ask_user","idempotencyKey":"answer",
            "action":if cancel {"cancel"} else {"submit"}});
        if !cancel { params["answers"] = json!({choice_id:{"values":["B"]},text_id:{"values":["my details"]}}); }
        for id in ["resolve", "replay"] {
            let ack = client.post(format!("{url}/webhook")).bearer_auth("tok-b2p")
                .json(&json!({"type":"req","id":id,"method":"interaction.resolve","session_id":"s-1",
                    "to_bot":{"provider_id":"bridge-1","provider_bot_ref":"worker-1"},"params":params}))
                .send().await.unwrap().json::<Value>().await.unwrap();
            assert_eq!(ack["ok"], true);
        }
        while let Some(chunk) = stream.next().await { acc.push_str(&String::from_utf8_lossy(&chunk.unwrap())); }
        let events = frames(&acc);
        assert_eq!(events.iter().filter(|v| v["phase"] == "resolved").count(), 1);
        let final_event = events.iter().find(|v| v["state"] == "final").expect("no final answer");
        let text = final_event["message"]["content"][0]["text"].as_str().expect("final message text");
        if asynchronous {
            if cancel { assert_eq!(text,"Question sent."); } else {
                assert!(text.contains("用户已回答") && text.contains("my details") && text.contains("B"),"{text}");
            }
            continue;
        }
        let answer: Value = serde_json::from_str(text).unwrap();
        assert_eq!(answer, if cancel {json!({"answers":{}})} else {
            json!({"answers":{"design":{"answers":["B"]},"details":{"answers":["my details"]}}})});
    }
}

fn frames(text: &str) -> Vec<Value> {
    text.lines().filter_map(|line| line.strip_prefix("data: "))
        .filter_map(|line| serde_json::from_str(line).ok()).collect()
}
