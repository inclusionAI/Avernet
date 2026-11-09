use std::sync::Arc;

use async_trait::async_trait;
use bcs_app_group_context::GroupContextApplicationServiceImpl;
use bcs_domain::{
    GroupContextEntry, GroupContextEntryMeta, GroupContextOwners, GroupContextPermission,
    GroupContextPolicy, ScopeLevel,
};
use bcs_group_context_store::{
    GroupContextRepo, GroupContextStoreError, MemoryGroupContextRepo, NewGroupContextEntry,
    StoreResult,
};
use bcs_service_api::application::v1::{
    AddGroupContext, ApplicationError, GetGroupContext, GroupContextApplicationService,
    GroupContextRequestContext, ListGroupContexts, SystemSetGroupContext, UpdateGroupContext,
};
use chrono::{Duration, TimeZone, Utc};
use serde_json::json;

fn ctx() -> GroupContextRequestContext {
    GroupContextRequestContext {
        group_id: Some("game-room-7".into()),
        session_id: Some("sess-001".into()),
        round_id: Some("round-003".into()),
    }
}

fn policy(users: &[&str]) -> GroupContextPolicy {
    GroupContextPolicy::users(users.iter().copied())
}

fn owners(user_ids: impl IntoIterator<Item = impl Into<String>>) -> GroupContextOwners {
    GroupContextOwners::new(user_ids)
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
    async fn list_slots(&self, group_id: &str) -> StoreResult<Vec<bcs_domain::group_context::GroupContextSlot>> {
        self.inner.list_slots(group_id).await
    }

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
        collect_from: Some(policy(&["judge_bot"])),
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
            collect_from: Some(policy(&["judge_bot"])),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("duplicate active conflicts");
    assert_code(error, "conflict");
}

