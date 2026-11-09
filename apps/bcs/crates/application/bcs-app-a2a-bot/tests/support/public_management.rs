use super::*;
use bcs_service_api::application::a2a_bot::RegisterAgentCommand;
use bcs_service_api::types::a2a_bot::{
    A2aCardSource,
    a2a_management::{ManagementCode as Code, SOURCE_AGENT_EXTENSION},
};

fn card(name: &str, id: Option<&str>) -> serde_json::Value {
    let mut card = json!({"name":name,"description":"完整服务说明","version":"1.0.0",
        "supportedInterfaces":[{"url":"https://example.org/agent","protocolBinding":"JSONRPC","protocolVersion":"1.0"}],
        "capabilities":{"streaming":true},"defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],
        "skills":[{"id":"query","name":"查询","description":"完整技能说明","tags":[],"custom":{"keep":true}}]});
    if let Some(id) = id {
        card["capabilities"]["extensions"] = json!([{"uri":SOURCE_AGENT_EXTENSION,"required":false,
        "params":{"agentId":id,"party":"first_party","future":[1,2]}}]);
    }
    card
}
async fn public_fixture() -> (RegistrationChain, A2aBotManagement) {
    let (chain, service, _, _) = fixture().await;
    chain
        .db
        .execute(DbStatement::new(
            "UPDATE bcs_providers SET slug='orders' WHERE provider_id='provider-a'",
        ))
        .await
        .unwrap();
    (chain, service)
}
fn request(raw: serde_json::Value) -> RegisterAgentCommand {
    RegisterAgentCommand {
        visibility: Default::default(),
        provider_admin_token: "test-admin".into(),
        owners: vec!["delegated-owner".into()],
        summary: None,
        source: A2aCardSource::Inline(raw),
        now_ms: 1000,
    }
}
fn code(error: ServiceError) -> Code {
    match error {
        ServiceError::A2aManagement(error) => error.code,
        other => panic!("unexpected error {other:?}"),
    }
}

#[tokio::test]
async fn public_registration_query_update_and_inline_refresh_follow_contract() {
    let (chain, service) = public_fixture().await;
    let raw = card("订单助手", Some("001Ab"));
    let registered = service.register_agent(request(raw.clone())).await.unwrap();
    assert_eq!(registered.agent_card, raw);
    assert_eq!(registered.provider_name, "orders");
    assert!(!registered.bot_id.is_empty());
    assert_eq!(registered.agent_name, "订单助手");
    let serialized = serde_json::to_value(&registered).unwrap();
    assert!(serialized.get("bot_runtime_token").is_none());
    assert_eq!(serialized.as_object().unwrap().len(), 5);
    assert_eq!(
        service
            .get_agent("test-admin", "订单助手")
            .await
            .unwrap()
            .bot_id,
        registered.bot_id
    );
    assert_eq!(
        code(
            service
                .refresh_agent("test-admin", "订单助手")
                .await
                .unwrap_err()
        ),
        Code::CardUrlRequired
    );
    let mut next = raw.clone();
    next["name"] = json!("订单助理");
    next["capabilities"]["extensions"][0]["params"]["party"] = json!("second_party");
    let updated = service
        .update_agent(
            "test-admin",
            "订单助手",
            A2aCardSource::Inline(next.clone()),
        )
        .await
        .unwrap();
    assert_eq!(updated.bot_id, registered.bot_id);
    assert_eq!(updated.agent_card, next);
    assert_eq!(
        code(
            service
                .get_agent("test-admin", "订单助手")
                .await
                .unwrap_err()
        ),
        Code::BotNotFound
    );
    assert_delegated_owner(&chain, &registered.bot_id).await;
    // Old names are released; source IDs remain attached to the existing Bot.
    service
        .register_agent(request(card("订单助手", Some("different"))))
        .await
        .unwrap();
}

#[tokio::test]
async fn source_conflicts_and_id_changes_roll_back_entire_operation() {
    let (chain, service) = public_fixture().await;
    let first = service
        .register_agent(request(card("订单助手", Some("001Ab"))))
        .await
        .unwrap();
    assert_eq!(
        code(
            service
                .register_agent(request(card("另一个助手", Some("001Ab"))))
                .await
                .unwrap_err()
        ),
        Code::SourceAgentConflict
    );
    for id in [None, Some("001ab"), Some("1Ab")] {
        assert_eq!(
            code(
                service
                    .update_agent(
                        "test-admin",
                        "订单助手",
                        A2aCardSource::Inline(card("不应改名", id))
                    )
                    .await
                    .unwrap_err()
            ),
            Code::SourceAgentChangeNotAllowed
        );
        assert_eq!(
            service
                .get_agent("test-admin", "订单助手")
                .await
                .unwrap()
                .agent_card,
            first.agent_card
        );
    }
    let rows = chain
        .db
        .query(DbStatement::new(
            "SELECT COUNT(*) AS n FROM bcs_bots WHERE actor_kind='bot'",
        ))
        .await
        .unwrap();
    assert_eq!(rows[0].get_i64("n").unwrap(), Some(1));
    // Exact-string uniqueness preserves case and leading zeros.
    service
        .register_agent(request(card("大小写不同", Some("001ab"))))
        .await
        .unwrap();
}

