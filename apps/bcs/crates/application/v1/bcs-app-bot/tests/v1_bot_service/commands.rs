use super::*;

#[test]
fn v1_bot_commands_expose_the_approved_control_plane_surface() {
    let caller = human_caller("staff-1");

    let _ = ListBotCandidates {
        caller: caller.clone(),
        bot_id: "bot-1".to_string(),
        purpose: Default::default(),
        name: None,
        offset: 0,
        limit: 20,
    };
    let _ = SearchBotCandidates {
        caller: caller.clone(),
        bot_id: "bot-1".to_string(),
        purpose: Default::default(),
        query: Some("planning".to_string()),
    };
    let _ = QueryBots {
        caller: caller.clone(),
        bot_ids: vec!["bot-1".to_string()],
    };
    let _ = GetBot {
        caller: caller.clone(),
        bot_id: "bot-1".to_string(),
    };
    let _ = UpdateBot {
        caller: caller.clone(),
        bot_id: "bot-1".to_string(),
        patch: BotPatch {
            name: Some("Renamed".to_string()),
            visibility: Some(BotVisibility::Protected),
            status: Some(BotStatus::Online),
            descriptor: Some(BotDescriptorPatch {
                summary: Some("summary".to_string()),
                domains: None,
                skills: None,
                scopes: None,
            }),
            task_claim_mode: None,
            task_dream_mode: None,
            user_visibility: None,
            friend_ext: None,
            friend_check_in_strategy: None,
        },
    };
    let _ = ListMyBots {
        caller,
        kind: Some(BotKind::Bot),
        name: None,
        status: None,
        reachability: Some(BotReachability::Reachable),
        offset: 0,
        limit: 20,
    };

    let _assert_object_safe: fn(&dyn BotService) = |_| {};
}
