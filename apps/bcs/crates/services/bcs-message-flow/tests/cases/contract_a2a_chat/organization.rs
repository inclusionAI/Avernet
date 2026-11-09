use super::*;

#[tokio::test]
async fn organization_scoped_a2a_allows_public_and_protected_without_friendship() {
    let service = build_organization_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("public-target", "public", None),
            ("protected-target", "protected", None),
        ],
        vec![],
        vec!["bot-source", "public-target", "protected-target"],
    )
    .await;

    let mut public_cmd = scoped_chat_command("public-target");
    public_cmd.run_id = Some("org-public-run".to_string());
    public_cmd.session_key = Some("org-public-session".to_string());
    assert!(service.chat(public_cmd).await.is_ok());
    let mut protected_cmd = scoped_chat_command("protected-target");
    protected_cmd.run_id = Some("org-protected-run".to_string());
    protected_cmd.session_key = Some("org-protected-session".to_string());
    assert!(service.chat(protected_cmd).await.is_ok());
}
#[tokio::test]
async fn organization_scoped_a2a_allows_private_friends_and_hides_private_strangers() {
    let friend_service = build_organization_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("private-friend", "private", None),
        ],
        vec![("bot-source", "private-friend")],
        vec!["bot-source", "private-friend"],
    )
    .await;
    assert!(friend_service.chat(scoped_chat_command("private-friend")).await.is_ok());

    let stranger_service = build_organization_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("private-stranger", "private", None),
        ],
        vec![],
        vec!["bot-source", "private-stranger"],
    )
    .await;
    assert!(matches!(
        stranger_service.chat(scoped_chat_command("private-stranger")).await.unwrap_err(),
        ServiceError::BotNotFound(id) if id == "private-stranger"
    ));
}
#[tokio::test]
async fn organization_scoped_a2a_rejects_sender_or_target_outside_effective_membership() {
    let sender_rejected = build_organization_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("bot-target", "public", None),
        ],
        vec![],
        vec!["bot-target"],
    )
    .await;
    assert!(matches!(
        sender_rejected.chat(scoped_chat_command("bot-target")).await.unwrap_err(),
        ServiceError::Forbidden(message) if message == "organization_member_required"
    ));

    let target_rejected = build_organization_service(
        vec![
            ("bot-source", "public", Some("owner-1")),
            ("bot-target", "public", None),
        ],
        vec![("bot-source", "bot-target")],
        vec!["bot-source"],
    )
    .await;
    assert!(matches!(
        target_rejected.chat(scoped_chat_command("bot-target")).await.unwrap_err(),
        ServiceError::Forbidden(message) if message == "organization_member_required"
    ));
}
#[tokio::test]
async fn organization_scoped_a2a_rejects_disabled_organization_before_friendship() {
    let bot_delivery = Arc::new(RecordingDelivery::new(true));
    let run_store = Arc::new(ChatRunStore::new());
    let registry = Arc::new(MemoryRegistry::default());
    registry.insert("bot-source", "public", Some("owner-1")).await;
    registry.insert("private-friend", "private", None).await;
    let friend = Arc::new(StaticFriendCoreService::new(vec![("bot-source", "private-friend")]));
    let organization = Arc::new(StaticOrganizationCoreService::new(vec!["bot-source", "private-friend"]).disabled());
    let service = A2aChat::new(bot_delivery, run_store, 30_000, registry, friend)
        .with_organization(organization)
        .with_bot_authority_hook(Some(
            staff_authority(&[("bot-source", "owner-1"), ("private-friend", "owner-1")]),
        ));

    assert!(matches!(
        service.chat(scoped_chat_command("private-friend")).await.unwrap_err(),
        ServiceError::Forbidden(message) if message == "organization_disabled"
    ));
}
