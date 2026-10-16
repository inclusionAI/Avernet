use std::sync::Arc;

use async_trait::async_trait;
use bcs_app_group_context::GroupContextApplicationServiceImpl;
use bcs_domain::{GroupContextEntry, GroupContextEntryMeta, GroupContextPermission, GroupContextPolicy, ScopeLevel};
use bcs_group_context_store::{
    GroupContextRepo, GroupContextStoreError, MemoryGroupContextRepo, NewGroupContextEntry,
    StoreResult,
};
use bcs_service_api::application::v1::{
    AddGroupContext, ApplicationError, GetGroupContext, GroupContextApplicationService,
    GroupContextRequestContext, ListGroupContexts, SystemSetGroupContext, UpdateGroupContext,
};
use chrono::{Duration, TimeZone, Utc};

fn ctx() -> GroupContextRequestContext {
    GroupContextRequestContext {
        group_id: "game-room-7".into(),
        session_id: Some("sess-001".into()),
        round_id: Some("round-003".into()),
    }
}

fn policy(users: &[&str]) -> GroupContextPolicy {
    GroupContextPolicy::users(users.iter().copied())
}

fn service(repo: Arc<dyn GroupContextRepo>) -> GroupContextApplicationServiceImpl {
    GroupContextApplicationServiceImpl::new(repo)
}

fn assert_code(error: ApplicationError, code: &str) {
    assert_eq!(error.code(), code, "unexpected error: {error}");
}

#[derive(Clone, Default)]
struct ConflictOnSupersedeRepo {
    inner: MemoryGroupContextRepo,
}

#[async_trait]
impl GroupContextRepo for ConflictOnSupersedeRepo {
    async fn create_active(&self, params: NewGroupContextEntry) -> StoreResult<GroupContextEntry> {
        self.inner.create_active(params).await
    }

    async fn supersede_active(
        &self,
        name: &str,
        scope: &str,
        _expected_active_id: i64,
        _params: NewGroupContextEntry,
    ) -> StoreResult<GroupContextEntry> {
        Err(GroupContextStoreError::Conflict {
            name: name.to_string(),
            scope: scope.to_string(),
        })
    }

    async fn get_active(&self, name: &str, scope: &str) -> StoreResult<Option<GroupContextEntry>> {
        self.inner.get_active(name, scope).await
    }

    async fn get_active_by_name_and_scopes(
        &self,
        name: &str,
        scopes: &[String],
    ) -> StoreResult<Vec<GroupContextEntry>> {
        self.inner.get_active_by_name_and_scopes(name, scopes).await
    }

    async fn list_active_meta_by_scopes(
        &self,
        scopes: &[String],
    ) -> StoreResult<Vec<GroupContextEntryMeta>> {
        self.inner.list_active_meta_by_scopes(scopes).await
    }

    async fn list_versions(&self, context_id: &str) -> StoreResult<Vec<GroupContextEntry>> {
        self.inner.list_versions(context_id).await
    }
}

