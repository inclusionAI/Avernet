use super::*;

#[tokio::test]
async fn add_defaults_creator_owner_and_ignores_body_owners() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());
    let command: AddGroupContext = serde_json::from_value(json!({
        "actor_id": "judge_bot",
        "request_context": {
            "group_id": "game-room-7",
            "session_id": "sess-001",
            "round_id": "round-003",
        },
        "name": "player_state",
        "scope_level": "session",
        "content": "v1",
        "visible_to": {"userIds": []},
        "collect_from": {"userIds": ["judge_bot"]},
        "owners": {"userIds": ["attacker"]}
    }))
    .expect("unknown add body field is ignored by serde");

    app.add(command).await.expect("add succeeds");

    let active = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(active.creator, "judge_bot");
    assert_eq!(active.owners, owners(["judge_bot"]));
}

#[tokio::test]
async fn owner_can_update_all_fields_without_collect_from_membership() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());
    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: "v1".into(),
        visible_to: policy(&["张三"]),
        collect_from: Some(policy(&["judge_bot"])),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("add succeeds");

    app.update(UpdateGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: Some("v2".into()),
        change_reason: None,
        visible_to: None,
        collect_from: Some(policy(&["other_bot"])),
        owners: None,
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("owner removes self from collect_from");

    let valid_from = Utc.with_ymd_and_hms(2026, 10, 19, 8, 0, 0).unwrap();
    let valid_to = Utc.with_ymd_and_hms(2026, 10, 20, 8, 0, 0).unwrap();
    app.update(UpdateGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: Some("v3".into()),
        change_reason: Some("owner updates full row".into()),
        visible_to: Some(policy(&["张三", "李四"])),
        collect_from: Some(policy(&["other_bot"])),
        owners: Some(owners(["judge_bot", "admin"])),
        valid_from: Some(valid_from),
        valid_to: Some(valid_to),
    })
    .await
    .expect("owner is not required to be in collect_from");

    let active = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(active.content, "v3");
    assert_eq!(active.visible_to, policy(&["张三", "李四"]));
    assert_eq!(active.collect_from, policy(&["other_bot"]));
    assert_eq!(active.owners, owners(["judge_bot", "admin"]));
    assert_eq!(active.valid_from, valid_from);
    assert_eq!(active.valid_to, Some(valid_to));
}

#[tokio::test]
async fn w_only_non_owner_updates_content_and_validity_but_not_management_fields() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());
    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: "v1".into(),
        visible_to: policy(&["judge_bot"]),
        collect_from: Some(policy(&["judge_bot", "other_bot"])),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("add succeeds");

    let valid_from = Utc.with_ymd_and_hms(2026, 10, 19, 8, 0, 0).unwrap();
    let valid_to = Utc.with_ymd_and_hms(2026, 10, 20, 8, 0, 0).unwrap();
    app.update(UpdateGroupContext {
        actor_id: "other_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: Some("w-only update".into()),
        change_reason: Some("normal writer".into()),
        visible_to: None,
        collect_from: None,
        owners: None,
        valid_from: Some(valid_from),
        valid_to: Some(valid_to),
    })
    .await
    .expect("W-only actor keeps content and validity access");
    let active = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(active.content, "w-only update");
    assert_eq!(active.owners, owners(["judge_bot"]));

    for update in [
        UpdateGroupContext {
            actor_id: "other_bot".into(),
            request_context: ctx(),
            name: "player_state".into(),
            scope_level: ScopeLevel::Session,
            content: None,
            change_reason: Some("change reason is only audit metadata".into()),
            visible_to: Some(policy(&["other_bot"])),
            collect_from: None,
            owners: None,
            valid_from: None,
            valid_to: None,
        },
        UpdateGroupContext {
            actor_id: "other_bot".into(),
            request_context: ctx(),
            name: "player_state".into(),
            scope_level: ScopeLevel::Session,
            content: None,
            change_reason: Some("change reason is only audit metadata".into()),
            visible_to: None,
            collect_from: Some(policy(&["other_bot"])),
            owners: None,
            valid_from: None,
            valid_to: None,
        },
        UpdateGroupContext {
            actor_id: "other_bot".into(),
            request_context: ctx(),
            name: "player_state".into(),
            scope_level: ScopeLevel::Session,
            content: None,
            change_reason: Some("change reason cannot bypass owner field policy".into()),
            visible_to: None,
            collect_from: None,
            owners: Some(owners(["other_bot"])),
            valid_from: None,
            valid_to: None,
        },
    ] {
        let error = app
            .update(update)
            .await
            .expect_err("W-only actor cannot modify management fields");
        assert_code(error, "permission_denied");
    }

    let active = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(active.visible_to, policy(&["judge_bot"]));
    assert_eq!(active.collect_from, policy(&["judge_bot", "other_bot"]));
    assert_eq!(active.owners, owners(["judge_bot"]));
}

