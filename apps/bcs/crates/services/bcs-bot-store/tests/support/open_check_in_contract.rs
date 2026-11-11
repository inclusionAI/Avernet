use bcs_service_api::{
    BotCapabilities, BotControlPlanePatch, BotControlPlaneRecord, BotControlPlaneRepoPort,
    BotRepoPort, FriendCheckInStrategy,
};
use serde_json::{Map, Value, json};

fn ext(value: Value) -> Map<String, Value> {
    serde_json::from_value(value).unwrap()
}

async fn seed(
    repo: &(impl BotRepoPort + BotControlPlaneRepoPort),
    id: &str,
    visibility: &str,
    strategy: FriendCheckInStrategy,
    scope: Value,
) {
    repo.register_with_owner_and_token(
        id.to_string(),
        BotCapabilities {
            name: Some(id.to_string()),
            ..Default::default()
        },
        "normalization-owner",
        &format!("token-{id}"),
    )
    .await
    .unwrap();
    repo.patch_control_plane(
        id,
        &bcs_config::resolve_env_str(),
        BotControlPlanePatch {
            visibility: Some(visibility.into()),
            friend_check_in_strategy: Some(strategy),
            friend_ext: Some(ext(scope)),
            ..Default::default()
        },
    )
    .await
    .unwrap()
    .unwrap();
}

async fn assert_state(
    repo: &(impl BotRepoPort + BotControlPlaneRepoPort),
    record: BotControlPlaneRecord,
    visibility: &str,
    strategy: FriendCheckInStrategy,
    scope: Value,
) {
    assert_eq!(record.visibility, visibility, "{}", record.bot_id);
    assert_eq!(
        record.friend_check_in_strategy, strategy,
        "{}",
        record.bot_id
    );
    assert_eq!(record.friend_ext, ext(scope), "{}", record.bot_id);
    assert_eq!(
        repo.try_get(&record.bot_id)
            .await
            .unwrap()
            .unwrap()
            .capabilities
            .visibility,
        visibility,
        "registry: {}",
        record.bot_id
    );
    assert_eq!(
        repo.get_control_plane(&record.bot_id, &bcs_config::resolve_env_str())
            .await
            .unwrap()
            .unwrap(),
        record
    );
}

/// The same effective-state matrix runs against Memory, SQLite and native MySQL.
/// Non-private means public only for OPEN + empty active scope, otherwise protected.
pub async fn verify(repo: &(impl BotRepoPort + BotControlPlaneRepoPort)) {
    let env = bcs_config::resolve_env_str();
    let scopes = [
        (json!({}), true),
        (json!({"view_scope_agent_friend_deps": null}), true),
        (json!({"view_scope_agent_friend_deps": []}), true),
        (
            json!({"view_scope_agent_friend_deps": [{"deptNo": "D1"}]}),
            false,
        ),
        (json!({"view_scope_agent_friend_deps": {}}), false),
        (json!({"view_scope_agent_friend_deps": "[]"}), false),
        (json!({"view_scope_agent_friend_deps": false}), false),
        (json!({"view_scope_agent_friend_deps": 0}), false),
    ];
    for (scope_index, (scope, empty)) in scopes.into_iter().enumerate() {
        for (strategy_index, strategy) in [
            FriendCheckInStrategy::Open,
            FriendCheckInStrategy::Approval,
            FriendCheckInStrategy::DeptFree,
        ]
        .into_iter()
        .enumerate()
        {
            for visibility in ["private", "protected", "public"] {
                let id = format!("normalize-{scope_index}-{strategy_index}-{visibility}");
                seed(
                    repo,
                    &id,
                    "private",
                    FriendCheckInStrategy::Approval,
                    json!({}),
                )
                .await;
                let record = repo
                    .patch_control_plane(
                        &id,
                        &env,
                        BotControlPlanePatch {
                            visibility: Some(visibility.into()),
                            friend_check_in_strategy: Some(strategy),
                            friend_ext: Some(ext(scope.clone())),
                            ..Default::default()
                        },
                    )
                    .await
                    .unwrap()
                    .unwrap();
                let expected = if visibility == "private" {
                    "private"
                } else if strategy == FriendCheckInStrategy::Open && empty {
                    "public"
                } else {
                    "protected"
                };
                assert_state(repo, record, expected, strategy, scope.clone()).await;
                // Repeating OPEN is not required: use the current strategy, including
                // MySQL unchanged-row behavior, to verify idempotence for each state.
                let repeated = repo
                    .patch_control_plane(
                        &id,
                        &env,
                        BotControlPlanePatch {
                            friend_check_in_strategy: Some(strategy),
                            ..Default::default()
                        },
                    )
                    .await
                    .unwrap()
                    .unwrap();
                assert_state(repo, repeated, expected, strategy, scope.clone()).await;
            }
        }
    }
    verify_public_only_request_means_all_visible(repo).await;
    verify_single_field_transitions(repo).await;
    verify_pending_approval_is_not_effective_scope(repo).await;
    // Legacy registry writes can leave a noncanonical row. A profile/user-only
    // patch must not act as an unrelated privacy migration.
    seed(repo, "unrelated-patch", "protected", FriendCheckInStrategy::Approval, json!({})).await;
    repo.update_visibility("unrelated-patch", "public").await.unwrap();
    let record = repo.patch_control_plane("unrelated-patch", &env, BotControlPlanePatch {
        name: Some("Renamed only".into()),
        user_visibility: Some(bcs_service_api::UserVisibility::Public),
        ..Default::default()
    }).await.unwrap().unwrap();
    assert_state(repo, record, "public", FriendCheckInStrategy::Approval, json!({})).await;
}

