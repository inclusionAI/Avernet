use super::*;

#[tokio::test]
async fn protected_target_requires_friendship_in_a2a_service() {
    let (service, delivery, _) = build_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("bot-target", "protected", None),
        ],
        vec![],
    )
    .await;

    let err = service.chat(chat_command("bot-target")).await.unwrap_err();

    assert!(matches!(err, ServiceError::NotFriends(bot_ids) if bot_ids == vec!["bot-target"]));
    assert!(delivery.frames().await.is_empty());
}
#[tokio::test]
async fn protected_target_allows_self_chat_without_friendship() {
    let (service, delivery, _) = build_service(
        vec![("bot-source", "protected", Some("owner-1"))],
        vec![],
    )
    .await;

    service.chat(chat_command("bot-source")).await.unwrap();

    assert_eq!(delivery.frames().await.len(), 1);
}
#[tokio::test]
async fn protected_friend_and_public_target_are_delivered_by_a2a_service() {
    let (service, delivery, _) = build_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("protected-target", "protected", None),
            ("public-target", "public", None),
        ],
        vec![("bot-source", "protected-target")],
    )
    .await;

    service
        .chat(chat_command("protected-target"))
        .await
        .unwrap();
    let mut public_cmd = chat_command("public-target");
    public_cmd.run_id = Some("run-2".to_string());
    public_cmd.session_key = Some("session-2".to_string());
    service.chat(public_cmd).await.unwrap();

    assert_eq!(delivery.frames().await.len(), 2);
}
#[tokio::test]
async fn private_or_missing_target_is_not_found_before_delivery() {
    // Friends CAN chat with private bots; strangers and missing bots get 404
    let (service, _delivery, _) = build_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("private-target", "private", None),
        ],
        vec![("bot-source", "private-target")],
    )
    .await;

    // Friends can reach private bots
    assert!(
        service.chat(chat_command("private-target")).await.is_ok(),
        "friends should be able to chat with private bots"
    );
    // Missing bots still return 404
    assert!(matches!(
        service.chat(chat_command("missing-target")).await.unwrap_err(),
        ServiceError::BotNotFound(id) if id == "missing-target"
    ));
}
#[tokio::test]
async fn private_stranger_is_not_found_before_delivery() {
    // Strangers cannot reach private bots
    let (service, delivery, _) = build_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("private-target", "private", None),
        ],
        vec![], // no friendship
    )
    .await;
    assert!(matches!(
        service.chat(chat_command("private-target")).await.unwrap_err(),
        ServiceError::BotNotFound(id) if id == "private-target"
    ));
    assert!(delivery.frames().await.is_empty());
}
#[tokio::test]
async fn authenticated_staff_must_own_source_bot_when_present() {
    let (service, delivery, _) = build_service(
        vec![
            ("bot-source", "public", Some("owner-2")),
            ("bot-target", "public", None),
        ],
        vec![],
    )
    .await;

    let err = service.chat(chat_command("bot-target")).await.unwrap_err();

    assert!(matches!(err, ServiceError::Unauthorized(_)));
    assert!(delivery.frames().await.is_empty());
}
#[tokio::test]
async fn disconnected_target_maps_to_service_not_connected_error() {
    let bot_delivery = Arc::new(RecordingDelivery::new(false));
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    registry
        .insert("bot-source", "public", Some("owner-1"))
        .await;
    registry.insert("bot-target", "public", None).await;
    let service = A2aChat::new(
        bot_delivery.clone(),
        run_store,
        30_000,
        registry,
        Arc::new(StaticFriendCoreService::default()),
    )
    .with_bot_authority_hook(Some(staff_authority_default()));

    let err = service.chat(chat_command("bot-target")).await.unwrap_err();

    assert!(matches!(
        err,
        ServiceError::BotNotConnected(id) if id == "bot-target"
    ));
    assert!(bot_delivery.frames().await.is_empty());
}
