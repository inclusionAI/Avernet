use super::*;

#[tokio::test]
async fn initiation_and_decision_flows_through_http() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[TARGET_HUMAN, STRANGER]).await;
    let owner = human_token(OWNER);
    let target = human_token(TARGET_HUMAN);
    let app = app_token(APP_ID, Some(TENANT));
    let base = format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}");

    // The current owner initiates: 201.
    let response = fixture.router.clone()
        .oneshot(signed_request("PUT", &base, Some(&owner)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::CREATED, "new row");
    let body = response_json(response).await;
    assert_eq!(body["data"]["status"], "pending");

    // Repeat from the same owner: refresh/reuse returns 200.
    let response = fixture.router.clone()
        .oneshot(signed_request("PUT", &base, Some(&owner)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK, "reuse/refresh returns 200");

    // A different target's pending blocks a new initiation: 409
    // transfer_pending (seed a second row is NOT possible; the
    // unique-pending rule enforces it — attempt another target).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{ANOTHER_ACTOR}"),
            Some(&owner),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::CONFLICT);
    let body = response_json(response).await;
    assert_eq!(error_code(&body), "transfer_pending");

    // Non-target Human cannot accept.
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&human_token(STRANGER))))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // Initiation is Human-only: the App scope has no initiate
    // action even for scoped targets.
    let response = fixture.router.clone()
        .oneshot(signed_request("PUT", &base, Some(&app)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // Target Human accepts: 200; the row shows accepted and the
    // owner flipped.
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&target)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["status"], "accepted");
    assert_eq!(body["data"]["decided_by_kind"], "human");

    // Repeat accept on the same terminal row: 200 unchanged.
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&target)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    // A different decision on the terminal row: 409.
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/reject"), Some(&target)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::CONFLICT);
    assert_eq!(error_code(&response_json(response).await), "transfer_state_conflict");

    // Ownership flipped to the target; the old owner became a
    // manager (visible through the manager list).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership"),
            Some(&human_token(TARGET_HUMAN)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["owner_user_id"], TARGET_HUMAN);
}
#[tokio::test]
async fn scoped_app_accepts_and_the_decision_is_recorded_as_service() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[]).await;
    seed_humans(&repo, &[TARGET_HUMAN]).await;
    seed_transfer_row(&repo, BOT_TRANSFER, OWNER, TARGET_HUMAN).await;
    let app = app_token(APP_ID, Some(TENANT));
    let base = format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}");

    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&app)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["status"], "accepted");
    assert_eq!(body["data"]["decided_by_kind"], "service");

    // The app then reads the row it decided through the same
    // scoped GET.
    let response = fixture.router.clone()
        .oneshot(signed_request("GET", &base, Some(&app)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response_json(response).await["data"]["status"], "accepted");
}
#[tokio::test]
async fn inbox_is_human_only_and_pagination_bounds_are_enforced() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[]).await;
    let app = app_token(APP_ID, Some(TENANT));

    // Inbox/outbox is a Human rule: the App scope has no action
    // for it.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/ownership-transfers?direction=received",
            Some(&app),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // limit 0 and over-the-max are bad requests.
    for limit in ["0", "101"] {
        let response = fixture.router.clone()
            .oneshot(signed_request(
                "GET",
                &format!("/api/v1/collaboration/ownership-transfers?direction=received&limit={limit}"),
                Some(&human_token(OWNER)),
            ))
            .await
            .expect("response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "limit {limit}");
    }
    // An unknown status filter is a bad request.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/ownership-transfers?direction=received&status=vibes",
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    // An unknown direction is a bad request.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/ownership-transfers?direction=sideways",
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);

    // Wait — direction=sideways is caught by dto; the empty
    // inbox page still works for a Human.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/ownership-transfers?direction=sent",
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["items"], Value::Array(vec![]));
    assert_eq!(body["data"]["total"], 0);
}
/// The `status` filter grammar (bot-authority.yaml: an
/// OpenAPI array on `GET /ownership-transfers`) accepts
/// the documented REPEATED form plus a single value and
/// a comma-delimited list — a native `Query` extractor
/// rejected every legal form with "expected a
/// sequence", so the route parses the raw query.
#[tokio::test]
async fn transfer_status_filter_accepts_single_repeated_and_csv_forms() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[]).await;
    // One PENDING transfer row (expires now + 7d, so
    // the effective status is still `pending`).
    seed_transfer_row(&repo, BOT_TRANSFER, OWNER, "bob").await;

    let uri_for = |query: &str| {
        format!(
            "/api/v1/collaboration/ownership-transfers?direction=sent&{query}"
        )
    };

    // Single value: the pending row matches.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &uri_for("status=pending"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("single status response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["total"], 1);
    assert_eq!(body["data"]["items"][0]["status"], "pending");

    // A single VALID non-matching status narrows the
    // page to empty (and proves the filter applies).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &uri_for("status=accepted"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("non-matching response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["total"], 0);
    assert_eq!(body["data"]["items"], Value::Array(vec![]));

    // Repeated form (`status=pending&status=accepted`,
    // the documented OpenAPI array) — previously a
    // HARD 400 "expected a sequence".
    for query in [
        "status=pending&status=cancelled",
        "status=accepted,pending",
        "status=pending,%20accepted",
        "status=accepted&status=pending,cancelled",
    ] {
        let response = fixture.router.clone()
            .oneshot(signed_request(
                "GET",
                &uri_for(query),
                Some(&human_token(OWNER)),
            ))
            .await
            .expect("repeated status response");
        assert_eq!(response.status(), StatusCode::OK, "{query}");
        let body = response_json(response).await;
        assert_eq!(body["data"]["total"], 1, "{query}");
        assert_eq!(body["data"]["items"][0]["status"], "pending", "{query}");
    }

    // Every status value without an inviting row in
    // it filters to the empty page again (repeated).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &uri_for("status=rejected&status=expired"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("no default");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["total"], 0);

    // No status → the unfiltered (default) page.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &uri_for(""),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("default response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["total"], 1);

    // Unknown status spelling stays a 400 with the
    // pinned `invalid_request` code.
    for query in [
        "status=vibes",
        "status=pending&status=vibes",
        "status=pending,",
    ] {
        let response = fixture.router.clone()
            .oneshot(signed_request(
                "GET",
                &uri_for(query),
                Some(&human_token(OWNER)),
            ))
            .await
            .expect("invalid status response");
        assert_eq!(response.status(), StatusCode::BAD_REQUEST, "{query}");
        let body = response_json(response).await;
        assert_eq!(error_code(&body), "invalid_request", "{query}");
    }

    // Unknown query parameters keep the historical
    // deny (the former `deny_unknown_fields` shape).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &uri_for("bogus=1"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("unknown parameter response");
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}
#[tokio::test]
async fn invite_code_gate_stays_on_human_paths_without_app_exemption() {
    let (fixture, repo) = build_state(false).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[]).await;

    // An un-invite-coded Human on authority routes is rejected
    // by the Application-level gate (403 invite_code_required).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        error_code(&response_json(response).await),
        "invite_code_required"
    );
    assert_eq!(fixture.invite.human_checks.lock().expect("gate").len(), 1);

    // A trusted App on the SAME dual/sync routes is never
    // treated as an un-invite-coded Human.
    let response = fixture.router.clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            &app_token(APP_ID, Some(TENANT)),
            json!({"manager_user_ids": []}),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(fixture.invite.human_checks.lock().expect("gate").len(), 1);
}
#[tokio::test]
async fn existing_routes_gain_no_app_exemption() {
    let (fixture, _) = build_state(false).await;

    // A normal protected collaboration route stays Human-rule:
    // an App-only credential must not gain access through the
    // new protected-only layering.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/templates",
            Some(&app_token(APP_ID, Some(TENANT))),
        ))
        .await
        .expect("response");
    // The App-only credential does NOT gain the template
    // route: the normal protected router keeps its
    // always-Human gate and the route fails it closed.
    assert_eq!(
        response.status(),
        StatusCode::FORBIDDEN,
        "App callers must not gain the template route"
    );

    // The invite-gate middleware still runs on normal protected
    // routes: signature-verified Humans without invite codes are
    // rejected there (the middleware's own denial).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            "/api/v1/collaboration/templates",
            Some(&human_token(OWNER)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        error_code(&response_json(response).await),
        "invite_code_required"
    );
}
#[tokio::test]
async fn user_id_casing_is_byte_exact_at_the_boundary() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_SYNC, OWNER, &[]).await;
    seed_humans(&repo, &[MANAGER]).await;
    let token = human_token(OWNER);

    // Only the exact-case user id grants/ungrants; the
    // wrong-case variant never touches "Bob".
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/Bob"),
            Some(&token),
        ))
        .await
        .expect("response");
    // "Bob" is unknown in the memory Human registry → the whole
    // request fails (400), never silently creating an identity.
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        error_code(&response_json(response).await),
        "bot_authority_human_unknown"
    );

    let response = fixture.router.clone()
        .oneshot(signed_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers/{MANAGER}"),
            Some(&token),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);

    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &format!("/api/v1/collaboration/bots/{BOT_SYNC}/managers"),
            Some(&token),
        ))
        .await
        .expect("response");
    let items = response_json(response).await["data"]["items"].clone();
    assert_eq!(items, Value::Array(vec![Value::String(MANAGER.to_string())]));
    // The wrong-case subject was never added.
    assert!(!items.to_string().contains("Bob"), "{items}");
}
// ---------------------------------------------------------------------------
// plan Review Focus #1 pins (User+App simultaneous credentials)
// ---------------------------------------------------------------------------

