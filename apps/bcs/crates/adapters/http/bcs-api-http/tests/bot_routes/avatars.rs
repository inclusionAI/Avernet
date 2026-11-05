use super::fixtures::*;
use bcs_service_api::application::v1::Bot;
use serde_json::{Value, json};
use std::sync::Arc;
use tower::ServiceExt;
use axum::http::StatusCode;

#[tokio::test]
async fn mine_serializes_avatar_links_and_explicit_nulls_in_the_v1_envelope() {
    let service = Arc::new(FakeBotService::default());
    let mut no_avatar = physical_bot();
    no_avatar.bot_id = "without-avatar".into();
    no_avatar.avatar_url = None;
    *service.mine_items.lock().expect("mine items") = Some(vec![
        Bot::Physical(physical_bot()),
        Bot::Physical(no_avatar),
        Bot::Human(human_bot()),
    ]);
    let response = test_router(service).oneshot(request(
        "GET",
        "/openapi/v1/collaboration/bots/mine",
        Value::Null,
    )).await.expect("mine response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["total"], 3);
    let items = body["data"]["items"].as_array().expect("items");
    for (item, expected) in items.iter().zip([
        json!("https://example.com/bot.png"), Value::Null, Value::Null,
    ]) {
        assert_eq!(item.get("avatar_url"), Some(&expected));
    }
}

#[test]
fn bot_responses_without_avatar_remain_deserializable() {
    for original in [Bot::Physical(physical_bot()), Bot::Human(human_bot())] {
        let mut legacy = serde_json::to_value(original).expect("serialize");
        legacy.as_object_mut().expect("object").remove("avatar_url");
        let decoded: Bot = serde_json::from_value(legacy).expect("legacy response");
        assert_eq!(serde_json::to_value(decoded).expect("serialize").get("avatar_url"),
            Some(&Value::Null));
    }
}
