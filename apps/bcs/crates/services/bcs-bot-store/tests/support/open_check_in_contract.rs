use bcs_service_api::{
    BotCapabilities, BotControlPlanePatch, BotControlPlaneRepoPort, BotRepoPort,
    FriendCheckInStrategy,
};
use serde_json::{Map, Value, json};

fn ext(value: Value) -> Map<String, Value> {
    serde_json::from_value(value).unwrap()
}

/// The same cases run against Memory, SQLite and the native MySQL gate.
/// A strategy change is one write; omitted visibility is derived from the live
/// row regardless of organization scope, while explicit visibility always wins.
pub async fn verify(repo: &(impl BotRepoPort + BotControlPlaneRepoPort)) {
    let env = bcs_config::resolve_env_str();
    let scopes = [
        json!({}),
        json!({"view_scope_agent_friend_deps": null}),
        json!({"view_scope_agent_friend_deps": []}),
        json!({"view_scope_agent_friend_deps": [{"deptNo": "D1"}]}),
        json!({"view_scope_agent_friend_deps": {}}),
        json!({"view_scope_agent_friend_deps": "[]"}),
        json!({"view_scope_agent_friend_deps": false}),
        json!({"view_scope_agent_friend_deps": 0}),
    ];
    for (index, scope) in scopes.into_iter().enumerate() {
        for use_patch_scope in [false, true] {
            let id = format!("open-scope-{index}-{use_patch_scope}");
            repo.register_with_owner_and_token(
                id.clone(),
                BotCapabilities {
                    name: Some(id.clone()),
                    ..Default::default()
                },
                "open-check-in-owner",
                &format!("token-{id}"),
            )
            .await
            .unwrap();
            repo.patch_control_plane(
                &id,
                &env,
                BotControlPlanePatch {
                    visibility: Some("protected".into()),
                    // Neither persisted nor replacement scope gates promotion.
                    friend_ext: Some(ext(if use_patch_scope {
                        json!({"view_scope_agent_friend_deps": ["old-scope"]})
                    } else {
                        scope.clone()
                    })),
                    ..Default::default()
                },
            )
            .await
            .unwrap()
            .unwrap();
            let updated = repo
                .patch_control_plane(
                    &id,
                    &env,
                    BotControlPlanePatch {
                        friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
                        friend_ext: use_patch_scope.then(|| ext(scope.clone())),
                        ..Default::default()
                    },
                )
                .await
                .unwrap()
                .unwrap();
            assert_eq!(updated.visibility, "public", "{id}");
            assert_eq!(
                updated.friend_check_in_strategy,
                FriendCheckInStrategy::Open
            );
            assert_eq!(updated.friend_ext, ext(scope.clone()));
            let registered = repo.try_get(&id).await.unwrap().unwrap();
            assert_eq!(
                registered.capabilities.visibility, "public",
                "registry: {id}"
            );
            assert_eq!(
                repo.get_control_plane(&id, &env).await.unwrap().unwrap(),
                updated
            );
            let repeated = repo
                .patch_control_plane(
                    &id,
                    &env,
                    BotControlPlanePatch {
                        friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
                        ..Default::default()
                    },
                )
                .await
                .unwrap()
                .unwrap();
            assert_eq!(repeated.visibility, "public", "idempotent: {id}");
        }
    }

    for (index, (initial, explicit, strategy, expected)) in [
        (
            "private",
            None,
            Some(FriendCheckInStrategy::Open),
            "private",
        ),
        ("public", None, Some(FriendCheckInStrategy::Open), "public"),
        (
            "protected",
            Some("protected"),
            Some(FriendCheckInStrategy::Open),
            "protected",
        ),
        (
            "protected",
            Some("private"),
            Some(FriendCheckInStrategy::Open),
            "private",
        ),
        (
            "private",
            Some("public"),
            Some(FriendCheckInStrategy::Open),
            "public",
        ),
        (
            "protected",
            None,
            Some(FriendCheckInStrategy::Approval),
            "protected",
        ),
        (
            "protected",
            None,
            Some(FriendCheckInStrategy::DeptFree),
            "protected",
        ),
        ("protected", None, None, "protected"),
        (
            "public",
            None,
            Some(FriendCheckInStrategy::Approval),
            "public",
        ),
        (
            "public",
            None,
            Some(FriendCheckInStrategy::DeptFree),
            "public",
        ),
    ]
    .into_iter()
    .enumerate()
    {
        let id = format!("open-visibility-{index}");
        repo.register_with_owner_and_token(
            id.clone(),
            BotCapabilities {
                name: Some(id.clone()),
                visibility: initial.into(),
                ..Default::default()
            },
            "open-check-in-owner",
            &format!("token-{id}"),
        )
        .await
        .unwrap();
        repo.patch_control_plane(
            &id,
            &env,
            BotControlPlanePatch {
                visibility: Some(initial.into()),
                ..Default::default()
            },
        )
        .await
        .unwrap()
        .unwrap();
        let updated = repo
            .patch_control_plane(
                &id,
                &env,
                BotControlPlanePatch {
                    visibility: explicit.map(str::to_owned),
                    friend_check_in_strategy: strategy,
                    friend_ext: Some(Map::new()),
                    ..Default::default()
                },
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(updated.visibility, expected, "{id}");
        assert_eq!(
            updated.friend_check_in_strategy,
            strategy.unwrap_or_default()
        );
    }
}
