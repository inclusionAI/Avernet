use std::sync::Arc;

use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode};
use bcs_app_group_context::GroupContextApplicationServiceImpl;
use bcs_bot::BotCore;
use bcs_group_context_store::{GroupContextRepo, MemoryGroupContextRepo};
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{BotCapabilities, BotRegistryCoreService};
use bcs_services_container::Services;
use serde::Deserialize;
use serde_json::{Value, json};
use tempfile::TempDir;
use tower::ServiceExt;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WhoIsSpyFixture {
    group_id: String,
    session_id: String,
    admin: String,
    judge_bot: String,
    players: Vec<String>,
    undercover_players: Vec<String>,
    civilian_players: Vec<String>,
    undercover_word: String,
    civilian_word: String,
}

fn fixture() -> WhoIsSpyFixture {
    serde_json::from_str(include_str!(
        "../../../../tests/fixtures/group_context_who_is_spy.json"
    ))
    .expect("fixture parses")
}

async fn app_with_repo(repo: Arc<MemoryGroupContextRepo>) -> axum::Router {
    let temp_dir = TempDir::new().expect("temp dir");
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    for (bot_id, token) in [
        ("admin", "admin-token"),
        ("judge_bot", "judge-token"),
        ("张三", "zhangsan-token"),
        ("李四", "lisi-token"),
        ("王五", "wangwu-token"),
        ("赵六", "zhaoliu-token"),
        ("钱七", "qianqi-token"),
    ] {
        registry
            .register_with_owner_and_token(
                bot_id.to_string(),
                BotCapabilities::default(),
                "test",
                token,
            )
            .await
            .expect("register bot token");
    }

    let group_context = Arc::new(GroupContextApplicationServiceImpl::new(repo));
    let services = Services::builder()
        .registry(registry as Arc<dyn BotRegistryCoreService>)
        .group_context(group_context)
        .build_for_test();
    build_router(HttpAppState::new(services))
}

fn token_for(actor: &str) -> &'static str {
    match actor {
        "admin" => "admin-token",
        "judge_bot" => "judge-token",
        "张三" => "zhangsan-token",
        "李四" => "lisi-token",
        "王五" => "wangwu-token",
        "赵六" => "zhaoliu-token",
        "钱七" => "qianqi-token",
        other => panic!("missing test token for {other}"),
    }
}

fn authed_post(uri: &str, token: &str, body: Value) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json")
        .header("authorization", format!("Bearer {token}"))
        .body(Body::from(body.to_string()))
        .expect("request")
}

async fn response_json(response: axum::response::Response) -> Value {
    let bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("read response body");
    serde_json::from_slice(&bytes).expect("JSON response")
}

async fn post_json(app: &axum::Router, uri: &str, token: &str, body: Value) -> (StatusCode, Value) {
    let response = app
        .clone()
        .oneshot(authed_post(uri, token, body))
        .await
        .expect("response");
    let status = response.status();
    let body = response_json(response).await;
    (status, body)
}

fn names(body: &Value, field: &str) -> Vec<String> {
    body[field]
        .as_array()
        .expect(field)
        .iter()
        .map(|item| item["name"].as_str().expect("name").to_string())
        .collect()
}

