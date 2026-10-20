use async_trait::async_trait;
use bcs_service_api::application::v1::{
    AddGroupContext, ApplicationError, GetGroupContext, GroupContextAck,
    GroupContextApplicationService, GroupContextEntry, GroupContextGetItem,
    GroupContextGetResponse, GroupContextListItem, GroupContextListResponse, GroupContextOwners,
    GroupContextPermission, GroupContextPolicy, GroupContextRequestContext, GroupContextStatus,
    ListGroupContexts, ScopeLevel, SystemSetGroupContext, UpdateGroupContext,
};
use serde_json::json;

fn time(value: &str) -> bcs_service_api::application::v1::GroupContextTime {
    value.parse().expect("valid RFC3339 test time")
}

struct NoopGroupContextService;

#[async_trait]
impl GroupContextApplicationService for NoopGroupContextService {
    async fn add(&self, _command: AddGroupContext) -> Result<GroupContextAck, ApplicationError> {
        Ok(GroupContextAck::ok())
    }

    async fn update(
        &self,
        _command: UpdateGroupContext,
    ) -> Result<GroupContextAck, ApplicationError> {
        Ok(GroupContextAck::ok())
    }

    async fn list(
        &self,
        _query: ListGroupContexts,
    ) -> Result<GroupContextListResponse, ApplicationError> {
        Ok(GroupContextListResponse { contexts: vec![] })
    }

    async fn get(
        &self,
        _query: GetGroupContext,
    ) -> Result<GroupContextGetResponse, ApplicationError> {
        Ok(GroupContextGetResponse { items: vec![] })
    }

    async fn systemset(
        &self,
        _command: SystemSetGroupContext,
    ) -> Result<GroupContextAck, ApplicationError> {
        Ok(GroupContextAck::ok())
    }
}

fn request_context() -> GroupContextRequestContext {
    GroupContextRequestContext {
        group_id: Some("game-room-7".into()),
        session_id: Some("sess-001".into()),
        round_id: Some("round-1".into()),
    }
}

#[test]
fn request_context_keeps_all_scope_ids_optional_without_a_default_value() {
    let context = GroupContextRequestContext::default();

    assert_eq!(context.group_id, None);
    assert_eq!(context.session_id, None);
    assert_eq!(context.round_id, None);
}

#[test]
fn scope_level_supports_exact_v1_values_and_rejects_unknown_values() {
    assert_eq!(
        serde_json::to_string(&ScopeLevel::Group).unwrap(),
        "\"group\""
    );
    assert_eq!(
        serde_json::to_string(&ScopeLevel::Session).unwrap(),
        "\"session\""
    );
    assert_eq!(
        serde_json::to_string(&ScopeLevel::Round).unwrap(),
        "\"round\""
    );

    assert!(serde_json::from_value::<ScopeLevel>(json!("tenant")).is_err());
    assert!(serde_json::from_value::<ScopeLevel>(json!("group_session")).is_err());
}

#[test]
fn list_and_get_default_to_deepest_supplied_scope_level() {
    let group_only = GroupContextRequestContext {
        group_id: Some("game-room-7".into()),
        session_id: None,
        round_id: None,
    };
    let session_context = GroupContextRequestContext {
        group_id: Some("game-room-7".into()),
        session_id: Some("sess-001".into()),
        round_id: None,
    };

    let list = ListGroupContexts {
        actor_id: "judge_bot".into(),
        request_context: group_only,
        scope_levels: None,
    };
    assert_eq!(list.effective_scope_levels(), vec![ScopeLevel::Group]);

    let list = ListGroupContexts {
        actor_id: "judge_bot".into(),
        request_context: session_context,
        scope_levels: None,
    };
    assert_eq!(list.effective_scope_levels(), vec![ScopeLevel::Session]);

    let get = GetGroupContext {
        actor_id: "judge_bot".into(),
        request_context: request_context(),
        name: "player_state".into(),
        scope_levels: None,
        limit: Some(10),
    };
    assert_eq!(get.effective_scope_levels(), vec![ScopeLevel::Round]);
}

#[test]
fn get_limit_is_compatibility_parameter_for_future_wildcard_queries() {
    let get = GetGroupContext {
        actor_id: "judge_bot".into(),
        request_context: request_context(),
        name: "player_state".into(),
        scope_levels: Some(vec![
            ScopeLevel::Group,
            ScopeLevel::Session,
            ScopeLevel::Round,
        ]),
        limit: Some(1),
    };

    assert_eq!(get.limit, Some(1));
    assert_eq!(get.effective_scope_levels().len(), 3);
    // With today's concrete group/session/round URL params, each selected scope
    // can match at most one active `(name, scope)` row. `limit` is retained so
    // future wildcard conditions can return multiple rows without a contract break.
}