async fn verify_single_field_transitions(repo: &(impl BotRepoPort + BotControlPlaneRepoPort)) {
    use FriendCheckInStrategy::{Approval, DeptFree, Open};
    let scoped = json!({"view_scope_agent_friend_deps": ["D1"], "unrelated": {"keep": true}});
    let all = json!({"view_scope_agent_friend_deps": [], "unrelated": {"keep": true}});
    let cases = [
        (
            "strategy-open",
            "protected",
            Approval,
            all.clone(),
            BotControlPlanePatch {
                friend_check_in_strategy: Some(Open),
                ..Default::default()
            },
            "public",
            Open,
            all.clone(),
        ),
        (
            "strategy-approval",
            "public",
            Open,
            all.clone(),
            BotControlPlanePatch {
                friend_check_in_strategy: Some(Approval),
                ..Default::default()
            },
            "protected",
            Approval,
            all.clone(),
        ),
        (
            "strategy-dept-free",
            "public",
            Open,
            all.clone(),
            BotControlPlanePatch {
                friend_check_in_strategy: Some(DeptFree),
                ..Default::default()
            },
            "protected",
            DeptFree,
            all.clone(),
        ),
        (
            "visibility-public",
            "private",
            Open,
            all.clone(),
            BotControlPlanePatch {
                visibility: Some("public".into()),
                ..Default::default()
            },
            "public",
            Open,
            all.clone(),
        ),
        (
            "visibility-protected",
            "private",
            Open,
            all.clone(),
            BotControlPlanePatch {
                visibility: Some("protected".into()),
                ..Default::default()
            },
            "public",
            Open,
            all.clone(),
        ),
        (
            "public-needs-approval",
            "private",
            Approval,
            all.clone(),
            BotControlPlanePatch {
                visibility: Some("public".into()),
                ..Default::default()
            },
            "protected",
            Approval,
            all.clone(),
        ),
        (
            "protected-retains-scope",
            "protected",
            Open,
            scoped.clone(),
            BotControlPlanePatch {
                visibility: Some("protected".into()),
                ..Default::default()
            },
            "protected",
            Open,
            scoped.clone(),
        ),
        (
            "scope-cleared",
            "protected",
            Open,
            scoped.clone(),
            BotControlPlanePatch {
                friend_ext: Some(ext(all.clone())),
                ..Default::default()
            },
            "public",
            Open,
            all.clone(),
        ),
        (
            "scope-added",
            "public",
            Open,
            all.clone(),
            BotControlPlanePatch {
                friend_ext: Some(ext(scoped.clone())),
                ..Default::default()
            },
            "protected",
            Open,
            scoped.clone(),
        ),
        (
            "private-wins",
            "public",
            Open,
            all.clone(),
            BotControlPlanePatch {
                visibility: Some("private".into()),
                ..Default::default()
            },
            "private",
            Open,
            all.clone(),
        ),
        (
            "private-open",
            "private",
            Approval,
            all.clone(),
            BotControlPlanePatch {
                friend_check_in_strategy: Some(Open),
                ..Default::default()
            },
            "private",
            Open,
            all.clone(),
        ),
        (
            "private-clear-scope",
            "private",
            Open,
            scoped.clone(),
            BotControlPlanePatch {
                friend_ext: Some(ext(all.clone())),
                ..Default::default()
            },
            "private",
            Open,
            all,
        ),
    ];
    for (id, initial, strategy, scope, patch, expected, expected_strategy, expected_scope) in cases
    {
        seed(repo, id, initial, strategy, scope).await;
        let record = repo
            .patch_control_plane(id, &bcs_config::resolve_env_str(), patch)
            .await
            .unwrap()
            .unwrap();
        assert_state(repo, record, expected, expected_strategy, expected_scope).await;
    }
}

