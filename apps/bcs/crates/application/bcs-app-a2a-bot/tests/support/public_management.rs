use super::*;

#[path = "public_management_by_id.rs"]
mod by_id;
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
    if id.is_some() {
        card["capabilities"]["extensions"] = json!([{"uri":SOURCE_AGENT_EXTENSION,"required":false,
        "params":{"party":"first_party","future":[1,2]}}]);
    }
    card
}
async fn public_fixture() -> (RegistrationChain, A2aBotManagement) {
    let (chain, service, _, _) = fixture().await;
    chain
        .db
        .execute(DbStatement::new(
            "UPDATE bcs_providers SET slug='orders', config='{\"downlink\":{\"enabled\":true,\"auth_mode\":\"static_bearer\"},\"a2a\":{\"default_owner\":\"default-owner\"}}' WHERE provider_id='provider-a'",
        ))
        .await
        .unwrap();
    (chain, service)
}
fn request(raw: serde_json::Value) -> RegisterAgentCommand {
    RegisterAgentCommand {
        provider_bot_ref: uuid::Uuid::new_v4().simple().to_string(),        visibility: Default::default(),
        provider_admin_token: "test-admin".into(),
        owners: Some(vec!["delegated-owner".into()]),
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
    assert_eq!(serialized.as_object().unwrap().len(), 6);
    assert_eq!(
        service
            .get_agent_by_id("test-admin", &registered.bot_id)
            .await
            .unwrap()
            .bot_id,
        registered.bot_id
    );
    assert_eq!(
        code(
            service
                .refresh_agent_by_id("test-admin", &registered.bot_id)
                .await
                .unwrap_err()
        ),
        Code::CardUrlRequired
    );
    let mut next = raw.clone();
    next["name"] = json!("订单助理");
    next["capabilities"]["extensions"][0]["params"]["party"] = json!("second_party");
    let updated = service
        .update_agent_by_id(
            "test-admin",
            &registered.bot_id,
            A2aCardSource::Inline(next.clone()),
        )
        .await
        .unwrap();
    assert_eq!(updated.bot_id, registered.bot_id);
    assert_eq!(updated.agent_card, next);
    assert_eq!(service.get_agent_by_id("test-admin", &registered.bot_id).await.unwrap().agent_card, next);
    assert_delegated_owner(&chain, &registered.bot_id).await;
    // Old names are released; source IDs remain attached to the existing Bot.
    let other = service.register_agent(request(card("订单助手", Some("different")))).await.unwrap();
    next["description"] = json!("仍然更新原 Bot");
    let again = service.update_agent_by_id("test-admin", &registered.bot_id, A2aCardSource::Inline(next.clone())).await.unwrap();
    assert_eq!(again.bot_id, registered.bot_id);
    assert_eq!(again.agent_card, next);
    assert_eq!(service.get_agent_by_id("test-admin", &other.bot_id).await.unwrap().agent_name, "订单助手");
}

#[tokio::test]
async fn same_names_are_allowed_but_provider_refs_conflict_and_party_can_change() {
    let (_,service)=public_fixture().await;
    let mut command=request(card("同名助手",Some("unused")));
    command.provider_bot_ref="platform-001".into();
    let first=service.register_agent(command.clone()).await.unwrap();
    assert!(matches!(service.register_agent(command).await,Err(ServiceError::Conflict(_))));
    let mut other=request(card("同名助手",None));other.provider_bot_ref="platform-002".into();
    let second=service.register_agent(other).await.unwrap();
    assert_ne!(first.bot_id,second.bot_id);
    let raw=card("同名助手",None);
    let updated=service.update_agent_by_id("test-admin",&first.bot_id,A2aCardSource::Inline(raw.clone())).await.unwrap();
    assert_eq!(updated.provider_bot_ref,"platform-001");
    assert_eq!(updated.agent_card,raw);
    let found=service.find_agents_by_provider_ref("test-admin","platform-001").await.unwrap();
    assert_eq!(found[0].bot_id,first.bot_id);
    assert_eq!(service.resolve_provider_ref_target("orders","platform-001").await.unwrap(),first.bot_id);
    assert_eq!(service.resolve_provider_ref_target("orders","platform-002").await.unwrap(),second.bot_id);
    assert_eq!(service.get_agent_by_id("test-admin",&second.bot_id).await.unwrap().agent_card,card("同名助手",None));
}

#[tokio::test]
async fn public_registration_accepts_maximum_core_reference_length() {
    let (_,service)=public_fixture().await;
    let mut command=request(card("边界注册",None));
    command.provider_bot_ref="x".repeat(128);
    let registered=service.register_agent(command).await.unwrap();
    assert_eq!(registered.provider_bot_ref,"x".repeat(128));
    let found=service.find_agents_by_provider_ref("test-admin",&registered.provider_bot_ref).await.unwrap();
    assert_eq!(found[0].bot_id,registered.bot_id);
}

#[tokio::test]
async fn public_registration_requires_owner_and_valid_platform_ref_before_fetch() {
    let (chain,service)=public_fixture().await;
    for reference in ["", " bad", "bad ", "bad/ref", "中文", &"x".repeat(129)] {
        let mut command=request(card("注册检查",None));
        command.provider_bot_ref=reference.into();
        command.source=A2aCardSource::Url("not-a-url".into());
        assert_eq!(code(service.register_agent(command).await.unwrap_err()),Code::InvalidRequest);
    }
    for owners in [vec!["".into()],vec!["owner".into(),"another".into()]] {
        let mut command=request(card("注册检查",None));command.owners=Some(owners);
        assert_eq!(code(service.register_agent(command).await.unwrap_err()),Code::InvalidOwner);
    }
    for owners in [None, Some(vec![])] {
        let mut command=request(card("默认负责人",None));command.owners=owners;
        let registered=service.register_agent(command).await.unwrap();
        assert!(!registered.bot_id.is_empty());
    }
}

#[tokio::test]
async fn missing_default_owner_rejects_empty_owner_registration_atomically() {
    let (chain,service)=public_fixture().await;
    chain.db.execute(DbStatement::new("UPDATE bcs_providers SET config='{\"downlink\":{\"enabled\":true,\"auth_mode\":\"static_bearer\"}}' WHERE provider_id='provider-a'")).await.unwrap();
    let mut command=request(card("无默认负责人",None));command.owners=None;
    assert_eq!(code(service.register_agent(command).await.unwrap_err()),Code::InvalidOwner);
    assert_empty(&chain).await;
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
async fn parallel_registrations_cannot_commit_duplicate_provider_refs() {
    let (chain,service)=public_fixture().await;
    let mut left=request(card("助手甲",None));left.provider_bot_ref="same-ref".into();
    let mut right=request(card("助手乙",None));right.provider_bot_ref="same-ref".into();
    let (a,b)=tokio::join!(service.register_agent(left),service.register_agent(right));
    assert_ne!(a.is_ok(),b.is_ok());
    let rows=chain.db.query(DbStatement::new("SELECT COUNT(*) AS n FROM bcs_bots WHERE actor_kind='bot'")).await.unwrap();
    assert_eq!(rows[0].get_i64("n").unwrap(),Some(1));
}

#[tokio::test]
async fn url_registration_and_refresh_preserve_the_source_and_full_card() {
    let (chain, service) = public_fixture().await;
    let mut command = request(card("unused", None));
    command.visibility = A2aRegistrationVisibility::Private;
    command.source = A2aCardSource::Url("https://example.org/card.json".into());
    let initial = service.register_agent(command).await.unwrap();
    assert_eq!(initial.agent_name, "Remote");
    assert_eq!(
        initial.agent_card_url.as_deref(),
        Some("https://example.org/card.json")
    );
    let refreshed = service.refresh_agent_by_id("test-admin", &initial.bot_id).await.unwrap();
    assert_eq!(refreshed.bot_id, initial.bot_id);
    assert_eq!(refreshed.agent_card, initial.agent_card);
    let rows=chain.db.query(DbStatement::with_params("SELECT visibility FROM bcs_bots WHERE bot_uuid=?",vec![initial.bot_id.into()])).await.unwrap();
    assert_eq!(rows[0].get_string("visibility").unwrap().as_deref(),Some("private"));
}

#[tokio::test]
async fn registration_persists_visibility_atomically_and_card_updates_preserve_it() {
    use bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::{Protected, Public, Private};
    for visibility in [Protected, Public, Private] {
        let (chain, service) = public_fixture().await;
        let mut command = request(card("可见性测试", None));
        command.visibility = visibility;
        let registered = service.register_agent(command).await.unwrap();
        let read_visibility = || DbStatement::with_params(
            "SELECT visibility FROM bcs_bots WHERE bot_uuid=?",
            vec![registered.bot_id.clone().into()]);
        let rows = chain.db.query(read_visibility()).await.unwrap();
        assert_eq!(rows[0].get_string("visibility").unwrap().as_deref(), Some(visibility.as_str()));
        service.update_agent_by_id("test-admin", &registered.bot_id,
            A2aCardSource::Inline(card("更新后的助手", None))).await.unwrap();
        let rows = chain.db.query(read_visibility()).await.unwrap();
        assert_eq!(rows[0].get_string("visibility").unwrap().as_deref(), Some(visibility.as_str()));
    }
}

#[tokio::test]
async fn visibility_only_update_preserves_card_and_combined_update_rolls_back_on_failure() {
    use bcs_service_api::types::a2a_bot::A2aRegistrationVisibility::{Protected,Public,Private};
    let (chain,service)=public_fixture().await;
    // The minimal chain fixture omits this independent Human-facing setting.
    chain.db.execute(DbStatement::new("ALTER TABLE bcs_bots ADD COLUMN user_visibility TEXT NOT NULL DEFAULT 'protected'")).await.unwrap();
    let raw=card("权限测试",None);
    let registered=service.register_agent(request(raw.clone())).await.unwrap();
    let before=chain.db.query(DbStatement::with_params("SELECT visibility,user_visibility FROM bcs_bots WHERE bot_uuid=?",vec![registered.bot_id.clone().into()])).await.unwrap();
    assert_eq!(before[0].get_string("visibility").unwrap().as_deref(),Some("protected"));
    let user_visibility=before[0].get_string("user_visibility").unwrap();
    for visibility in [Private,Protected,Public] {
        let updated=service.update_agent_by_id_with_visibility("test-admin",&registered.bot_id,None,Some(visibility)).await.unwrap();
        assert_eq!(updated.agent_card,raw);
        let rows=chain.db.query(DbStatement::with_params("SELECT visibility,user_visibility FROM bcs_bots WHERE bot_uuid=?",vec![registered.bot_id.clone().into()])).await.unwrap();
        assert_eq!(rows[0].get_string("visibility").unwrap().as_deref(),Some(visibility.as_str()));
        assert_eq!(rows[0].get_string("user_visibility").unwrap(),user_visibility);
    }
    let updated=service.update_agent_by_id_with_visibility("test-admin",&registered.bot_id,None,Some(Public)).await.unwrap();
    assert_eq!(updated.agent_card,raw);
    let read=|| DbStatement::with_params("SELECT visibility FROM bcs_bots WHERE bot_uuid=?",vec![registered.bot_id.clone().into()]);
    assert_eq!(chain.db.query(read()).await.unwrap()[0].get_string("visibility").unwrap().as_deref(),Some("public"));
    chain.db.execute(DbStatement::new("CREATE TRIGGER reject_visibility BEFORE UPDATE OF visibility ON bcs_bots BEGIN SELECT RAISE(ABORT,'visibility failure'); END")).await.unwrap();
    assert!(service.update_agent_by_id_with_visibility("test-admin",&registered.bot_id,Some(A2aCardSource::Inline(card("改名失败",None))),Some(Private)).await.is_err());
    assert_eq!(service.get_agent_by_id("test-admin",&registered.bot_id).await.unwrap().agent_card,raw);
    assert_eq!(chain.db.query(read()).await.unwrap()[0].get_string("visibility").unwrap().as_deref(),Some("public"));
    assert!(service.update_agent_by_id_with_visibility("wrong-token","权限测试",None,Some(Protected)).await.is_err());
}