#[tokio::test]
async fn dual_credential_human_rejection_never_falls_back_to_app() {
    let (fixture, repo) = build_state(true).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[]).await;
    seed_humans(&repo, &[TARGET_HUMAN]).await;
    let dual = dual_token(OWNER, APP_ID);

    // The current owner carries BOTH identities. On the
    // dual-admission accept route the Human is processed
    // FIRST: alice is not the target, the Human rule
    // rejects, and the App scope MUST NOT be consumed as a
    // fallback (the scope would accept).
    let base = format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}");
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "PUT",
            &base,
            Some(&dual),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::CREATED);
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&dual)))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        error_code(&response_json(response).await),
        "bot_authority_operator_forbidden"
    );

    // The row is untouched: no App-side decision was
    // recorded anywhere.
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &base,
            Some(&human_token(TARGET_HUMAN)),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response_json(response).await["data"]["status"], "pending");
    // A Human-only alice credential on the same accept
    // rejects identically (the dual projection never
    // matters to the outcome).
    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&human_token(OWNER))))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::FORBIDDEN);

    // The same dual credential on the collection PUT IS
    // processed as the App identity explicitly (spec §6.1:
    // sync 始终显式选择 App 身份): the empty snapshot
    // commits as the App mutation, and a repeated
    // snapshot reports the zero diff.
    let response = fixture.router.clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/managers"),
            &dual,
            json!({"manager_user_ids": []}),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["data"]["removed"], 0);
    assert_eq!(body["data"]["total"], 0);
    let response = fixture.router.clone()
        .oneshot(signed_json_request(
            "PUT",
            &format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/managers"),
            &dual,
            json!({"manager_user_ids": [TARGET_HUMAN]}),
        ))
        .await
        .expect("response");
    let body = response_json(response).await;
    assert_eq!(body["data"]["added"], 1);
    assert_eq!(body["data"]["total"], 1);
}
#[tokio::test]
async fn un_invite_coded_dual_caller_cannot_accept() {
    let (fixture, repo) = build_state(false).await;
    seed_bot(&repo, BOT_TRANSFER, OWNER, &[TARGET_HUMAN]).await;
    seed_transfer_row(&repo, BOT_TRANSFER, OWNER, TARGET_HUMAN).await;
    let dual = dual_token(TARGET_HUMAN, APP_ID);
    let base = format!("/api/v1/collaboration/bots/{BOT_TRANSFER}/ownership-transfers/{TARGET_ACTOR}");

    let response = fixture.router.clone()
        .oneshot(signed_request("POST", &format!("{base}/accept"), Some(&dual)))
        .await
        .expect("response");
    // The Human is processed first: the invite gate
    // rejects and there is no App fallback.
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        error_code(&response_json(response).await),
        "invite_code_required"
    );
    assert_eq!(fixture.invite.human_checks.lock().expect("gate").len(), 1);

    // Nothing was decided: the scoped App reads the
    // same row (the App branch is unaffected by the
    // Human invite gate).
    let response = fixture.router.clone()
        .oneshot(signed_request(
            "GET",
            &base,
            Some(&app_token(APP_ID, Some(TENANT))),
        ))
        .await
        .expect("response");
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response_json(response).await["data"]["status"], "pending");
}
