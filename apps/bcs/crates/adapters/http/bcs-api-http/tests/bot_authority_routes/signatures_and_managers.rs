use super::*;

// ---------------------------------------------------------------------------
// 5.5/5.6 route cases
// ---------------------------------------------------------------------------

#[tokio::test]
async fn every_authority_route_requires_a_verified_signature() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[]).await;
    let token = human_token(OWNER);

    // Without a principal header: 401 on a sampling of the
    // new method/path combos.
    let cases = [
        ("GET", "/api/v1/collaboration/bots/bot_sync/managers", None),
        (
            "PUT",
            "/api/v1/collaboration/bots/bot_sync/managers",
            None,
        ),
        (
            "PUT",
            "/api/v1/collaboration/bots/bot_sync/managers/bob",
            None,
        ),
        (
            "DELETE",
            "/api/v1/collaboration/bots/bot_sync/managers/bob",
            None,
        ),
        (
            "GET",
            "/api/v1/collaboration/bots/bot_sync/ownership",
            None,
        ),
        (
            "PUT",
            "/api/v1/collaboration/bots/bot_sync/ownership",
            None,
        ),
        (
            "PUT",
            &format!("/api/v1/collaboration/bots/bot_transfer/ownership-transfers/{TARGET_ACTOR}"),
            None,
        ),
        (
            "GET",
            &format!("/api/v1/collaboration/bots/bot_transfer/ownership-transfers/{TARGET_ACTOR}"),
            None,
        ),
        (
            "POST",
            &format!("/api/v1/collaboration/bots/bot_transfer/ownership-transfers/{TARGET_ACTOR}/accept"),
            None,
        ),
        (
            "POST",
            &format!("/api/v1/collaboration/bots/bot_transfer/ownership-transfers/{TARGET_ACTOR}/reject"),
            None,
        ),
        (
            "POST",
            &format!("/api/v1/collaboration/bots/bot_transfer/ownership-transfers/{TARGET_ACTOR}/cancel"),
            None,
        ),
        (
            "GET",
            "/api/v1/collaboration/ownership-transfers?direction=received",
            None,
        ),
        // With a valid signature the same requests pass the
        // verification boundary (identity decisions then apply).
        ("GET", "/api/v1/collaboration/bots/bot_sync/managers", Some(token.as_str())),
    ];
    for (method, uri, token) in cases {
        let response = fixture
            .router
            .clone()
            .oneshot(signed_request(method, uri, token))
            .await
            .expect("response");
        let status = response.status();
        if token.is_none() {
            assert_eq!(
                status,
                StatusCode::UNAUTHORIZED,
                "{method} {uri} without a principal must be 401"
            );
        } else if !status.is_success() {
            panic!("{method} {uri} with a valid signature reached {status}");
        }
    }
}
#[tokio::test]
async fn expired_tampered_and_wrong_audience_signatures_are_unauthenticated() {
    let (fixture, _) = build_state(true).await;
    let cases = [
        (
            "expired",
            expired_human_token(OWNER),
        ),
        (
            "tampered",
            tampered_human_token(OWNER),
        ),
        (
            "wrong-audience",
            wrong_audience_human_token(OWNER),
        ),
    ];
    for (label, token) in cases {
        let response = fixture
            .router
            .clone()
            .oneshot(signed_request(
                "GET",
                "/api/v1/collaboration/bots/bot_sync/managers",
                Some(&token),
            ))
            .await
            .expect("response");
        assert_eq!(
            response.status(),
            StatusCode::UNAUTHORIZED,
            "{label} signature must stay unauthenticated"
        );
        let body = response_json(response).await;
        assert_eq!(error_code(&body), "unauthenticated");
    }
}
#[tokio::test]
async fn sync_body_requires_the_field_and_rejects_null() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[MANAGER]).await;
    let token = app_token(APP_ID, Some(TENANT));

    for body in [json!({}), json!({"manager_user_ids": null})] {
        let response = fixture
            .router
            .clone()
            .oneshot(signed_json_request(
                "PUT",
                &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
                &token,
                body,
            ))
            .await
            .expect("response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert_eq!(error_code(&response_json(response).await), "invalid_request");
    }

    // The manager collection is untouched.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(
        response_json(response).await["data"]["items"],
        Value::Array(vec![Value::String(MANAGER.to_string())])
    );
}
#[tokio::test]
async fn human_owner_lists_managers_with_the_owner_summary() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[MANAGER]).await;
    let token = human_token(OWNER);

    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["code"], 20_000);
    assert_eq!(body["data"]["owner"]["owner_user_id"], OWNER);
    assert_eq!(
        body["data"]["owner"]["owner_actor_id"],
        format!("human_{OWNER}")
    );
    assert_eq!(body["data"]["items"], Value::Array(vec![MANAGER.into()]));
    assert_eq!(body["data"]["total"], 1);

    // A Human with no relation is denied (403), never leaked the page.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&human_token(STRANGER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}