#[tokio::test]
async fn add_resolves_raw_scope_and_rejects_duplicate_active() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());

    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: "张三出局".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["judge_bot"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("add succeeds");

    let active = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(active.scope, "sess-001");
    assert_eq!(active.scope_level, ScopeLevel::Session);

    let error = app
        .add(AddGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: "player_state".into(),
            scope_level: ScopeLevel::Session,
            content: "李四出局".into(),
            visible_to: policy(&[]),
            collect_from: policy(&["judge_bot"]),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("duplicate active conflicts");
    assert_code(error, "conflict");
}

#[tokio::test]
async fn add_validates_content_collect_from_actor_and_required_scope_params() {
    let app = service(Arc::new(MemoryGroupContextRepo::new()));

    let error = app
        .add(AddGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: "too_big".into(),
            scope_level: ScopeLevel::Group,
            content: "x".repeat(4097),
            visible_to: policy(&[]),
            collect_from: policy(&["judge_bot"]),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("oversize rejected");
    assert_code(error, "payload_too_large");

    let error = app
        .add(AddGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: "empty_collect".into(),
            scope_level: ScopeLevel::Group,
            content: "ok".into(),
            visible_to: policy(&[]),
            collect_from: policy(&[]),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("empty collect_from rejected");
    assert_code(error, "invalid_param");

    let error = app
        .add(AddGroupContext {
            actor_id: "other_bot".into(),
            request_context: ctx(),
            name: "not_writer".into(),
            scope_level: ScopeLevel::Group,
            content: "ok".into(),
            visible_to: policy(&[]),
            collect_from: policy(&["judge_bot"]),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("actor must be collect_from member");
    assert_code(error, "permission_denied");

    let error = app
        .add(AddGroupContext {
            actor_id: "judge_bot".into(),
            request_context: GroupContextRequestContext {
                group_id: "game-room-7".into(),
                session_id: None,
                round_id: None,
            },
            name: "missing_session".into(),
            scope_level: ScopeLevel::Session,
            content: "ok".into(),
            visible_to: policy(&[]),
            collect_from: policy(&["judge_bot"]),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("selected session scope requires sessionId");
    assert_code(error, "invalid_param");
}

fn add_for_validation(
    actor_id: &str,
    name: &str,
    group_id: &str,
    visible_to: GroupContextPolicy,
    collect_from: GroupContextPolicy,
) -> AddGroupContext {
    AddGroupContext {
        actor_id: actor_id.into(),
        request_context: GroupContextRequestContext { group_id: group_id.into(), session_id: Some("sess-001".into()), round_id: Some("round-003".into()) },
        name: name.into(),
        scope_level: ScopeLevel::Group,
        content: "ok".into(),
        visible_to,
        collect_from,
        valid_from: None,
        valid_to: None,
    }
}

#[tokio::test]
async fn add_rejects_blank_actor_name_and_group_scope() {
    let app = service(Arc::new(MemoryGroupContextRepo::new()));

    for (actor_id, name, group_id) in [
        ("  ", "player_state", "game-room-7"),
        ("judge_bot", "   ", "game-room-7"),
        ("judge_bot", "player_state", "   "),
    ] {
        let error = app
            .add(add_for_validation(
                actor_id,
                name,
                group_id,
                policy(&[]),
                policy(&["judge_bot"]),
            ))
            .await
            .expect_err("blank request fields must be rejected");
        assert_code(error, "invalid_param");
    }
}

fn oversized_policy() -> GroupContextPolicy {
    GroupContextPolicy::users((0..240).map(|index| format!("user-{index:03}-99999999")))
}

#[tokio::test]
async fn add_and_update_reject_values_larger_than_storage_columns() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());

    let error = app
        .add(add_for_validation(
            "judge_bot",
            &"n".repeat(256),
            "game-room-7",
            policy(&[]),
            policy(&["judge_bot"]),
        ))
        .await
        .expect_err("oversized name rejected");
    assert_code(error, "payload_too_large");

    let error = app
        .add(add_for_validation(
            "judge_bot",
            "oversized_scope",
            &"g".repeat(256),
            policy(&[]),
            policy(&["judge_bot"]),
        ))
        .await
        .expect_err("oversized scope rejected");
    assert_code(error, "payload_too_large");

    let error = app
        .add(add_for_validation(
            "judge_bot",
            "oversized_visible_to",
            "game-room-7",
            oversized_policy(),
            policy(&["judge_bot"]),
        ))
        .await
        .expect_err("oversized visible_to rejected");
    assert_code(error, "payload_too_large");

    app.add(add_for_validation(
        "judge_bot",
        "oversized_collect_from",
        "game-room-7",
        policy(&[]),
        policy(&["judge_bot"]),
    ))
    .await
    .expect("valid context added");

    let error = app
        .update(UpdateGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: "oversized_collect_from".into(),
            scope_level: ScopeLevel::Group,
            content: Some("new".into()),
            change_reason: None,
            visible_to: Some(oversized_policy()),
            collect_from: Some(policy(&["judge_bot"])),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("oversized update visible_to rejected");
    assert_code(error, "payload_too_large");

    let error = app
        .update(UpdateGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: "oversized_collect_from".into(),
            scope_level: ScopeLevel::Group,
            content: Some("new".into()),
            change_reason: None,
            visible_to: None,
            collect_from: Some(oversized_policy()),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("oversized update collect_from rejected");
    assert_code(error, "payload_too_large");

    let active = repo
        .get_active("oversized_collect_from", "game-room-7")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(active.content, "ok");
}

#[tokio::test]
async fn update_supersedes_preserves_omitted_fields_and_uses_old_writer_policy() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());
    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: "第一轮".into(),
        visible_to: policy(&["张三"]),
        collect_from: policy(&["judge_bot"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("add succeeds");
    let old = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("store ok")
        .expect("old active");

    app.update(UpdateGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: Some("第二轮".into()),
        change_reason: Some("第一轮投票".into()),
        visible_to: None,
        collect_from: None,
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("update succeeds");

    let active = repo
        .get_active("player_state", "sess-001")
        .await
        .expect("store ok")
        .expect("new active");
    assert_eq!(active.content, "第二轮");
    assert_eq!(active.visible_to, old.visible_to);
    assert_eq!(active.collect_from, old.collect_from);
    assert_eq!(active.context_id, old.context_id);
    assert_eq!(active.supersedes, Some(old.id));
    assert_eq!(active.change_reason.as_deref(), Some("第一轮投票"));

    let versions = repo
        .list_versions(&active.context_id)
        .await
        .expect("versions ok");
    assert_eq!(versions.len(), 2);
    assert!(versions.iter().any(|row| {
        row.id == old.id
            && row.is_superseded
            && row.gmt_superseded.is_some()
            && row.valid_to == old.valid_to
    }));
}

#[tokio::test]
async fn add_defaults_validity_and_preserves_explicit_validity() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());
    let before = Utc::now();

    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "default_validity".into(),
        scope_level: ScopeLevel::Session,
        content: "默认有效期".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["judge_bot"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("add with default validity succeeds");

    let defaulted = repo
        .get_active("default_validity", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert!(defaulted.valid_from >= before);
    assert!(defaulted.valid_from <= Utc::now());
    assert_eq!(defaulted.valid_to, None);

    let explicit_from = Utc.with_ymd_and_hms(2026, 10, 12, 8, 0, 0).unwrap();
    let explicit_to = Utc.with_ymd_and_hms(2026, 10, 13, 8, 0, 0).unwrap();
    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "explicit_validity".into(),
        scope_level: ScopeLevel::Session,
        content: "显式有效期".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["judge_bot"]),
        valid_from: Some(explicit_from),
        valid_to: Some(explicit_to),
    })
    .await
    .expect("add with explicit validity succeeds");

    let explicit = repo
        .get_active("explicit_validity", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(explicit.valid_from, explicit_from);
    assert_eq!(explicit.valid_to, Some(explicit_to));
}

#[tokio::test]
async fn update_defaults_omitted_validity_to_new_effective_time_and_can_change_valid_range() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());
    let original_from = Utc.with_ymd_and_hms(2026, 10, 12, 8, 0, 0).unwrap();
    let original_to = Utc.with_ymd_and_hms(2026, 10, 12, 9, 0, 0).unwrap();

    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "validity_patch".into(),
        scope_level: ScopeLevel::Session,
        content: "原内容".into(),
        visible_to: policy(&["张三"]),
        collect_from: policy(&["judge_bot"]),
        valid_from: Some(original_from),
        valid_to: Some(original_to),
    })
    .await
    .expect("add succeeds");

    let before_update = Utc::now();
    app.update(UpdateGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "validity_patch".into(),
        scope_level: ScopeLevel::Session,
        content: Some("新内容".into()),
        change_reason: None,
        visible_to: None,
        collect_from: None,
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("content-only update succeeds");
    let defaulted = repo
        .get_active("validity_patch", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(defaulted.content, "新内容");
    assert!(defaulted.valid_from >= before_update);
    assert!(defaulted.valid_from <= Utc::now());
    assert_eq!(defaulted.valid_to, None);
    assert_eq!(defaulted.visible_to, policy(&["张三"]));
    assert_eq!(defaulted.collect_from, policy(&["judge_bot"]));

    let changed_from = Utc.with_ymd_and_hms(2026, 10, 13, 8, 0, 0).unwrap();
    let changed_to = Utc.with_ymd_and_hms(2026, 10, 13, 9, 0, 0).unwrap();
    app.update(UpdateGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "validity_patch".into(),
        scope_level: ScopeLevel::Session,
        content: None,
        change_reason: Some("只调整业务有效期".into()),
        visible_to: None,
        collect_from: None,
        valid_from: Some(changed_from),
        valid_to: Some(changed_to),
    })
    .await
    .expect("validity-only update succeeds");
    let changed = repo
        .get_active("validity_patch", "sess-001")
        .await
        .expect("store ok")
        .expect("active row");
    assert_eq!(changed.content, "新内容");
    assert_eq!(changed.visible_to, policy(&["张三"]));
    assert_eq!(changed.collect_from, policy(&["judge_bot"]));
    assert_eq!(changed.valid_from, changed_from);
    assert_eq!(changed.valid_to, Some(changed_to));
}

#[tokio::test]
async fn get_and_list_do_not_filter_by_business_valid_to() {
    let app = service(Arc::new(MemoryGroupContextRepo::new()));
    let past_from = Utc::now() - Duration::days(2);
    let past_to = Utc::now() - Duration::days(1);

    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "expired_business_window".into(),
        scope_level: ScopeLevel::Session,
        content: "业务有效期已过但仍是当前版本".into(),
        visible_to: policy(&["张三"]),
        collect_from: policy(&["judge_bot"]),
        valid_from: Some(past_from),
        valid_to: Some(past_to),
    })
    .await
    .expect("add succeeds");

    let list = app
        .list(ListGroupContexts {
            actor_id: "张三".into(),
            request_context: ctx(),
            scope_levels: Some(vec![ScopeLevel::Session]),
        })
        .await
        .expect("list succeeds");
    assert_eq!(list.contexts.len(), 1);
    assert_eq!(list.contexts[0].name, "expired_business_window");
    assert_eq!(list.contexts[0].scope, "sess-001");
    assert_eq!(list.contexts[0].permission, GroupContextPermission::R);

    let get = app
        .get(GetGroupContext {
            actor_id: "张三".into(),
            request_context: ctx(),
            name: "expired_business_window".into(),
            scope_levels: Some(vec![ScopeLevel::Session]),
            limit: None,
        })
        .await
        .expect("get succeeds");
    assert_eq!(get.items.len(), 1);
    assert_eq!(get.items[0].content, "业务有效期已过但仍是当前版本");
    assert_eq!(get.items[0].valid_from, past_from);
    assert_eq!(get.items[0].valid_to, Some(past_to));
}

#[tokio::test]
async fn update_maps_missing_permission_and_stale_supersede() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());

    let error = app
        .update(UpdateGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: "missing".into(),
            scope_level: ScopeLevel::Session,
            content: Some("new".into()),
            change_reason: None,
            visible_to: None,
            collect_from: None,
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("missing row");
    assert_code(error, "not_found");

    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "private_write".into(),
        scope_level: ScopeLevel::Session,
        content: "old".into(),
        visible_to: policy(&[]),
        collect_from: policy(&["judge_bot"]),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("add succeeds");
    let error = app
        .update(UpdateGroupContext {
            actor_id: "other_bot".into(),
            request_context: ctx(),
            name: "private_write".into(),
            scope_level: ScopeLevel::Session,
            content: Some("new".into()),
            change_reason: None,
            visible_to: None,
            collect_from: None,
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("old collect_from controls update permission");
    assert_code(error, "permission_denied");

    let conflict_repo = Arc::new(ConflictOnSupersedeRepo::default());
    let conflict_app = service(conflict_repo);
    conflict_app
        .add(AddGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: "racy".into(),
            scope_level: ScopeLevel::Session,
            content: "old".into(),
            visible_to: policy(&[]),
            collect_from: policy(&["judge_bot"]),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect("seed succeeds");
    let error = conflict_app
        .update(UpdateGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: "racy".into(),
            scope_level: ScopeLevel::Session,
            content: Some("new".into()),
            change_reason: None,
            visible_to: None,
            collect_from: None,
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("concurrent supersede maps to conflict");
    assert_code(error, "conflict");
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
                group_id: "game-room-7".into(),
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
                group_id: "game-room-7".into(),
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
                group_id: "game-room-7".into(),
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
                group_id: "game-room-7".into(),
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
