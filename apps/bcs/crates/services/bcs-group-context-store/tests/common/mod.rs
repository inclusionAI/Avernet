use bcs_domain::{GroupContextOwners, GroupContextPolicy, ScopeLevel};
use bcs_group_context_store::{GroupContextRepo, GroupContextStoreError, NewGroupContextEntry};
use chrono::{TimeZone, Utc};

pub fn entry(context_id: &str, name: &str, scope: &str, content: &str) -> NewGroupContextEntry {
    let now = Utc
        .timestamp_millis_opt(1_728_374_400_000)
        .single()
        .unwrap();
    NewGroupContextEntry {
        context_id: context_id.into(),
        name: name.into(),
        scope_level: ScopeLevel::Session,
        scope: scope.into(),
        creator: "judge_bot".into(),
        content: content.into(),
        visible_to: GroupContextPolicy::users(["judge_bot"]),
        collect_from: GroupContextPolicy::users(["judge_bot"]),
        owners: GroupContextOwners::new(["judge_bot"]),
        supersedes: None,
        change_reason: None,
        valid_from: now,
        valid_to: None,
        tx_time: now,
    }
}

pub async fn create_duplicate_active_conflicts(repo: &dyn GroupContextRepo) {
    repo.create_active(entry("ctx-1", "player_state", "sess-001", "alive"))
        .await
        .expect("create active");
    let err = repo
        .create_active(entry("ctx-2", "player_state", "sess-001", "duplicate"))
        .await
        .expect_err("duplicate active must conflict");
    assert!(matches!(
        err,
        GroupContextStoreError::DuplicateActive { .. }
    ));
}

pub async fn supersede_preserves_history(repo: &dyn GroupContextRepo) {
    let first = repo
        .create_active(entry("ctx-1", "player_state", "sess-001", "张三存活"))
        .await
        .expect("create active");
    let second = repo
        .supersede_active(
            "player_state",
            "sess-001",
            first.id, // CAS token: must match the active row's id
            NewGroupContextEntry {
                context_id: first.context_id.clone(), // preserve context_id across versions
                name: "player_state".into(),
                scope_level: ScopeLevel::Session,
                scope: "sess-001".into(),
                creator: "judge_bot".into(),
                content: "张三出局".into(),
                visible_to: GroupContextPolicy::users(Vec::<String>::new()),
                collect_from: GroupContextPolicy::users(["judge_bot"]),
                owners: GroupContextOwners::new(["judge_bot"]),
                supersedes: Some(first.id),
                change_reason: Some("第一轮投票".into()),
                valid_from: Utc
                    .timestamp_millis_opt(1_728_374_460_000)
                    .single()
                    .unwrap(),
                valid_to: None,
                tx_time: Utc
                    .timestamp_millis_opt(1_728_374_460_000)
                    .single()
                    .unwrap(),
            },
        )
        .await
        .expect("supersede active");

    assert_eq!(second.context_id, first.context_id);
    assert_eq!(second.supersedes, Some(first.id));
    assert_eq!(second.change_reason.as_deref(), Some("第一轮投票"));
    assert_eq!(second.content, "张三出局");

    let active = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("get active")
        .expect("active row");
    assert_eq!(active.id, second.id);
    assert!(!active.is_superseded);
    assert!(active.gmt_superseded.is_none());

    let versions = repo
        .list_versions(&first.context_id)
        .await
        .expect("list versions");
    assert_eq!(versions.len(), 2);
    assert!(versions.iter().any(|row| row.id == first.id
        && row.is_superseded
        && row.gmt_superseded == Some(second.tx_time)
        && row.valid_to.is_none()));
    assert!(versions
        .iter()
        .any(|row| row.id == second.id && !row.is_superseded && row.gmt_superseded.is_none()));
}