#[tokio::test]
async fn app_sync_replaces_the_manager_collection() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &["legacy_mgr"]).await;
    seed_humans(&repo, &[MANAGER, "dave"]).await;
    let token = app_token(APP_ID, Some(TENANT));

    let response = fixture
        .router
        .clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            &token,
            json!({"manager_user_ids": [MANAGER, MANAGER, "dave".to_string()]}),
        ))
        .await
        .expect("response");
    assert_eq!(
        response.status(),
        StatusCode::OK,
        "sync through the collection PUT"
    );
    let body = response_json(response).await;
    assert_eq!(body["data"]["added"], 2);
    assert_eq!(body["data"]["removed"], 1);
    assert_eq!(body["data"]["total"], 2);

    // sync never removes the owner, only managers: the owner
    // still reads the final page.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["owner"]["owner_user_id"], OWNER);
    let items = body["data"]["items"].as_array().expect("items");
    assert_eq!(items.len(), 2);

    // Re-submitting the same snapshot reports a zero diff.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            &token,
            json!({"manager_user_ids": [MANAGER, "dave".to_string()]}),
        ))
        .await
        .expect("response");
    let body = response_json(response).await;
    assert_eq!(body["data"]["added"], 0);
    assert_eq!(body["data"]["removed"], 0);
}
#[tokio::test]
async fn human_caller_is_rejected_from_the_collection_replace() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[]).await;

    let response = fixture
        .router
        .clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            &human_token(OWNER),
            json!({"manager_user_ids": [MANAGER]}),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let body = response_json(response).await;
    assert_eq!(
        error_code(&body),
        "bot_authority_operator_forbidden"
    );
}
#[tokio::test]
async fn put_single_user_id_sync_adds_that_user_only() {
    // The plan-pinned trap: a legal user id `sync` must be a
    // single-item add, never a collection replace, because the
    // surface deliberately has no /managers/sync sub-path.
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &["existing_mgr"]).await;
    seed_humans(&repo, &["sync"]).await;
    let token = human_token(OWNER);

    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/sync"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["added"], 1);
    assert_eq!(body["data"]["removed"], 0);

    // The pre-existing manager survived; the user id `sync` is
    // now a manager; the owner is untouched.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&token),
        ))
        .await
        .expect("response");
    let body = response_json(response).await;
    let mut items = body["data"]["items"]
        .as_array()
        .expect("items")
        .iter()
        .map(|v| v.as_str().expect("string").to_string())
        .collect::<Vec<_>>();
    items.sort();
    assert_eq!(items, vec!["existing_mgr", "sync"]);
    assert_eq!(body["data"]["owner"]["owner_user_id"], OWNER);
}
#[tokio::test]
async fn manager_single_add_remove_round_trip_and_owner_target_409() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[MANAGER]).await;
    let token = human_token(OWNER);

    // Adding the owner through the manager endpoint: 409
    // owner_not_manager (spec §5.1).
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{OWNER}"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert_eq!(error_code(&body), "owner_not_manager");

    // The manager can manage managers too; grant and revoke.
    // Unknown Human targets fail the whole request (400, no
    // partial write).
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{MANAGER}"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(
        response.status(),
        StatusCode::OK,
        "re-adding an existing manager succeeds"
    );

    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "DELETE",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{MANAGER}"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    // Removing an absent manager is a success (spec §5.1).
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "DELETE",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{MANAGER}"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
}
#[tokio::test]
async fn single_item_edits_are_human_only() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[]).await;
    let app = app_token(APP_ID, Some(TENANT));

    for method in ["PUT", "DELETE"] {
        let response = fixture
            .router
            .clone()
            .oneshot(signed_request(
                method,
                &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{MANAGER}"),
                Some(&app),
            ))
            .await
            .expect("response");
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
    }
}
#[tokio::test]
async fn owner_checks_and_unauthorized_humans_on_dual_read() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[]).await;
    seed_transfer_row(&repo, BOT_TRANSFER, OWNER, TARGET_HUMAN).await;

    // The current-round initiator may read (dual admission,
    // Human path).
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["status"], "pending");
    assert_eq!(body["data"]["target_owner_actor_id"], TARGET_ACTOR);
    assert_eq!(body["data"]["from_owner_actor_id"], format!("human_{OWNER}"));

    // The target Human may read.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}"),
            Some(&human_token(TARGET_HUMAN)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    // An unrelated Human is denied.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}"),
            Some(&human_token("someone-else")),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // A scoped app reads via the SAME handler.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}"),
            Some(&app_token(APP_ID, Some(TENANT))),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    // No configured target set anymore: a scoped App reaches
    // the handler for any pending row — only existence divides
    // read access (missing pair = 404, spec §6.1/§6.4).
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{ANOTHER_ACTOR}"),
            Some(&app_token(APP_ID, Some(TENANT))),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::NOT_FOUND);

    // Unknown (bot, target) pairs are 404 with the pinned code.
    let response = fixture
        .router
        .clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{ANOTHER_ACTOR}"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let body = response_json(response).await;
    assert_eq!(error_code(&body), "bot_transfer_not_found");
}