#[tokio::test]
async fn add_session_and_round_scope_do_not_require_group_id() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());

    for (name, scope_level, expected_scope) in [
        ("session_context", ScopeLevel::Session, "sess-001"),
        ("round_context", ScopeLevel::Round, "round-003"),
    ] {
        let mut request_context = ctx();
        request_context.group_id = None;

        app.add(AddGroupContext {
            actor_id: "judge_bot".into(),
            request_context,
            name: name.into(),
            scope_level,
            content: "ok".into(),
            visible_to: policy(&[]),
            collect_from: Some(policy(&["judge_bot"])),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect("selected scope boundary is enough");

        let active = repo
            .get_active(name, expected_scope)
            .await
            .expect("store ok")
            .expect("active row");
        assert_eq!(active.scope_level, scope_level);
        assert_eq!(active.scope, expected_scope);
    }
}

#[tokio::test]
async fn add_collect_from_is_optional_empty_and_independent_of_owner_authority() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = service(repo.clone());

    for (name, collect_from) in [
        ("missing_collect", None),
        ("empty_collect", Some(policy(&[]))),
    ] {
        app.add(AddGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: name.into(),
            scope_level: ScopeLevel::Group,
            content: "owner-only v1".into(),
            visible_to: policy(&[]),
            collect_from,
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect("add does not require the creator in collect_from");

        let active = repo
            .get_active(name, "game-room-7")
            .await
            .expect("store ok")
            .expect("active row");
        assert_eq!(active.collect_from, policy(&[]));
        assert_eq!(active.owners, owners(["judge_bot"]));

        app.update(UpdateGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: name.into(),
            scope_level: ScopeLevel::Group,
            content: Some("owner-only v2".into()),
            change_reason: None,
            visible_to: None,
            collect_from: None,
            owners: None,
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect("owner remains writable without collect_from membership");

        let active = repo
            .get_active(name, "game-room-7")
            .await
            .expect("store ok")
            .expect("updated row");
        assert_eq!(active.collect_from, policy(&[]));
        assert_eq!(active.owners, owners(["judge_bot"]));
        assert_eq!(active.content, "owner-only v2");
    }

    app.add(AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: ctx(),
        name: "delegated_writer".into(),
        scope_level: ScopeLevel::Group,
        content: "delegated v1".into(),
        visible_to: policy(&[]),
        collect_from: Some(policy(&["other_bot"])),
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("creator may add while delegating collect_from to another bot");

    app.update(UpdateGroupContext {
        actor_id: "other_bot".into(),
        request_context: ctx(),
        name: "delegated_writer".into(),
        scope_level: ScopeLevel::Group,
        content: Some("delegated v2".into()),
        change_reason: Some("listed writer".into()),
        visible_to: None,
        collect_from: None,
        owners: None,
        valid_from: None,
        valid_to: None,
    })
    .await
    .expect("explicit collect_from member keeps write access");

    let delegated = repo
        .get_active("delegated_writer", "game-room-7")
        .await
        .expect("store ok")
        .expect("delegated row");
    assert_eq!(delegated.content, "delegated v2");
    assert_eq!(delegated.collect_from, policy(&["other_bot"]));
    assert_eq!(delegated.owners, owners(["judge_bot"]));
}

#[tokio::test]
async fn add_reports_missing_scope_boundary_with_scope_level() {
    let app = service(Arc::new(MemoryGroupContextRepo::new()));

    for (scope_level, mut request_context, expected_message) in [
        (
            ScopeLevel::Group,
            ctx(),
            "param groupId is required when scope_level=group",
        ),
        (
            ScopeLevel::Session,
            ctx(),
            "param sessionId is required when scope_level=session",
        ),
        (
            ScopeLevel::Round,
            ctx(),
            "param roundId is required when scope_level=round",
        ),
    ] {
        request_context.group_id = None;
        match scope_level {
            ScopeLevel::Group => {
                request_context.session_id = None;
                request_context.round_id = None;
            }
            ScopeLevel::Session => request_context.session_id = None,
            ScopeLevel::Round => request_context.round_id = None,
        }

        let error = app
            .add(AddGroupContext {
                actor_id: "judge_bot".into(),
                request_context,
                name: "boundary".into(),
                scope_level,
                content: "ok".into(),
                visible_to: policy(&[]),
                collect_from: Some(policy(&["judge_bot"])),
                valid_from: None,
                valid_to: None,
            })
            .await
            .expect_err("selected boundary is required");
        assert!(
            error.to_string().contains(&expected_message),
            "error: {error}"
        );
    }
}

#[tokio::test]
async fn update_list_and_get_use_selected_scope_without_group_id() {
    let app = service(Arc::new(MemoryGroupContextRepo::new()));

    for (scope_level, request_context, expected_scope) in [
        (
            ScopeLevel::Session,
            GroupContextRequestContext {
                group_id: None,
                session_id: Some("sess-001".into()),
                round_id: None,
            },
            "sess-001",
        ),
        (
            ScopeLevel::Round,
            GroupContextRequestContext {
                group_id: None,
                session_id: None,
                round_id: Some("round-003".into()),
            },
            "round-003",
        ),
    ] {
        app.systemset(SystemSetGroupContext {
            request_context: request_context.clone(),
            name: "selected_scope".into(),
            scope_level,
            content: "initial".into(),
            visible_to: policy(&[]),
            collect_from: policy(&["judge_bot"]),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect("seed succeeds");

        app.update(UpdateGroupContext {
            actor_id: "judge_bot".into(),
            request_context: request_context.clone(),
            name: "selected_scope".into(),
            scope_level,
            content: Some("updated".into()),
            change_reason: None,
            visible_to: None,
            collect_from: None,
            owners: None,
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect("selected scope boundary is enough for update");

        let get = app
            .get(GetGroupContext {
                actor_id: "张三".into(),
                request_context: request_context.clone(),
                name: "selected_scope".into(),
                scope_levels: None,
                limit: None,
            })
            .await
            .expect("selected scope boundary is enough for get");
        assert_eq!(get.items.len(), 1);
        assert_eq!(get.items[0].content, "updated");

        let list = app
            .list(ListGroupContexts {
                actor_id: "张三".into(),
                request_context: request_context.clone(),
                scope_levels: Some(vec![scope_level]),
            })
            .await
            .expect("selected scope boundary is enough for list");
        assert_eq!(
            list.contexts
                .iter()
                .map(|item| item.scope.as_str())
                .collect::<Vec<_>>(),
            vec![expected_scope]
        );
    }
}

#[tokio::test]
async fn add_validates_content_and_required_scope_params() {
    let app = service(Arc::new(MemoryGroupContextRepo::new()));

    let error = app
        .add(AddGroupContext {
            actor_id: "judge_bot".into(),
            request_context: ctx(),
            name: "too_big".into(),
            scope_level: ScopeLevel::Group,
            content: "x".repeat(4097),
            visible_to: policy(&[]),
            collect_from: Some(policy(&["judge_bot"])),
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("oversize rejected");
    assert_code(error, "payload_too_large");

    let error = app
        .add(AddGroupContext {
            actor_id: "judge_bot".into(),
            request_context: GroupContextRequestContext {
                group_id: Some("game-room-7".into()),
                session_id: None,
                round_id: None,
            },
            name: "missing_session".into(),
            scope_level: ScopeLevel::Session,
            content: "ok".into(),
            visible_to: policy(&[]),
            collect_from: Some(policy(&["judge_bot"])),
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
        request_context: GroupContextRequestContext {
            group_id: Some(group_id.to_string()),
            session_id: Some("sess-001".into()),
            round_id: Some("round-003".into()),
        },
        name: name.into(),
        scope_level: ScopeLevel::Group,
        content: "ok".into(),
        visible_to,
        collect_from: Some(collect_from),
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
            &"a".repeat(256),
            "oversized_actor",
            "game-room-7",
            policy(&[]),
            policy(&["judge_bot"]),
        ))
        .await
        .expect_err("oversized actor rejected before storage");
    assert_code(error, "payload_too_large");

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
            owners: None,
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
            owners: None,
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
        collect_from: Some(policy(&["judge_bot"])),
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
        owners: None,
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
        collect_from: Some(policy(&["judge_bot"])),
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
        collect_from: Some(policy(&["judge_bot"])),
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
        collect_from: Some(policy(&["judge_bot"])),
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
        owners: None,
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
        owners: None,
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
        collect_from: Some(policy(&["judge_bot"])),
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
            owners: None,
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
        collect_from: Some(policy(&["judge_bot"])),
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
            owners: None,
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
            collect_from: Some(policy(&["judge_bot"])),
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
            owners: None,
            valid_from: None,
            valid_to: None,
        })
        .await
        .expect_err("concurrent supersede maps to conflict");
    assert_code(error, "conflict");
}

#[path = "group_context_service/creator_audit.rs"]
mod creator_audit;
#[path = "group_context_service/ownership.rs"]
mod ownership;
