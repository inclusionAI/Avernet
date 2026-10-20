use super::*;

#[tokio::test]
async fn normal_creation_returns_and_persists_each_notify_mode() {
    for mode in [
        bcs_service_api::HumanMentionNotifyMode::All,
        bcs_service_api::HumanMentionNotifyMode::DriverBotOnly,
        bcs_service_api::HumanMentionNotifyMode::None,
    ] {
        let fixture = Fixture::new().await;
        fixture.add_public_bot("driver").await;
        let mut command = collaboration_create_command();
        let caller = authenticated_bot_principal("driver", "owner");
        command.caller = caller.clone();
        let CreateGroupSpec::Collaboration(input) = &mut command.group else {
            panic!("expected normal Group");
        };
        input.human_mention_notify_mode = mode;

        let created = fixture.service.create(command).await.expect("create Group");
        assert_eq!(detail_notify_mode(&created), mode);
        let group_id = detail_group_id(&created).to_string();
        let saved = fixture.groups.try_get(&group_id).await.unwrap().unwrap();
        assert_eq!(saved.human_mention_notify_mode, mode);
        let read = fixture.service.get(GetGroup { caller, group_id }).await.unwrap();
        assert_eq!(detail_notify_mode(&read), mode);
    }
}