#[test]
fn permission_labels_are_the_spec_values() {
    assert_eq!(
        GroupContextPermission::from_access(true, false),
        Some(GroupContextPermission::R)
    );
    assert_eq!(
        GroupContextPermission::from_access(false, true),
        Some(GroupContextPermission::W)
    );
    assert_eq!(
        GroupContextPermission::from_access(true, true),
        Some(GroupContextPermission::WR)
    );
    assert_eq!(GroupContextPermission::from_access(false, false), None);

    assert_eq!(
        serde_json::to_value(GroupContextPermission::R).unwrap(),
        json!("R")
    );
    assert_eq!(
        serde_json::to_value(GroupContextPermission::W).unwrap(),
        json!("W")
    );
    assert_eq!(
        serde_json::to_value(GroupContextPermission::WR).unwrap(),
        json!("WR")
    );
    assert_eq!(
        serde_json::to_value(GroupContextPermission::WRO).unwrap(),
        json!("WRO")
    );
}

#[test]
fn owners_are_a_multi_value_json_object_with_stable_user_ids_field() {
    let owners = GroupContextOwners::new(["creator-bot", "player-two"]);
    let value = serde_json::to_value(&owners).expect("owners json");
    assert_eq!(value, json!({ "userIds": ["creator-bot", "player-two"] }));

    let decoded: GroupContextOwners = serde_json::from_value(value).expect("owners deserializes");
    assert_eq!(decoded, owners);
    assert!(decoded.contains("creator-bot"));
    assert!(decoded.contains("player-two"));
    assert!(!decoded.contains("player-three"));
    assert!(!decoded.is_empty());

    let empty = GroupContextOwners::new(Vec::<String>::new());
    assert!(empty.is_empty());
}

#[test]
fn policy_shape_is_json_object_with_user_ids() {
    let policy = GroupContextPolicy::users(["judge_bot", "张三"]);
    let value = serde_json::to_value(&policy).expect("policy json");
    assert_eq!(value, json!({ "userIds": ["judge_bot", "张三"] }));

    let visible_to_everyone = GroupContextPolicy::users(Vec::<String>::new());
    assert!(visible_to_everyone.is_visible_to("anyone"));
    assert!(!visible_to_everyone.contains_user("anyone"));
}