pub async fn supersede_preserves_multiple_history_versions(repo: &dyn GroupContextRepo) {
    let first = repo
        .create_active(entry("ctx-chain", "version_chain", "sess-chain", "v1"))
        .await
        .expect("create v1");

    let second = repo
        .supersede_active(
            "version_chain",
            "sess-chain",
            first.id,
            NewGroupContextEntry {
                context_id: first.context_id.clone(),
                name: "version_chain".into(),
                scope_level: ScopeLevel::Session,
                scope: "sess-chain".into(),
                creator: "judge_bot".into(),
                content: "v2".into(),
                visible_to: GroupContextPolicy::users(["judge_bot"]),
                collect_from: GroupContextPolicy::users(["judge_bot"]),
                owners: GroupContextOwners::new(["judge_bot"]),
                supersedes: Some(first.id),
                change_reason: Some("first update".into()),
                valid_from: Utc
                    .timestamp_millis_opt(1_728_374_460_000)
                    .single()
                    .unwrap(),
                valid_to: None,
                tx_time: Utc
                    .timestamp_millis_opt(1_728_374_460_000)
                    .single()
                    .unwrap(),
            },
        )
        .await
        .expect("supersede v1 -> v2 succeeds");

    let third = repo
        .supersede_active(
            "version_chain",
            "sess-chain",
            second.id,
            NewGroupContextEntry {
                context_id: first.context_id.clone(),
                name: "version_chain".into(),
                scope_level: ScopeLevel::Session,
                scope: "sess-chain".into(),
                creator: "judge_bot".into(),
                content: "v3".into(),
                visible_to: GroupContextPolicy::users(["judge_bot"]),
                collect_from: GroupContextPolicy::users(["judge_bot"]),
                owners: GroupContextOwners::new(["judge_bot"]),
                supersedes: Some(second.id),
                change_reason: Some("second update".into()),
                valid_from: Utc
                    .timestamp_millis_opt(1_728_374_520_000)
                    .single()
                    .unwrap(),
                valid_to: None,
                tx_time: Utc
                    .timestamp_millis_opt(1_728_374_520_000)
                    .single()
                    .unwrap(),
            },
        )
        .await
        .expect("supersede v2 -> v3 succeeds");

    let active = repo
        .get_active("version_chain", "sess-chain")
        .await
        .expect("get active")
        .expect("active row");
    assert_eq!(active.id, third.id);
    assert_eq!(active.content, "v3");
    assert!(!active.is_superseded);

    let versions = repo
        .list_versions(&first.context_id)
        .await
        .expect("list versions");
    assert_eq!(versions.len(), 3);
    assert!(versions
        .iter()
        .any(|row| row.id == first.id && row.is_superseded && row.supersedes.is_none()));
    assert!(versions
        .iter()
        .any(|row| row.id == second.id && row.is_superseded && row.supersedes == Some(first.id)));
    assert!(versions
        .iter()
        .any(|row| row.id == third.id && !row.is_superseded && row.supersedes == Some(second.id)));
}

pub async fn supersede_stale_id_returns_conflict(repo: &dyn GroupContextRepo) {
    // Simulates F2 race condition: two concurrent writers read the same active row.
    // Writer A's CAS succeeds, Writer B's CAS fails because the expected id is stale.
    let first = repo
        .create_active(entry("ctx-cas", "cas_test", "sess-001", "v1"))
        .await
        .expect("create v1");

    let v2_entry = NewGroupContextEntry {
        context_id: first.context_id.clone(),
        name: "cas_test".into(),
        scope_level: ScopeLevel::Session,
        scope: "sess-001".into(),
        creator: "judge_bot".into(),
        content: "v2".into(),
        visible_to: GroupContextPolicy::users(["judge_bot"]),
        collect_from: GroupContextPolicy::users(["judge_bot"]),
        owners: GroupContextOwners::new(["judge_bot"]),
        supersedes: Some(first.id),
        change_reason: Some("CAS v2".into()),
        valid_from: Utc
            .timestamp_millis_opt(1_728_374_460_000)
            .single()
            .unwrap(),
        valid_to: None,
        tx_time: Utc
            .timestamp_millis_opt(1_728_374_460_000)
            .single()
            .unwrap(),
    };

    // Writer A: CAS with first.id succeeds
    let _v2 = repo
        .supersede_active("cas_test", "sess-001", first.id, v2_entry)
        .await
        .expect("supersede v1 -> v2 succeeds");

    // Now v2 is the active row. Writer B tries to supersede using stale first.id.
    // The CAS UPDATE WHERE id=first.id AND is_superseded=FALSE affects 0 rows -> Conflict.
    let stale_entry = NewGroupContextEntry {
        context_id: first.context_id.clone(),
        name: "cas_test".into(),
        scope_level: ScopeLevel::Session,
        scope: "sess-001".into(),
        creator: "judge_bot".into(),
        content: "stale-v3".into(),
        visible_to: GroupContextPolicy::users(["judge_bot"]),
        collect_from: GroupContextPolicy::users(["judge_bot"]),
        owners: GroupContextOwners::new(["judge_bot"]),
        supersedes: Some(first.id),
        change_reason: Some("stale CAS".into()),
        valid_from: Utc
            .timestamp_millis_opt(1_728_374_520_000)
            .single()
            .unwrap(),
        valid_to: None,
        tx_time: Utc
            .timestamp_millis_opt(1_728_374_520_000)
            .single()
            .unwrap(),
    };

    let err = repo
        .supersede_active("cas_test", "sess-001", first.id, stale_entry)
        .await
        .expect_err("stale CAS token must return Conflict");
    assert!(matches!(err, GroupContextStoreError::Conflict { .. }));

    // Active row should still be v2, not overwritten by stale data.
    let active = repo
        .get_active("cas_test", "sess-001")
        .await
        .expect("get active")
        .expect("active row");
    assert_eq!(active.content, "v2");
}

