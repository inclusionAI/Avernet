use super::*;
use bcs_service_api::types::a2a_bot::a2a_management::GENERATED_PROVIDER_BOT_REF_PREFIX;

fn with_reference(raw: serde_json::Value, reference: &str) -> RegisterAgentCommand {
    let mut command = request(raw);
    command.provider_bot_ref = Some(reference.into());
    command
}
async fn stored_reference(chain: &RegistrationChain, bot_id: &str) -> String {
    let rows = chain.db.query(DbStatement::with_params(
        "SELECT provider_bot_ref FROM bcs_bots WHERE bot_uuid=?", vec![bot_id.into()])).await.unwrap();
    rows[0].get_string("provider_bot_ref").unwrap().unwrap()
}

#[tokio::test]
async fn explicit_call_identifier_is_stored_returned_and_recovers_the_bot() {
    let (chain, service) = public_fixture().await;
    let registered = service.register_agent(with_reference(card("订单助手", None), "订单-001")).await.unwrap();
    assert_eq!(registered.provider_bot_ref.as_deref(), Some("订单-001"));
    assert_eq!(stored_reference(&chain, &registered.bot_id).await, "订单-001");
    assert_eq!(serde_json::to_value(&registered).unwrap()["provider_bot_ref"], "订单-001");
    let found = service.find_agents_by_provider_bot_ref("test-admin", "订单-001").await.unwrap();
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].bot_id, registered.bot_id);
    assert!(service.find_agents_by_provider_bot_ref("test-admin", "订单-002").await.unwrap().is_empty());
    assert!(service.find_agents_by_provider_bot_ref("wrong-admin", "订单-001").await.is_err());
    // Renames keep the call identifier and its lookup.
    service.update_agent_by_id("test-admin", &registered.bot_id, A2aCardSource::Inline(card("改名后", None))).await.unwrap();
    let renamed = service.find_agents_by_provider_bot_ref("test-admin", "订单-001").await.unwrap();
    assert_eq!((renamed[0].agent_name.as_str(), renamed[0].provider_bot_ref.as_deref()), ("改名后", Some("订单-001")));
}

#[tokio::test]
async fn hosted_agents_are_called_by_their_own_id_independent_of_the_card_source() {
    let (chain, service) = public_fixture().await;
    // A hosted Agent: the Card names the platform Agent ID, callers use the hosted ID.
    let hosted = service.register_agent(with_reference(card("托管助手", Some("platform-42")), "hosted-007")).await.unwrap();
    assert_eq!(hosted.provider_bot_ref.as_deref(), Some("hosted-007"));
    assert_eq!(stored_reference(&chain, &hosted.bot_id).await, "hosted-007");
    assert_eq!(service.find_agents_by_provider_bot_ref("test-admin", "hosted-007").await.unwrap()[0].bot_id, hosted.bot_id);
    assert_eq!(service.find_agents_by_source_id("test-admin", "platform-42").await.unwrap()[0].bot_id, hosted.bot_id);
    // The two keys never resolve through each other.
    assert!(service.find_agents_by_provider_bot_ref("test-admin", "platform-42").await.unwrap().is_empty());
    assert!(service.find_agents_by_source_id("test-admin", "hosted-007").await.unwrap().is_empty());
    // A platform-native Agent may use the same value for both keys.
    let native = service.register_agent(with_reference(card("平台助手", Some("native-1")), "native-1")).await.unwrap();
    assert_eq!(native.provider_bot_ref.as_deref(), Some("native-1"));
    // A source extension alone does not set the call identifier.
    let source_only = service.register_agent(request(card("仅来源", Some("platform-43")))).await.unwrap();
    assert_eq!(source_only.provider_bot_ref, None);
    assert!(stored_reference(&chain, &source_only.bot_id).await.starts_with(GENERATED_PROVIDER_BOT_REF_PREFIX));
}

#[tokio::test]
async fn call_identifiers_are_unique_and_remain_reserved_after_deletion() {
    let (chain, service) = public_fixture().await;
    let first = service.register_agent(with_reference(card("第一个", None), "order-1")).await.unwrap();
    assert_eq!(code(service.register_agent(with_reference(card("第二个", None), "order-1")).await.unwrap_err()),
        Code::ProviderBotRefConflict);
    chain.db.execute(DbStatement::with_params("UPDATE bcs_bots SET is_deleted=1 WHERE bot_uuid=?",
        vec![first.bot_id.clone().into()])).await.unwrap();
    assert!(service.find_agents_by_provider_bot_ref("test-admin", "order-1").await.unwrap().is_empty());
    assert_eq!(code(service.register_agent(with_reference(card("第三个", None), "order-1")).await.unwrap_err()),
        Code::ProviderBotRefConflict);
    // Exact comparison keeps case distinct (SQLite binary collation).
    service.register_agent(with_reference(card("大小写", None), "Order-1")).await.unwrap();
    assert!(service.find_agents_by_provider_bot_ref("test-admin", "ORDER-1").await.unwrap().is_empty());
}

#[tokio::test]
async fn invalid_or_reserved_identifiers_are_rejected_before_fetch_or_write() {
    let (chain, service) = public_fixture().await;
    let generated = format!("{GENERATED_PROVIDER_BOT_REF_PREFIX}x");
    let too_long = "x".repeat(129);
    for reference in ["", " padded", "padded ", "line\nbreak", generated.as_str(), too_long.as_str()] {
        assert_eq!(code(service.register_agent(with_reference(card("非法", None), reference)).await.unwrap_err()),
            Code::InvalidRequest, "{reference:?}");
        assert_eq!(code(service.find_agents_by_provider_bot_ref("test-admin", reference).await.unwrap_err()),
            Code::InvalidRequest, "{reference:?}");
    }
    assert_empty(&chain).await;
}

#[tokio::test]
async fn generated_references_are_hidden_from_responses_and_lookup() {
    let (chain, service) = public_fixture().await;
    let registered = service.register_agent(request(card("无调用标识", None))).await.unwrap();
    assert_eq!(registered.provider_bot_ref, None);
    assert_eq!(serde_json::to_value(&registered).unwrap()["provider_bot_ref"], serde_json::Value::Null);
    let reference = stored_reference(&chain, &registered.bot_id).await;
    assert!(reference.starts_with(GENERATED_PROVIDER_BOT_REF_PREFIX));
    assert_eq!(code(service.find_agents_by_provider_bot_ref("test-admin", &reference).await.unwrap_err()),
        Code::InvalidRequest);
}