#[tokio::test]
async fn who_is_spy_walkthrough_passes_through_direct_bcs_http_router() {
    let fx = fixture();
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = app_with_repo(repo.clone()).await;
    let group_add_uri = format!("/groupcontext/add?groupId={}", fx.group_id);
    let group_update_uri = format!("/groupcontext/update?groupId={}", fx.group_id);
    let session_add_uri = format!(
        "/groupcontext/add?groupId={}&sessionId={}",
        fx.group_id, fx.session_id
    );
    let session_update_uri = format!(
        "/groupcontext/update?groupId={}&sessionId={}",
        fx.group_id, fx.session_id
    );

    let (status, body) = post_json(
        &app,
        &group_add_uri,
        token_for(&fx.admin),
        json!({
            "name": "game_rule",
            "scope_level": "group",
            "content": "【谁是卧底游戏规则】每轮每人描述自己的词语，不能直接说出词语。",
            "visible_to": {"userIds": []},
            "collect_from": {"userIds": [fx.admin.clone()]}
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!({"status": "ok", "error_msg": null}));

    let (status, body) = post_json(
        &app,
        &group_update_uri,
        token_for(&fx.admin),
        json!({
            "name": "game_rule",
            "scope_level": "group",
            "content": "【谁是卧底游戏规则】每轮描述词语并投票，票数最高者出局。"
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!({"status": "ok", "error_msg": null}));

    let undercover_visible = json!([
        fx.judge_bot,
        fx.undercover_players[0],
        fx.undercover_players[1]
    ]);
    let civilian_visible = json!([
        fx.judge_bot,
        fx.civilian_players[0],
        fx.civilian_players[1],
        fx.civilian_players[2]
    ]);
    for (name, content, visible_to) in [
        (
            "player_word_1",
            format!("卧底词语：{}", fx.undercover_word),
            undercover_visible,
        ),
        (
            "player_word_2",
            format!("平民词语：{}", fx.civilian_word),
            civilian_visible,
        ),
        (
            "player_role",
            "卧底：张三、赵六；平民：李四、王五、钱七".to_string(),
            json!(["judge_bot"]),
        ),
        (
            "player_state",
            fx.players
                .iter()
                .map(|player| format!("{player}存活"))
                .collect::<Vec<_>>()
                .join("、"),
            json!([]),
        ),
    ] {
        let (status, body) = post_json(
            &app,
            &session_add_uri,
            token_for(&fx.judge_bot),
            json!({
                "name": name,
                "scope_level": "session",
                "content": content,
                "visible_to": {"userIds": visible_to},
                "collect_from": {"userIds": ["judge_bot"]}
            }),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "add {name}: {body}");
        assert_eq!(body, json!({"status": "ok", "error_msg": null}));
    }

    let (status, body) = post_json(
        &app,
        &session_update_uri,
        token_for(&fx.judge_bot),
        json!({
            "name": "player_state",
            "scope_level": "session",
            "content": "张三出局、李四存活、王五存活、赵六存活、钱七存活",
            "change_reason": "第一轮投票"
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body, json!({"status": "ok", "error_msg": null}));

    let active_player_state = repo
        .get_active("player_state", &fx.session_id)
        .await
        .expect("active row")
        .expect("active player_state");
    assert_eq!(
        active_player_state.content,
        "张三出局、李四存活、王五存活、赵六存活、钱七存活"
    );
    let versions = repo
        .list_versions(&active_player_state.context_id)
        .await
        .expect("versions");
    assert_eq!(versions.len(), 2);
    let old_version = versions
        .iter()
        .find(|entry| entry.is_superseded)
        .expect("old version");
    let new_version = versions
        .iter()
        .find(|entry| !entry.is_superseded)
        .expect("new version");
    assert!(old_version.content.contains("张三存活"));
    assert!(old_version.gmt_superseded.is_some());
    assert_eq!(new_version.supersedes, Some(old_version.id));
    assert_eq!(new_version.change_reason.as_deref(), Some("第一轮投票"));

    for name in ["player_word_1", "player_word_2", "player_role"] {
        let (status, body) = post_json(
            &app,
            &format!(
                "/groupcontext/get?groupId={}&sessionId={}",
                fx.group_id, fx.session_id
            ),
            token_for(&fx.judge_bot),
            json!({"name": name, "scope_levels": ["session"]}),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "judge get {name}: {body}");
        assert_eq!(body["items"].as_array().expect("items").len(), 1);
    }

    let zhangsan = &fx.undercover_players[0];
    let (status, body) = post_json(
        &app,
        &format!(
            "/groupcontext/list?groupId={}&sessionId={}",
            fx.group_id, fx.session_id
        ),
        token_for(zhangsan),
        json!({"scope_levels": ["group", "session"]}),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    for item in body["contexts"].as_array().expect("contexts") {
        assert!(
            item.get("content").is_none(),
            "list item must be meta-only: {item}"
        );
        assert!(
            item.get("valid_from").is_none(),
            "list item must omit valid_from: {item}"
        );
        assert!(
            item.get("valid_to").is_none(),
            "list item must omit valid_to: {item}"
        );
    }
    let visible_names = names(&body, "contexts");
    assert!(visible_names.contains(&"game_rule".to_string()));
    assert!(visible_names.contains(&"player_word_1".to_string()));
    assert!(visible_names.contains(&"player_state".to_string()));
    assert!(!visible_names.contains(&"player_word_2".to_string()));
    assert!(!visible_names.contains(&"player_role".to_string()));

    for (name, levels) in [
        ("game_rule", json!(["group"])),
        ("player_word_1", json!(["session"])),
        ("player_state", json!(["session"])),
    ] {
        let (status, body) = post_json(
            &app,
            &format!(
                "/groupcontext/get?groupId={}&sessionId={}",
                fx.group_id, fx.session_id
            ),
            token_for(zhangsan),
            json!({"name": name, "scope_levels": levels}),
        )
        .await;
        assert_eq!(status, StatusCode::OK, "张三 get {name}: {body}");
        assert_eq!(
            body["items"].as_array().expect("items").len(),
            1,
            "张三 get {name}: {body}"
        );
        if name == "player_state" {
            assert_eq!(
                body["items"][0]["content"],
                "张三出局、李四存活、王五存活、赵六存活、钱七存活"
            );
        }
    }

    for name in ["player_word_2", "player_role"] {
        let (status, body) = post_json(
            &app,
            &format!(
                "/groupcontext/get?groupId={}&sessionId={}",
                fx.group_id, fx.session_id
            ),
            token_for(zhangsan),
            json!({"name": name, "scope_levels": ["session"]}),
        )
        .await;
        assert_eq!(status, StatusCode::FORBIDDEN, "张三 get {name}: {body}");
        assert_eq!(body["status"], "permission_denied");
    }
}

#[tokio::test]
async fn owner_lifecycle_walkthrough_transfers_immediate_management_rights() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = app_with_repo(repo.clone()).await;
    let uri = "/groupcontext/add?groupId=game-room-7&sessionId=sess-owners";

    let (status, body) = post_json(
        &app,
        uri,
        "judge-token",
        json!({
            "name": "owner_state",
            "scope_level": "session",
            "content": "v1",
            "visible_to": {"userIds": ["judge_bot"]},
            "collect_from": {"userIds": ["judge_bot", "admin"]},
            "owners": {"userIds": ["admin"]}
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "add owner_state: {body}");
    assert_eq!(body, json!({"status": "ok", "error_msg": null}));
    let active = repo
        .get_active("owner_state", "sess-owners")
        .await
        .expect("active row")
        .expect("owner_state");
    assert_eq!(active.content, "v1");
    assert_eq!(active.owners.user_ids, vec!["judge_bot"]);

    let (status, body) = post_json(
        &app,
        "/groupcontext/update?groupId=game-room-7&sessionId=sess-owners",
        "admin-token",
        json!({
            "name": "owner_state",
            "scope_level": "session",
            "content": "blocked",
            "owners": {"userIds": ["admin"]}
        }),
    )
    .await;
    assert_eq!(
        status,
        StatusCode::FORBIDDEN,
        "W-only cannot add itself: {body}"
    );
    assert_eq!(body["status"], "permission_denied");

    let (status, body) = post_json(
        &app,
        "/groupcontext/update?groupId=game-room-7&sessionId=sess-owners",
        "admin-token",
        json!({
            "name": "owner_state",
            "scope_level": "session",
            "content": "v2 by W-only",
            "change_reason": "ordinary writer"
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "W-only content update: {body}");
    let active = repo
        .get_active("owner_state", "sess-owners")
        .await
        .expect("active row")
        .expect("owner_state");
    assert_eq!(active.content, "v2 by W-only");
    assert_eq!(active.owners.user_ids, vec!["judge_bot"]);

    let (status, body) = post_json(
        &app,
        "/groupcontext/update?groupId=game-room-7&sessionId=sess-owners",
        "judge-token",
        json!({
            "name": "owner_state",
            "scope_level": "session",
            "content": "v3 transferred",
            "visible_to": {"userIds": ["李四"]},
            "collect_from": {"userIds": ["张三"]},
            "owners": {"userIds": ["admin"]},
            "change_reason": "transfer ownership"
        }),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "owner transfer: {body}");
    let active = repo
        .get_active("owner_state", "sess-owners")
        .await
        .expect("active row")
        .expect("owner_state");
    assert_eq!(active.content, "v3 transferred");
    assert_eq!(active.owners.user_ids, vec!["admin"]);

    let (status, body) = post_json(
        &app,
        "/groupcontext/list?groupId=game-room-7&sessionId=sess-owners",
        "admin-token",
        json!({"scope_levels": ["session"]}),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "new owner list: {body}");
    assert_eq!(body["contexts"][0]["name"], "owner_state");
    assert_eq!(body["contexts"][0]["permission"], "WRO");
    assert!(!body.to_string().contains("\"owners\""));

    let (status, body) = post_json(
        &app,
        "/groupcontext/get?groupId=game-room-7&sessionId=sess-owners",
        "admin-token",
        json!({"name": "owner_state", "scope_levels": ["session"]}),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "new owner get: {body}");
    assert_eq!(body["items"][0]["content"], "v3 transferred");
    assert_eq!(body["items"][0]["visible_to"], json!({"userIds": ["李四"]}));
    assert_eq!(
        body["items"][0]["collect_from"],
        json!({"userIds": ["张三"]})
    );
    assert_eq!(
        body["items"][0]["owners"],
        json!({"userIds": ["admin"]})
    );

    let (status, body) = post_json(
        &app,
        "/groupcontext/update?groupId=game-room-7&sessionId=sess-owners",
        "admin-token",
        json!({
            "name": "owner_state",
            "scope_level": "session",
            "content": "v4 by owner outside policies",
            "visible_to": {"userIds": ["李四"]},
            "collect_from": {"userIds": ["judge_bot"]},
            "owners": {"userIds": ["admin"]},
            "change_reason": "retain ordinary writer"
        }),
    )
    .await;
    assert_eq!(
        status,
        StatusCode::OK,
        "owner updates outside collect_from: {body}"
    );

    let (status, body) = post_json(
        &app,
        "/groupcontext/list?groupId=game-room-7&sessionId=sess-owners",
        "judge-token",
        json!({"scope_levels": ["session"]}),
    )
    .await;
    assert_eq!(
        status,
        StatusCode::OK,
        "former owner remains W-only: {body}"
    );
    assert_eq!(body["contexts"][0]["permission"], "W");

    let (status, body) = post_json(
        &app,
        "/groupcontext/get?groupId=game-room-7&sessionId=sess-owners",
        "judge-token",
        json!({"name": "owner_state", "scope_levels": ["session"]}),
    )
    .await;
    assert_eq!(
        status,
        StatusCode::FORBIDDEN,
        "former owner W has no read: {body}"
    );
    assert_eq!(body["status"], "permission_denied");

    let (status, body) = post_json(
        &app,
        "/groupcontext/update?groupId=game-room-7&sessionId=sess-owners",
        "judge-token",
        json!({
            "name": "owner_state",
            "scope_level": "session",
            "content": "v5 by retained W-only writer",
            "change_reason": "content-only update"
        }),
    )
    .await;
    assert_eq!(
        status,
        StatusCode::OK,
        "former owner content update: {body}"
    );

    let (status, body) = post_json(
        &app,
        "/groupcontext/update?groupId=game-room-7&sessionId=sess-owners",
        "judge-token",
        json!({
            "name": "owner_state",
            "scope_level": "session",
            "content": "blocked policy update",
            "visible_to": {"userIds": ["judge_bot"]}
        }),
    )
    .await;
    assert_eq!(
        status,
        StatusCode::FORBIDDEN,
        "former owner policy update: {body}"
    );
    assert_eq!(body["status"], "permission_denied");

    let active = repo
        .get_active("owner_state", "sess-owners")
        .await
        .expect("active row")
        .expect("owner_state");
    assert_eq!(active.content, "v5 by retained W-only writer");
    assert_eq!(active.owners.user_ids, vec!["admin"]);
    let versions = repo
        .list_versions(&active.context_id)
        .await
        .expect("versions");
    assert_eq!(versions.len(), 5);
    assert!(versions.iter().all(|entry| !entry.owners.is_empty()));
    assert_eq!(
        versions
            .iter()
            .find(|entry| !entry.is_superseded)
            .expect("active")
            .owners
            .user_ids,
        vec!["admin"]
    );
}

#[tokio::test]
async fn bot_cannot_manage_slots_but_can_use_a_defined_slot() {
    let repo = Arc::new(MemoryGroupContextRepo::new());
    let app = app_with_repo(repo.clone()).await;
    let list = "/groupcontext/list-slot?groupId=slot-group";
    let add = "/groupcontext/add?groupId=slot-group&sessionId=slot-group:12345678";
    for operation in ["add", "update", "delete"] {
        let (status, _) = post_json(
            &app, &format!("/groupcontext/{operation}-slot?groupId=slot-group"), token_for("admin"),
            json!({"name":"timeline"}),
        ).await;
        assert_eq!(status, StatusCode::FORBIDDEN);
    }
    repo.change_slot("slot-group", "human_owner",
        bcs_domain::group_context::GroupContextSlotChange::Add {
            name: "timeline".into(), description: String::new(),
        },
    ).await.unwrap();
    let (status, body) = post_json(&app, list, token_for("judge_bot"), json!({})).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["slots"][0]["creator"], "human_owner");

    let mut context = json!({
        "name":"timeline","scope_level":"session","content":"event",
        "visible_to":{"userIds":[]}
    });
    assert_eq!(post_json(&app, add, token_for("judge_bot"), context.clone()).await.0,
        StatusCode::BAD_REQUEST);
    context["slot"] = json!(true);
    assert_eq!(post_json(&app, add, token_for("judge_bot"), context).await.0, StatusCode::OK);

    let existing = repo.get_active("timeline", "slot-group:12345678").await.unwrap().unwrap();
    assert!(existing.is_slot);
    assert_eq!(existing.content, "event");
}

struct SlotTestIdentity;

#[async_trait::async_trait]
impl bcs_http::state::UserIdentityPort for SlotTestIdentity {
    async fn extract(
        &self,
        _: &axum::http::HeaderMap,
        _: &axum::http::Uri,
    ) -> Option<bcs_http::state::HttpUserIdentity> {
        Some(bcs_http::state::HttpUserIdentity {
            staff_no: Some("slot-owner".into()),
            nick_name: None,
        })
    }

    async fn ensure_identity(
        &self, _: &str, _: &str, _: Option<&str>, _: Option<&str>, _: &str,
    ) -> Result<String, bcs_auth_api::AuthError> {
        panic!("slot management must not create an identity")
    }

    async fn get_identity_by_token(
        &self, _: &str,
    ) -> Result<Option<bcs_auth_api::UserIdentityInfo>, bcs_auth_api::AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(
        &self, _: &str,
    ) -> Result<Option<bcs_auth_api::UserIdentityInfo>, bcs_auth_api::AuthError> {
        Ok(None)
    }
}

#[tokio::test]
async fn human_slot_management_routes_persist_changes_and_report_validation_errors() {
    use bcs_service_api::{Group, GroupCoreService, Participant, ParticipantRole};

    let repo = Arc::new(MemoryGroupContextRepo::new());
    let authority = bcs_test_support::authority_edge_fixture();
    bcs_test_support::grant_owner_edge(&authority.repo, "slot-driver", "slot-owner").await;
    let groups = Arc::new(bcs_group::GroupCore::with_repo(Arc::new(bcs_group::MemoryGroupRepo::new())));
    groups.upsert(Group::new(
        "slot-group", "slot-driver", vec![Participant::bot("slot-driver", ParticipantRole::Driver)],
    )).await.unwrap();
    let service = bcs_app_session::GroupContextAddAccessGuard::new(
        Arc::new(GroupContextApplicationServiceImpl::new(repo.clone())),
        groups,
        Arc::new(bcs_session_store::MemorySessionRepo::new()),
        authority.hook,
    );
    let services = Services::builder().group_context(Arc::new(service)).build_for_test();
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(SlotTestIdentity)));
    let post = |uri: &str, body: Value| {
        let request = Request::post(uri).header("content-type", "application/json")
            .body(Body::from(body.to_string())).unwrap();
        app.clone().oneshot(request)
    };

    let add = "/groupcontext/add-slot?groupId=slot-group";
    let update = "/groupcontext/update-slot?groupId=slot-group";
    let delete = "/groupcontext/delete-slot?groupId=slot-group";
    for (uri, body) in [
        ("/groupcontext/add-slot", json!({"name":"timeline"})),
        (add, json!({})),
    ] {
        assert_eq!(post(uri, body).await.unwrap().status(), StatusCode::BAD_REQUEST);
    }

    for (uri, body) in [
        (add, json!({"name":"timeline"})),
        (update, json!({"name":"timeline","new_name":"events","description":"Key events"})),
    ] {
        let response = post(uri, body).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response_json(response).await["status"], "ok");
    }
    let slots = repo.list_slots("slot-group").await.unwrap();
    assert_eq!(slots.len(), 1);
    assert_eq!(slots[0].name, "events");
    assert_eq!(slots[0].description, "Key events");
    assert_eq!(slots[0].creator, "human_slot-owner");
    assert_eq!(slots[0].modifier, "human_slot-owner");
    assert!(slots[0].gmt_modified >= slots[0].gmt_create);

    let body = json!({"name":"events"});
    assert_eq!(post(delete, body.clone()).await.unwrap().status(), StatusCode::OK);
    assert!(repo.list_slots("slot-group").await.unwrap().is_empty());
    assert_eq!(post(delete, body).await.unwrap().status(), StatusCode::NOT_FOUND);
}