#[tokio::test]
async fn ownership_transfer_takes_effect_immediately() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());
    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: "v1".into(),
        visible_to: policy(&[]),
        collect_from: Some(policy(&["judge_bot"])),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("add succeeds");

    app.update(UpdateGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: Some("v2".into()),
        change_reason: Some("transfer ownership".into()),
        visible_to: None,
        collect_from: Some(policy(&["judge_bot"])),
        owners: Some(owners(["admin"])),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("current owner transfers ownership");

    let transferred = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("store ok")
        .expect("transferred active row");
    assert_eq!(transferred.owners, owners(["admin"]));
    assert!(transferred.owners.contains("admin"));
    assert!(!transferred.owners.contains("judge_bot"));

    app.update(UpdateGroupContext {
        actor_id: "admin".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: Some("v3 by owner outside collect_from".into()),
        change_reason: None,
        visible_to: None,
        collect_from: Some(policy(&["judge_bot"])),
        owners: Some(owners(["admin"])),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("new owner is active immediately");

    let error = app
        .update(UpdateGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: "player_state".into(),
            scope_level: ScopeLevel::Session,
            content: None,
            change_reason: None,
            visible_to: Some(policy(&["judge_bot"])),
            collect_from: None,
            owners: None,
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("old owner immediately loses management rights");
    assert_code(error, "permission_denied");

    app.update(UpdateGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: Some("v4 by retained normal writer".into()),
        change_reason: None,
        visible_to: None,
        collect_from: None,
        owners: None,
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("old owner retains ordinary W policy only");

    let active = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(active.content, "v4 by retained normal writer");
    assert_eq!(active.owners, owners(["admin"]));
}

#[tokio::test]
async fn update_validates_requested_owners() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());
    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: "v1".into(),
        visible_to: policy(&[]),
        collect_from: Some(policy(&["judge_bot"])),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("add succeeds");

    for (owners, expected_code) in [
        (
            GroupContextOwners::new(Vec::<String>::new()),
            "invalid_param",
        ),
        (owners(["   "]), "invalid_param"),
        (owners(["a".repeat(256)]), "payload_too_large"),
        (
            GroupContextOwners::new((0..240).map(|index| format!("owner-{index:03}-99999999"))),
            "payload_too_large",
        ),
    ] {
        let error = app
            .update(UpdateGroupContext {
                actor_id: "judge_bot".into(),
                request_context: ctx(),
                name: "player_state".into(),
                scope_level: ScopeLevel::Session,
                content: None,
                change_reason: None,
                visible_to: None,
                collect_from: None,
                owners: Some(owners),
                valid_from: None,
                valid_to: None,
            })
            .await
            .expect_err("invalid owners rejected");
        assert_code(error, expected_code);
    }

    let active = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(active.owners, owners(["judge_bot"]));
}

#[tokio::test]
async fn update_and_systemset_preserve_legacy_empty_owners() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());
    let now = Utc
        .timestamp_millis_opt(1_728_374_400_000)
        .single()
        .unwrap();
    repo.create_active(NewGroupContextEntry {
        is_slot: false,
        context_id: "ctx-legacy-empty-owners".into(),
        name: "legacy_empty_owners".into(),
        scope_level: ScopeLevel::Session,
        scope: "sess-001".into(),
        content: "v1".into(),
        creator: "writer-a".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["writer-a"]),
        owners: owners(Vec::<String>::new()),
        supersedes: None,
        change_reason: None,
        valid_from: now,
        valid_to: None,
        tx_time: now,
    })
    .await
    .expect("seed migrated row with NULL owners");

    let before_update = app
        .list(ListGroupContexts {
            actor_id: "writer-a".into(),
            request_context: ctx(),
            scope_levels: Some(vec![ScopeLevel::Session]),
        })
        .await
        .expect("list legacy empty owners");
    assert_eq!(before_update.contexts.len(), 1);
    assert_eq!(
        before_update.contexts[0].permission,
        GroupContextPermission::WR
    );

    app.update(UpdateGroupContext {
        actor_id: "writer-a".into(),
        request_context: ctx(),
        name: "legacy_empty_owners".into(),
        scope_level: ScopeLevel::Session,
        content: Some("v2".into()),
        change_reason: None,
        visible_to: None,
        collect_from: None,
        owners: None,
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("update preserves legacy empty owners");

    let after_update = repo
        .get_active("legacy_empty_owners", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(after_update.content, "v2");
    assert_eq!(after_update.owners, owners(Vec::<String>::new()));

    app.systemset(SystemSetGroupContext {
        request_context: ctx(),
        name: "legacy_empty_owners".into(),
        scope_level: ScopeLevel::Session,
        content: "v3".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["writer-a"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("systemset preserves legacy empty owners");

    let after_systemset = repo
        .get_active("legacy_empty_owners", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(after_systemset.content, "v3");
    assert_eq!(after_systemset.owners, owners(Vec::<String>::new()));
}

#[tokio::test]
async fn systemset_preserves_existing_owners_and_defaults_new_owner_to_system() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());

    app.systemset(SystemSetGroupContext {
        request_context: ctx(),
        name: "system_rule".into(),
        scope_level: ScopeLevel::Group,
        content: "system v1".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["admin"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("system create succeeds");
    let first = repo
        .get_active("system_rule", "game-room-7")
        .await
        .expect("store ok")
        .expect("first active row");
    assert_eq!(first.owners, owners(["system"]));

    app.systemset(SystemSetGroupContext {
        request_context: ctx(),
        name: "system_rule".into(),
        scope_level: ScopeLevel::Group,
        content: "system v2".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["admin"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("systemset does not require system to be in collect_from");
    let second = repo
        .get_active("system_rule", "game-room-7")
        .await
        .expect("store ok")
        .expect("second active row");
    assert_eq!(second.owners, owners(["system"]));

    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "owned_rule".into(),
        scope_level: ScopeLevel::Group,
        content: "human v1".into(),
        visible_to: policy(&[]),
        collect_from: Some(policy(&["judge_bot"])),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("add succeeds");

    app.systemset(SystemSetGroupContext {
        request_context: ctx(),
        name: "owned_rule".into(),
        scope_level: ScopeLevel::Group,
        content: "human v2 by system".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["judge_bot"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("systemset bypasses owner and collect_from authorization");

    let active = repo
        .get_active("owned_rule", "game-room-7")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(active.content, "human v2 by system");
    assert_eq!(active.owners, owners(["judge_bot"]));
}

#[tokio::test]
async fn systemset_update_or_create_without_actor_membership() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());

    app.systemset(SystemSetGroupContext {
        request_context: ctx(),
        name: "game_rule".into(),
        scope_level: ScopeLevel::Group,
        content: "规则 v1".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["admin"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("system create succeeds without actor");

    app.systemset(SystemSetGroupContext {
        request_context: ctx(),
        name: "game_rule".into(),
        scope_level: ScopeLevel::Group,
        content: "规则 v2".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["admin"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("system supersede succeeds");

    let active = repo
        .get_active("game_rule", "game-room-7")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(active.content, "规则 v2");
    let versions = repo
        .list_versions(&active.context_id)
        .await
        .expect("versions ok");
    assert_eq!(versions.len(), 2);
}

#[tokio::test]
async fn systemset_defaults_validity_on_create_and_supersede() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());
    let explicit_from = Utc.with_ymd_and_hms(2026, 10, 12, 8, 0, 0).unwrap();
    let explicit_to = Utc.with_ymd_and_hms(2026, 10, 12, 9, 0, 0).unwrap();

    app.systemset(SystemSetGroupContext {
        request_context: ctx(),
        name: "system_validity".into(),
        scope_level: ScopeLevel::Group,
        content: "规则 v1".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["admin"]),
        valid_from: Some(explicit_from),
        valid_to: Some(explicit_to),
    })
    .await
    .expect("system create succeeds");
    let before_supersede = Utc::now();

    app.systemset(SystemSetGroupContext {
        request_context: ctx(),
        name: "system_validity".into(),
        scope_level: ScopeLevel::Group,
        content: "规则 v2".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["admin"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("system supersede succeeds");

    let active = repo
        .get_active("system_validity", "game-room-7")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(active.content, "规则 v2");
    assert!(active.valid_from >= before_supersede);
    assert!(active.valid_from <= Utc::now());
    assert_eq!(active.valid_to, None);
}

#[tokio::test]
async fn list_filters_visible_or_writable_and_labels_permissions() {
    let app = service(Arc::new(MemoryGroupContextRepo::new()));
    for (name, visible_to, collect_from) in [
        ("read_only", policy(&["张三"]), policy(&["judge_bot"])),
        ("write_only", policy(&["李四"]), policy(&["张三"])),
        ("read_write", policy(&["张三"]), policy(&["张三"])),
        ("hidden", policy(&["李四"]), policy(&["judge_bot"])),
    ] {
        app.systemset(SystemSetGroupContext {
            request_context: ctx(),
            name: name.into(),
            scope_level: ScopeLevel::Session,
            content: name.into(),
            visible_to,
            collect_from,
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect("seed succeeds");
    }

    let response = app
        .list(ListGroupContexts {
            actor_id: "张三".into(),
            request_context: ctx(),
            scope_levels: Some(vec![ScopeLevel::Session]),
        })
        .await
        .expect("list succeeds");
    let mut names = response
        .contexts
        .iter()
        .map(|item| (item.name.as_str(), item.permission))
        .collect::<Vec<_>>();
    names.sort_by_key(|(name, _)| *name);
    assert_eq!(
        names,
        vec![
            ("read_only", GroupContextPermission::R),
            ("read_write", GroupContextPermission::WR),
            ("write_only", GroupContextPermission::W),
        ]
    );
}

#[tokio::test]
async fn owner_list_and_get_bypass_policies_and_get_exposes_policy_to_owner_only() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());
    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: "owner-managed state".into(),
        visible_to: policy(&["张三"]),
        collect_from: Some(policy(&["judge_bot"])),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("add succeeds");
    app.update(UpdateGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: Some("owner-managed state".into()),
        change_reason: None,
        visible_to: Some(policy(&["张三"])),
        collect_from: Some(policy(&["张三"])),
        owners: None,
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("owner updates policy without being listed in it");

    let owner_list = app
        .list(ListGroupContexts {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            scope_levels: Some(vec![ScopeLevel::Session]),
        })
        .await
        .expect("owner list succeeds");
    let owner_context = owner_list
        .contexts
        .iter()
        .find(|item| item.name == "player_state")
        .expect("owner is not filtered by visible_to or collect_from");
    assert_eq!(owner_context.permission, GroupContextPermission::WRO);
    let encoded_list = serde_json::to_value(&owner_list).expect("list response serializes");
    assert!(!encoded_list.to_string().contains("\"owners\""));

    let owner_get = app
        .get(GetGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: "player_state".into(),
            scope_levels: Some(vec![ScopeLevel::Session]),
            limit: None,
        })
        .await
        .expect("owner get bypasses visible_to");
    assert_eq!(owner_get.items.len(), 1);
    assert_eq!(owner_get.items[0].content, "owner-managed state");
    assert_eq!(owner_get.items[0].visible_to, Some(policy(&["张三"])));
    assert_eq!(owner_get.items[0].collect_from, Some(policy(&["张三"])));
    assert_eq!(owner_get.items[0].owners, Some(owners(["judge_bot"])));
    let encoded_owner_get = serde_json::to_value(&owner_get).expect("owner get serializes");
    assert_eq!(
        encoded_owner_get["items"][0]["owners"],
        serde_json::json!({"userIds": ["judge_bot"]})
    );

    let reader_get = app
        .get(GetGroupContext {
            actor_id: "张三".into(),
            request_context: ctx(),
            name: "player_state".into(),
            scope_levels: Some(vec![ScopeLevel::Session]),
            limit: None,
        })
        .await
        .expect("ordinary reader succeeds");
    assert_eq!(reader_get.items.len(), 1);
    assert_eq!(reader_get.items[0].visible_to, None);
    assert_eq!(reader_get.items[0].collect_from, None);
    assert_eq!(reader_get.items[0].owners, None);
    let encoded_reader_get = serde_json::to_value(&reader_get).expect("reader get serializes");
    assert!(encoded_reader_get.to_string().find("\"owners\"").is_none());
}

#[tokio::test]
async fn get_without_scope_levels_queries_only_deepest_supplied_scope() {
    let app = service(Arc::new(MemoryGroupContextRepo::new()));
    for (scope_level, content) in [
        (ScopeLevel::Group, "group"),
        (ScopeLevel::Session, "session"),
        (ScopeLevel::Round, "round"),
    ] {
        app.systemset(SystemSetGroupContext {
            request_context: ctx(),
            name: "game_state".into(),
            scope_level,
            content: content.into(),
            visible_to: policy(&["张三"]),
            collect_from: policy(&["judge_bot"]),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect("seed visible row");
    }

    let group_only = app
        .get(GetGroupContext {
            actor_id: "张三".into(),
            request_context: GroupContextRequestContext {
                group_id: Some("game-room-7".into()),
                session_id: None,
                round_id: None,
            },
            name: "game_state".into(),
            scope_levels: None,
            limit: None,
        })
        .await
        .expect("group-only get succeeds");
    assert_eq!(group_only.items.len(), 1);
    assert_eq!(group_only.items[0].content, "group");

    let session_only = app
        .get(GetGroupContext {
            actor_id: "张三".into(),
            request_context: GroupContextRequestContext {
                group_id: Some("game-room-7".into()),
                session_id: Some("sess-001".into()),
                round_id: None,
            },
            name: "game_state".into(),
            scope_levels: None,
            limit: None,
        })
        .await
        .expect("session get succeeds without roundId");
    assert_eq!(session_only.items.len(), 1);
    assert_eq!(session_only.items[0].content, "session");

    let round_only = app
        .get(GetGroupContext {
            actor_id: "张三".into(),
            request_context: ctx(),
            name: "game_state".into(),
            scope_levels: None,
            limit: None,
        })
        .await
        .expect("round get succeeds");
    assert_eq!(round_only.items.len(), 1);
    assert_eq!(round_only.items[0].content, "round");
}

#[tokio::test]
async fn list_without_scope_levels_queries_only_deepest_supplied_scope() {
    let app = service(Arc::new(MemoryGroupContextRepo::new()));
    for (scope_level, name) in [
        (ScopeLevel::Group, "group_rule"),
        (ScopeLevel::Session, "session_word"),
    ] {
        app.systemset(SystemSetGroupContext {
            request_context: ctx(),
            name: name.into(),
            scope_level,
            content: name.into(),
            visible_to: policy(&["张三"]),
            collect_from: policy(&["judge_bot"]),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect("seed visible row");
    }

    let group_list = app
        .list(ListGroupContexts {
            actor_id: "张三".into(),
            request_context: GroupContextRequestContext {
                group_id: Some("game-room-7".into()),
                session_id: None,
                round_id: None,
            },
            scope_levels: None,
        })
        .await
        .expect("group-only list succeeds");
    assert_eq!(
        group_list
            .contexts
            .iter()
            .map(|item| item.name.as_str())
            .collect::<Vec<_>>(),
        vec!["group_rule"]
    );

    let session_list = app
        .list(ListGroupContexts {
            actor_id: "张三".into(),
            request_context: GroupContextRequestContext {
                group_id: Some("game-room-7".into()),
                session_id: Some("sess-001".into()),
                round_id: None,
            },
            scope_levels: None,
        })
        .await
        .expect("session list succeeds without roundId");
    assert_eq!(
        session_list
            .contexts
            .iter()
            .map(|item| item.name.as_str())
            .collect::<Vec<_>>(),
        vec!["session_word"]
    );
}

#[tokio::test]
async fn get_filters_visible_only_sorts_fine_to_coarse_and_accepts_limit() {
    let app = service(Arc::new(MemoryGroupContextRepo::new()));
    for (scope_level, content) in [
        (ScopeLevel::Group, "group"),
        (ScopeLevel::Session, "session"),
        (ScopeLevel::Round, "round"),
    ] {
        app.systemset(SystemSetGroupContext {
            request_context: ctx(),
            name: "game_state".into(),
            scope_level,
            content: content.into(),
            visible_to: policy(&["张三"]),
            collect_from: policy(&["judge_bot"]),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect("seed visible row");
    }
    app.systemset(SystemSetGroupContext {
        request_context: ctx(),
        name: "write_only".into(),
        scope_level: ScopeLevel::Session,
        content: "secret".into(),
        visible_to: policy(&["李四"]),
        collect_from: policy(&["张三"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("seed write-only row");

    let response = app
        .get(GetGroupContext {
            actor_id: "张三".into(),
            request_context: ctx(),
            name: "game_state".into(),
            scope_levels: Some(vec![
                ScopeLevel::Group,
                ScopeLevel::Session,
                ScopeLevel::Round,
            ]),
            limit: Some(1),
        })
        .await
        .expect("get succeeds");
    assert_eq!(
        response
            .items
            .iter()
            .map(|item| item.content.as_str())
            .collect::<Vec<_>>(),
        vec!["round", "session", "group"]
    );

    let error = app
        .get(GetGroupContext {
            actor_id: "张三".into(),
            request_context: ctx(),
            name: "write_only".into(),
            scope_levels: Some(vec![ScopeLevel::Session]),
            limit: None,
        })
        .await
        .expect_err("unreadable context returns permission_denied");
    assert_code(error, "permission_denied");
}