async fn verify_pending_approval_is_not_effective_scope(
    repo: &(impl BotRepoPort + BotControlPlaneRepoPort),
) {
    for (id, initial, scope, expected) in [
        ("pending-private", "private", json!({}), "private"),
        (
            "pending-scoped",
            "protected",
            json!({"view_scope_agent_friend_deps": ["D1"]}),
            "protected",
        ),
        ("pending-restriction", "public", json!({}), "public"),
    ] {
        seed(
            repo,
            id,
            initial,
            FriendCheckInStrategy::Open,
            scope.clone(),
        )
        .await;
        let mut next = ext(scope);
        next.insert("public_agent_approval".into(), json!({
            "puid": "pending-1", "status": "PROCESSING", "visibility": "public",
            "view_friend_deps": if id == "pending-restriction" { json!(["D2"]) } else { json!([]) }
        }));
        let record = repo
            .patch_control_plane(
                id,
                &bcs_config::resolve_env_str(),
                BotControlPlanePatch {
                    friend_ext: Some(next.clone()),
                    ..Default::default()
                },
            )
            .await
            .unwrap()
            .unwrap();
        assert_state(
            repo,
            record,
            expected,
            FriendCheckInStrategy::Open,
            Value::Object(next),
        )
        .await;
    }
}

/// Old external clients express all-visible with visibility alone. BCS owns
/// clearing just the active agent scope; unrelated data and draft approval stay.
async fn verify_public_only_request_means_all_visible(
    repo: &(impl BotRepoPort + BotControlPlaneRepoPort),
) {
    use FriendCheckInStrategy::{Approval, DeptFree, Open};
    let env = bcs_config::resolve_env_str();
    let scoped = json!({
        "view_scope_agent_friend_deps": ["D1"],
        "view_scope_user_friend_deps": ["U1"],
        "no_check_scope_friend_deps": ["N1"],
        "public_agent_approval": {"status": "PROCESSING", "puid": "draft-1", "view_friend_deps": ["D2"]},
        "unknown": {"view_scope_agent_friend_deps": ["nested-keep"]}
    });
    let mut cleared = scoped.clone();
    cleared["view_scope_agent_friend_deps"] = json!([]);
    for (index, strategy, expected) in [(0, Open, "public"), (1, Approval, "protected"), (2, DeptFree, "protected")] {
        let id = format!("public-only-{index}");
        seed(repo, &id, "protected", strategy, scoped.clone()).await;
        for _ in 0..2 {
            let record = repo.patch_control_plane(&id, &env, BotControlPlanePatch {
                visibility: Some("public".into()), ..Default::default()
            }).await.unwrap().unwrap();
            assert_state(repo, record, expected, strategy, cleared.clone()).await;
        }
    }
    seed(repo, "public-plus-open", "protected", Approval, scoped.clone()).await;
    let record = repo.patch_control_plane("public-plus-open", &env, BotControlPlanePatch {
        visibility: Some("public".into()), friend_check_in_strategy: Some(Open), ..Default::default()
    }).await.unwrap().unwrap();
    assert_state(repo, record, "public", Open, cleared).await;

    // Opening strategy alone is not a request to remove a restriction.
    seed(repo, "open-with-retained-scope", "protected", Approval, scoped.clone()).await;
    let record = repo.patch_control_plane("open-with-retained-scope", &env, BotControlPlanePatch {
        friend_check_in_strategy: Some(Open), ..Default::default()
    }).await.unwrap().unwrap();
    assert_state(repo, record, "protected", Open, scoped.clone()).await;

    // Explicit friend_ext has whole-object replacement semantics and wins over
    // the compatibility interpretation, even with explicit visibility=public.
    seed(repo, "public-with-explicit-scope", "private", Open, json!({})).await;
    let record = repo.patch_control_plane("public-with-explicit-scope", &env, BotControlPlanePatch {
        visibility: Some("public".into()), friend_ext: Some(ext(scoped.clone())), ..Default::default()
    }).await.unwrap().unwrap();
    assert_state(repo, record, "protected", Open, scoped.clone()).await;

    seed(repo, "private-retains-scope", "protected", Open, scoped.clone()).await;
    let record = repo.patch_control_plane("private-retains-scope", &env, BotControlPlanePatch {
        visibility: Some("private".into()), ..Default::default()
    }).await.unwrap().unwrap();
    assert_state(repo, record, "private", Open, scoped).await;
}