pub async fn creator_is_persisted_for_each_version(repo: &dyn GroupContextRepo) {
    let mut first_params = entry("ctx-creators", "creator_audit", "sess-creators", "v1");
    first_params.creator = "writer-a".into();
    let first = repo.create_active(first_params).await.expect("create v1");

    let mut second_params = entry("ctx-creators", "creator_audit", "sess-creators", "v2");
    second_params.creator = "writer-b".into();
    second_params.supersedes = Some(first.id);
    let second = repo
        .supersede_active("creator_audit", "sess-creators", first.id, second_params)
        .await
        .expect("supersede v1 -> v2");
    assert_eq!(second.creator, "writer-b");

    let versions = repo
        .list_versions(&first.context_id)
        .await
        .expect("list versions");
    assert_eq!(
        versions
            .iter()
            .map(|entry| entry.creator.as_str())
            .collect::<Vec<_>>(),
        vec!["writer-a", "writer-b"]
    );
}

pub async fn owners_are_persisted_per_version(repo: &dyn GroupContextRepo) {
    let mut first_params = entry("ctx-owners", "owner_history", "sess-owners", "v1");
    first_params.creator = "writer-a".into();
    first_params.owners = GroupContextOwners::new(["writer-a"]);
    let first = repo.create_active(first_params).await.expect("create v1");

    let mut second_params = entry("ctx-owners", "owner_history", "sess-owners", "v2");
    second_params.creator = "writer-b".into();
    second_params.owners = GroupContextOwners::new(["delegate_bot"]);
    second_params.supersedes = Some(first.id);
    let second = repo
        .supersede_active("owner_history", "sess-owners", first.id, second_params)
        .await
        .expect("supersede v1 while changing owners");
    assert_eq!(second.owners, GroupContextOwners::new(["delegate_bot"]));

    let versions = repo
        .list_versions(&first.context_id)
        .await
        .expect("list owners history");
    assert_eq!(
        versions
            .iter()
            .map(|entry| (entry.id, entry.creator.as_str(), entry.owners.user_ids.clone()))
            .collect::<Vec<_>>(),
        vec![
            (first.id, "writer-a", vec!["writer-a".to_string()]),
            (second.id, "writer-b", vec!["delegate_bot".to_string()]),
        ]
    );
}

pub async fn list_active_by_multiple_scopes(repo: &dyn GroupContextRepo) {
    repo.create_active(entry("ctx-g", "game_rule", "game-room-7", "规则"))
        .await
        .unwrap();
    repo.create_active(entry("ctx-s", "player_state", "sess-001", "状态"))
        .await
        .unwrap();
    repo.create_active(entry("ctx-other", "other", "sess-002", "其他"))
        .await
        .unwrap();

    let scopes = vec!["game-room-7".to_string(), "sess-001".to_string()];
    let rows = repo.list_active_meta_by_scopes(&scopes).await.unwrap();
    assert!(rows.iter().all(|row| row.owners.contains("judge_bot")));
    let names = rows.into_iter().map(|row| row.name).collect::<Vec<_>>();
    assert_eq!(names.len(), 2);
    assert!(names.iter().any(|name| name == "game_rule"));
    assert!(names.iter().any(|name| name == "player_state"));
}

pub async fn current_row_with_expired_business_validity_still_blocks_and_is_returned(
    repo: &dyn GroupContextRepo,
) {
    let now = Utc
        .timestamp_millis_opt(1_728_374_400_000)
        .single()
        .unwrap();
    let expired = Utc
        .timestamp_millis_opt(1_700_000_000_000)
        .single()
        .unwrap();
    let mut current = entry(
        "ctx-expired",
        "business_window",
        "sess-001",
        "expired-but-current",
    );
    current.valid_from = expired;
    current.valid_to = Some(expired);
    current.tx_time = now;

    let created = repo.create_active(current).await.expect("create current");
    assert!(!created.is_superseded);
    assert_eq!(created.valid_to, Some(expired));

    let duplicate = repo
        .create_active(entry(
            "ctx-dupe",
            "business_window",
            "sess-001",
            "duplicate",
        ))
        .await
        .expect_err("current row with expired business valid_to still blocks duplicate");
    assert!(matches!(
        duplicate,
        GroupContextStoreError::DuplicateActive { .. }
    ));

    let active = repo
        .get_active("business_window", "sess-001")
        .await
        .expect("get active")
        .expect("active row");
    assert_eq!(active.id, created.id);
    assert_eq!(active.valid_to, Some(expired));

    let rows = repo
        .list_active_meta_by_scopes(&["sess-001".to_string()])
        .await
        .expect("list active");
    assert!(rows.iter().any(|row| row.name == created.name));
}