#[tokio::test]
async fn public_admission_rejects_required_authentication_and_invalid_fields_before_writing() {
    let (chain, service) = public_fixture().await;
    let mut bearer = card("订单助手", None);
    bearer["securitySchemes"] = json!({"bearer":{"httpAuthSecurityScheme":{"scheme":"bearer"}}});
    bearer["securityRequirements"] = json!([{"schemes":{"bearer":{"list":[]}}}]);
    assert_eq!(
        code(service.register_agent(request(bearer)).await.unwrap_err()),
        Code::CardAuthUnsupported
    );
    for field in [
        "name",
        "description",
        "version",
        "skills",
        "capabilities",
        "defaultInputModes",
    ] {
        let mut raw = card("订单助手", None);
        raw.as_object_mut().unwrap().remove(field);
        assert!(
            service.register_agent(request(raw)).await.is_err(),
            "{field}"
        );
    }
    let mut invalid = request(card("订单助手", None));
    invalid.summary = Some("界".repeat(4097));
    assert_eq!(
        code(service.register_agent(invalid).await.unwrap_err()),
        Code::InvalidRequest
    );
    assert_empty(&chain).await;
}

#[tokio::test]
async fn parallel_registrations_cannot_commit_duplicate_source_ids() {
    let (chain, service) = public_fixture().await;
    let (a, b) = tokio::join!(
        service.register_agent(request(card("助手甲", Some("same")))),
        service.register_agent(request(card("助手乙", Some("same"))))
    );
    assert_ne!(a.is_ok(), b.is_ok(), "exactly one source owner must commit");
    let rows = chain
        .db
        .query(DbStatement::new(
            "SELECT COUNT(*) AS n FROM bcs_bots WHERE actor_kind='bot'",
        ))
        .await
        .unwrap();
    assert_eq!(rows[0].get_i64("n").unwrap(), Some(1));
}

#[tokio::test]
async fn url_registration_and_refresh_preserve_the_source_and_full_card() {
    let (_, service) = public_fixture().await;
    let mut command = request(card("unused", None));
    command.source = A2aCardSource::Url("https://example.org/card.json".into());
    let initial = service.register_agent(command).await.unwrap();
    assert_eq!(initial.agent_name, "Remote");
    assert_eq!(
        initial.agent_card_url.as_deref(),
        Some("https://example.org/card.json")
    );
    let refreshed = service.refresh_agent("test-admin", "Remote").await.unwrap();
    assert_eq!(refreshed.bot_id, initial.bot_id);
    assert_eq!(refreshed.agent_card, initial.agent_card);
}

#[tokio::test]
async fn registration_persists_visibility_atomically_and_card_updates_preserve_it() {
    use bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::{Protected, Public};
    for visibility in [Protected, Public] {
        let (chain, service) = public_fixture().await;
        let mut command = request(card("可见性测试", None));
        command.visibility = visibility;
        let registered = service.register_agent(command).await.unwrap();
        let read_visibility = || DbStatement::with_params(
            "SELECT visibility FROM bcs_bots WHERE bot_uuid=?",
            vec![registered.bot_id.clone().into()]);
        let rows = chain.db.query(read_visibility()).await.unwrap();
        assert_eq!(rows[0].get_string("visibility").unwrap().as_deref(), Some(visibility.as_str()));
        service.update_agent("test-admin", "可见性测试",
            A2aCardSource::Inline(card("更新后的助手", None))).await.unwrap();
        let rows = chain.db.query(read_visibility()).await.unwrap();
        assert_eq!(rows[0].get_string("visibility").unwrap().as_deref(), Some(visibility.as_str()));
    }
}

#[tokio::test]
async fn visibility_only_update_preserves_card_and_combined_update_rolls_back_on_failure() {
    use bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::{Protected,Public};
    let (chain,service)=public_fixture().await;
    let raw=card("权限测试",None);
    let registered=service.register_agent(request(raw.clone())).await.unwrap();
    let updated=service.update_agent_with_visibility("test-admin","权限测试",None,Some(Public)).await.unwrap();
    assert_eq!(updated.agent_card,raw);
    let read=|| DbStatement::with_params("SELECT visibility FROM bcs_bots WHERE bot_uuid=?",vec![registered.bot_id.clone().into()]);
    assert_eq!(chain.db.query(read()).await.unwrap()[0].get_string("visibility").unwrap().as_deref(),Some("public"));
    chain.db.execute(DbStatement::new("CREATE TRIGGER reject_visibility BEFORE UPDATE OF visibility ON bcs_bots BEGIN SELECT RAISE(ABORT,'visibility failure'); END")).await.unwrap();
    assert!(service.update_agent_with_visibility("test-admin","权限测试",Some(A2aCardSource::Inline(card("改名失败",None))),Some(Protected)).await.is_err());
    assert_eq!(service.get_agent("test-admin","权限测试").await.unwrap().agent_card,raw);
    assert_eq!(chain.db.query(read()).await.unwrap()[0].get_string("visibility").unwrap().as_deref(),Some("public"));
    assert!(service.update_agent_with_visibility("wrong-token","权限测试",None,Some(Protected)).await.is_err());
}