#[test]
fn commands_cover_all_groupcontext_endpoints_without_body_actor_for_systemset() {
    let add = AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: request_context(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: "张三存活".into(),
        visible_to: GroupContextPolicy::users(Vec::<String>::new()),
        collect_from: GroupContextPolicy::users(["judge_bot"]),
        valid_from: Some(time("2026-10-12T08:16:08Z")),
        valid_to: None,
    };
    assert_eq!(add.actor_id, "judge_bot");

    let update = UpdateGroupContext {
        actor_id: "judge_bot".into(),
        request_context: request_context(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: Some("张三出局".into()),
        change_reason: Some("第一轮投票".into()),
        visible_to: None,
        collect_from: None,
        owners: Some(GroupContextOwners::new(["judge_bot"])),
        valid_from: None,
        valid_to: Some(time("2026-10-13T08:16:08Z")),
    };
    assert_eq!(update.change_reason.as_deref(), Some("第一轮投票"));

    let systemset = SystemSetGroupContext {
        request_context: request_context(),
        name: "game_rule".into(),
        scope_level: ScopeLevel::Group,
        content: "规则".into(),
        visible_to: GroupContextPolicy::users(Vec::<String>::new()),
        collect_from: GroupContextPolicy::users(["admin"]),
        valid_from: None,
        valid_to: None,
    };
    assert_eq!(systemset.name, "game_rule");
}

#[test]
fn entry_active_state_uses_superseded_marker_not_business_valid_to() {
    let now = time("2026-10-12T08:16:08Z");
    let past = time("2023-11-14T22:13:20Z");
    let mut entry = GroupContextEntry {
        id: 42,
        context_id: "ctx_42".into(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        scope: "sess-001".into(),
        content: "张三存活".into(),
        creator: "judge_bot".into(),
        visible_to: GroupContextPolicy::users(Vec::<String>::new()),
        collect_from: GroupContextPolicy::users(["judge_bot"]),
        owners: GroupContextOwners::new(["judge_bot"]),
        supersedes: None,
        change_reason: None,
        valid_from: past,
        valid_to: Some(past),
        is_superseded: false,
        gmt_superseded: None,
        tx_time: now,
        gmt_create: now,
        gmt_modified: now,
    };

    assert!(
        entry.is_active(),
        "business expiry must not make the current row inactive"
    );

    entry.is_superseded = true;
    entry.gmt_superseded = Some(now);
    assert!(!entry.is_active());

    let value = serde_json::to_value(&entry).expect("entry serializes");
    assert_eq!(value["is_superseded"], json!(true));
    assert_eq!(value["gmt_superseded"], json!("2026-10-12T08:16:08Z"));
}

#[test]
fn list_item_serializes_meta_only_without_content_or_valid_fields() {
    let now = time("2026-10-12T08:16:08Z");
    let item = GroupContextListItem {
        name: "game_rule".into(),
        scope_level: ScopeLevel::Group,
        scope: "game-room-7".into(),
        permission: GroupContextPermission::R,
        gmt_create: now,
        gmt_modified: now,
    };

    let value = serde_json::to_value(&item).expect("list item serializes");
    assert_eq!(
        value,
        json!({
            "name": "game_rule",
            "scope_level": "group",
            "scope": "game-room-7",
            "permission": "R",
            "gmt_create": "2026-10-12T08:16:08Z",
            "gmt_modified": "2026-10-12T08:16:08Z"
        })
    );
    assert!(value.get("content").is_none());
    assert!(value.get("valid_from").is_none());
    assert!(value.get("valid_to").is_none());
}

#[test]
fn get_item_serializes_content_and_business_validity() {
    let valid_from = time("2026-10-12T08:16:08Z");
    let valid_to = time("2026-10-13T08:16:08Z");
    let item = GroupContextGetItem {
        name: "player_word_1".into(),
        scope_level: ScopeLevel::Session,
        content: "你的词语是：香蕉".into(),
        valid_from,
        valid_to: Some(valid_to),
        visible_to: None,
        collect_from: None,
    };

    let value = serde_json::to_value(&item).expect("get item serializes");
    assert_eq!(value["content"], json!("你的词语是：香蕉"));
    assert_eq!(value["valid_from"], json!("2026-10-12T08:16:08Z"));
    assert_eq!(value["valid_to"], json!("2026-10-13T08:16:08Z"));
}

#[test]
fn commands_serialize_optional_business_validity_fields() {
    let valid_from = time("2026-10-12T08:16:08Z");
    let add = AddGroupContext {
        actor_id: "judge_bot".into(),
        request_context: request_context(),
        name: "player_state".into(),
        scope_level: ScopeLevel::Session,
        content: "张三存活".into(),
        visible_to: GroupContextPolicy::users(Vec::<String>::new()),
        collect_from: GroupContextPolicy::users(["judge_bot"]),
        valid_from: Some(valid_from),
        valid_to: None,
    };

    let value = serde_json::to_value(&add).expect("add command serializes");
    assert_eq!(value["valid_from"], json!("2026-10-12T08:16:08Z"));
    assert!(value.get("valid_to").is_none());

    let decoded: AddGroupContext = serde_json::from_value(value).expect("add command deserializes");
    assert_eq!(decoded.valid_from, Some(valid_from));
    assert_eq!(decoded.valid_to, None);
}

#[test]
fn response_statuses_cover_spec_success_and_error_values() {
    assert_eq!(
        serde_json::to_value(GroupContextStatus::Ok).unwrap(),
        json!("ok")
    );
    assert_eq!(
        serde_json::to_value(GroupContextStatus::InvalidParam).unwrap(),
        json!("invalid_param")
    );
    assert_eq!(
        serde_json::to_value(GroupContextStatus::PermissionDenied).unwrap(),
        json!("permission_denied")
    );
    assert_eq!(
        serde_json::to_value(GroupContextStatus::NotFound).unwrap(),
        json!("not_found")
    );
    assert_eq!(
        serde_json::to_value(GroupContextStatus::Conflict).unwrap(),
        json!("conflict")
    );
    assert_eq!(
        serde_json::to_value(GroupContextStatus::PayloadTooLarge).unwrap(),
        json!("payload_too_large")
    );
    assert_eq!(
        serde_json::to_value(GroupContextStatus::InternalError).unwrap(),
        json!("internal_error")
    );

    let ok = GroupContextAck::ok();
    assert_eq!(ok.status, GroupContextStatus::Ok);
    assert_eq!(ok.error_msg, None);
}

#[test]
fn group_context_service_is_object_safe() {
    fn accepts_service(_: &dyn GroupContextApplicationService) {}
    accepts_service(&NoopGroupContextService);
}

#[test]
fn get_item_policy_fields_are_owner_only_and_omitted_when_missing() {
    let valid_from = time("2026-10-12T08:16:08Z");
    let non_owner = GroupContextGetItem {
        name: "player_word_1".into(),
        scope_level: ScopeLevel::Session,
        content: "你的词语是：香蕉".into(),
        valid_from,
        valid_to: None,
        visible_to: None,
        collect_from: None,
    };

    let non_owner_json = serde_json::to_value(&non_owner).expect("non-owner get item serializes");
    assert!(non_owner_json.get("visible_to").is_none());
    assert!(non_owner_json.get("collect_from").is_none());

    let owner = GroupContextGetItem {
        visible_to: Some(GroupContextPolicy::users(Vec::<String>::new())),
        collect_from: Some(GroupContextPolicy::users(["judge_bot"])),
        ..non_owner
    };
    let owner_json = serde_json::to_value(&owner).expect("owner get item serializes");
    assert_eq!(owner_json["visible_to"], json!({ "userIds": [] }));
    assert_eq!(
        owner_json["collect_from"],
        json!({ "userIds": ["judge_bot"] })
    );
}
