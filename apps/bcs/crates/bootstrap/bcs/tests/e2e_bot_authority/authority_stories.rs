use super::*;

/// The ideographic story (plan §8.1): registration →
/// manager → business op → App sync → transfer chain →
/// owner revocation fallout → second transfer →
/// retirement.
#[tokio::test]
async fn e2e_bot_authority_full_chain_story() {
    let (_temp_dir, data_dir) = create_temp_dir();
    let mut pm = ProcessManager::new();
    let port = pm.start_bcs(&data_dir).await.expect("start phase A");
    let mut bcs_url = format!("http://127.0.0.1:{port}");
    println!("[Phase A] BCS on {bcs_url}");

    let client = reqwest::Client::new();

    // ── 1. Trusted registration (v1 Human token) ─────────────────────
    let (bot_id, bot_token) =
        register_trusted_bot(&client, &bcs_url, WALT, "Story Bot").await;
    println!("[1] trusted bot registered: {bot_id} (owner {WALT})");

    // The target/secondary Humans become KNOWN through
    // their own trusted registrations (the Backend-side
    // human pre-seed requirement in production; here the
    // same register chain seeds `human_bob`/`human_carol`).
    let (bob_bot_id, _) =
        register_trusted_bot(&client, &bcs_url, BOB, "Bob Bot").await;
    let (carol_bot_id, _) =
        register_trusted_bot(&client, &bcs_url, CAROL, "Carol Bot").await;
    let _ = (bob_bot_id, carol_bot_id);

    // ── Memory/chain read: the SAME committed record the
    //    registration wrote is observable through the
    //    runtime-injected authority chain (memory-mode
    //    same-record pin = 4.3a's memory boats; here at
    //    process level).
    let (status, page) =
        read_managers(&client, &bcs_url, &human_token(WALT), &bot_id).await;
    assert_eq!(status, 200, "owner reads the manager page: {page:?}");
    assert_eq!(owner_user_of(&page), WALT, "registration owner readable");
    println!("[1] registration-committed owner readable via runtime chain");

    // ── Chain-level fault injection (cross-references the
    //    4b four-cell suites): expired / tampered Gateway
    //    JWT through the real verifier → 401.
    for (label, token) in [
        ("expired", expired_human_token(WALT)),
        ("tampered", tampered_human_token(WALT)),
    ] {
        let (status, _) =
            read_managers(&client, &bcs_url, &token, &bot_id).await;
        assert_eq!(status, 401, "chain-level {label} JWT must 401");
    }
    println!("[1] expired/tampered JWT → 401 through the real verifier");

    // ── 2. Add manager (single PUT, owner Human) ─────────────────────
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!("/api/v1/collaboration/bots/{bot_id}/managers/{BOB}"),
        &human_token(WALT),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "manager add: {text}");
    println!("[2] manager {BOB} added");

    // ── 3. The manager performs a business op on the Bot's
    //    behalf (workbench chat.send as the Bot), and the
    //    real Run producer frames flow to the manager's
    //    protected socket.
    let group = client
        .post(&format!("{bcs_url}/groups"))
        .header("Authorization", format!("Bearer {bot_token}"))
        .json(&json!({
            "driver_bot": bot_id,
            "participants": [
                { "bot_uuid": bot_id, "role": "driver" }
            ],
            "label": "Authority Story Group"
        }))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("group create request");
    assert!(
        group.status().is_success(),
        "group creation must succeed: {}",
        group.status()
    );
    let group_id = group.json::<Value>().await.expect("group body")["id"]
        .as_str()
        .expect("group id")
        .to_string();

    // The Bot's REAL producer connection (path pin: the
    // workbench run chain needs the bot's connected
    // uplink, exactly like `e2e_workbench`).
    let mut bot_ws = connect_bot_socket(port, &bot_token).await;
    let _ = &mut bot_ws;

    // The owner seat still connects to the Bot's own
    // group before the authority story proceeds.
    let mut walt_socket = WorkbenchClient::connect(port, WALT).await;
    let walt_connect = walt_socket.subscribe_group(&group_id).await;
    assert!(
        walt_connect["ok"].as_bool().unwrap_or(false),
        "the owner may view the Bot's group: {walt_connect}"
    );
    let owner_chat = walt_socket
        .chat_as_bot_outcome(&group_id, &bot_id, "Story Bot", "from the owner seat")
        .await;
    assert!(
        owner_chat.is_none() || owner_chat.as_deref() == Some(""),
        "owner-as-bot chat.send must pass authorization: {owner_chat:?}"
    );
    walt_socket.close().await;

    let mut bob_socket = WorkbenchClient::connect(port, BOB).await;
    let connect_ok = bob_socket.subscribe_group(&group_id).await;
    assert!(
        connect_ok["ok"].as_bool().unwrap_or(false),
        "the current manager may view the Bot's group: {connect_ok}"
    );
    let sent = bob_socket
        .chat_as_bot_outcome(
            &group_id,
            &bot_id,
            "Story Bot",
            "worked from the manager seat",
        )
        .await;
    assert!(
        sent.is_none() || sent.as_deref() == Some(""),
        "manager-as-bot chat.send must pass authorization (can_manage): {sent:?}"
    );
    println!("[3] manager business op authorized at the WS boundary");
    bob_socket.close().await;

    // ── 4. Restart with the trusted-app scope rule for the
    //    App NAME (byte-exact match on the verified
    //    principal's app name; the rule covers every bot on
    //    the instance), then prove durability across the
    //    restart.
    pm.kill_all();
    let rule_bots = trusted_apps_rule();
    let port = {
        let extra = json!({
            "bot_authority": { "trusted_apps": [rule_bots] }
        });
        start_with_extra(&mut pm, &data_dir, Some(extra)).await
    };
    bcs_url = format!("http://127.0.0.1:{port}");
    println!("[4] server restarted with trusted_app rule; {bcs_url}");

    let (status, page) =
        read_managers(&client, &bcs_url, &human_token(WALT), &bot_id).await;
    assert_eq!(status, 200, "post-restart owner read: {page:?}");
    assert_eq!(owner_user_of(&page), WALT, "owner edge survived restart");

    // ── 5. Trusted-App scope: rule enumeration ───────────────────────
    // Wrong tenant → deny; an unknown App NAME → deny (the
    // scoping identity is the name now, not a numeric id);
    // an action outside `actions` (ownership:read not
    // granted) → deny.
    let (status, body) =
        read_managers(&client, &bcs_url, &app_token(APP_ID, Some("other")), &bot_id)
            .await;
    assert_eq!(
        status, 403,
        "App outside the trusted rule tenant must be denied: {body:?}"
    );
    let (status, body) = read_managers(
        &client,
        &bcs_url,
        &named_app_token(99, "Other App", Some(TENANT)),
        &bot_id,
    )
    .await;
    assert_eq!(
        status, 403,
        "an untrusted app name is denied: {body:?}"
    );
    let (status, body) = read_managers(
        &client,
        &bcs_url,
        &app_token(APP_ID, Some(TENANT)),
        &bot_id,
    )
    .await;
    assert_eq!(
        status, 403,
        "the sync-only rule denies ownership reads: {body:?}"
    );
    println!("[5] trusted-app scope denials pinned");

    // ── 6. Trusted App `managers:sync`: full-set replace ─────────────
    // The App replaces the whole manager set: the manual
    // manager {BOB} goes away, {CAROL} becomes the set;
    // the owner is NOT a sync operand (keeping {WALT}
    // away proves it).
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!("/api/v1/collaboration/bots/{bot_id}/managers"),
        &app_token(APP_ID, Some(TENANT)),
        Some(json!({ "manager_user_ids": [CAROL] })),
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "App sync: {text}");
    let (_, page) =
        read_managers(&client, &bcs_url, &human_token(WALT), &bot_id).await;
    assert_eq!(owner_user_of(&page), WALT, "sync never touches the owner");
    assert!(
        !manager_ids(page.clone()).contains(&BOB.to_string()),
        "the manual manager was replaced away: {page:?}"
    );
    assert!(
        manager_ids(page).contains(&CAROL.to_string()),
        "the synced set is the requested one"
    );
    println!("[6] App full-set sync applied (set = [{CAROL}])");

    // ── 7. Ownership transfer: initiate → refresh → accept ───────────
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!(
            "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/human_{BOB}"
        ),
        &human_token(WALT),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 201, "new transfer row → 201: {text}");

    // Refresh (renew) of the same pending round → 200.
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!(
            "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/human_{BOB}"
        ),
        &human_token(WALT),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "reuse/refresh → 200: {text}");

    // The target Human accepts.
    let resp = collaboration(
        &client,
        &bcs_url,
        "POST",
        &format!(
            "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/human_{BOB}/accept"
        ),
        &human_token(BOB),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "target accept: {text}");

    let (_, page) =
        read_managers(&client, &bcs_url, &human_token(BOB), &bot_id).await;
    assert_eq!(owner_user_of(&page), BOB, "BOB is the new owner");
    assert!(
        manager_ids(page.clone()).contains(&WALT.to_string()),
        "the ORIGINAL owner is now a plain manager: {page:?}"
    );
    println!("[7] transfer initiated→renewed→accepted; owner = {BOB}");

    // ── 8. Sync removing the ORIGINAL owner (a plain
    //    manager now): ONLY the Bot-bound entries for
    //    {WALT} fail after this.
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!("/api/v1/collaboration/bots/{bot_id}/managers"),
        &app_token(APP_ID, Some(TENANT)),
        Some(json!({ "manager_user_ids": [CAROL] })),
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "App sync removing {WALT}: {text}");

    // Bot-bound HTTP (contract-pinned reads): the
    // revoked-ex-owner Human loses the 403-no-edge bot
    // read (compat note GET /bots/{id}).
    let resp = client
        .get(&format!("{bcs_url}/bots/{bot_id}"))
        .header("X-Mock-User-Id", WALT)
        .header("X-Mock-Nick-Name", WALT)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("bot read");
    assert_eq!(
        resp.status().as_u16(),
        403,
        "revoked-ex-owner no longer reads the Bot: {}",
        resp.text().await.unwrap_or_default()
    );
    let (status, _) =
        read_managers(&client, &bcs_url, &human_token(WALT), &bot_id).await;
    assert_eq!(
        status, 403,
        "the ex-manager loses the authority manager read"
    );
    // ... while an in-set manager keeps management reads.
    let (status, _) =
        read_managers(&client, &bcs_url, &human_token(CAROL), &bot_id).await;
    assert_eq!(status, 200, "the in-set manager keeps management reads");

    // Bot-bound WS/application boundary: the revoked
    // Human fails BOTH group-view and sender control
    // (Row 14 consults the CURRENT edge — the same
    // consult the protected delivery writer does per
    // frame in 7b's socket suites).
    let mut bot_ws_b = connect_bot_socket(port, &bot_token).await;
    let _ = &mut bot_ws_b;
    let mut walt_socket = WorkbenchClient::connect(port, WALT).await;
    let connect_view = walt_socket.subscribe_group(&group_id).await;
    let walt_denied = !connect_view["ok"].as_bool().unwrap_or(true);
    let walt_chat = walt_socket
        .chat_as_bot_outcome(&group_id, &bot_id, "Story Bot", "from the ex-owner seat")
        .await;
    let chat_denied = walt_chat
        .as_deref()
        .is_some_and(|code| code == "forbidden_sender");
    assert!(
        walt_denied || chat_denied,
        "the revoked-ex-owner must lose the Bot-bound workbench op \
         (connect: {connect_view}, chat: {walt_chat:?})"
    );
    walt_socket.close().await;

    // ... but the CURRENT owner's business op keeps
    // passing authorization (only the revoked Human is
    // impacted).
    let mut bob_socket2 = WorkbenchClient::connect(port, BOB).await;
    let connect_ok = bob_socket2.subscribe_group(&group_id).await;
    assert!(
        connect_ok["ok"].as_bool().unwrap_or(false),
        "the current owner keeps the workbench view: {connect_ok}"
    );
    let sent = bob_socket2
        .chat_as_bot_outcome(&group_id, &bot_id, "Story Bot", "still working")
        .await;
    assert!(
        sent.is_none() || sent.as_deref() == Some(""),
        "the current owner keeps the business op: {sent:?}"
    );
    bob_socket2.close().await;
    println!("[8] sync removed {WALT}; exactly {WALT}'s Bot-bound entries fail");

    // Independent Human rights: the ORIGINAL owner keeps
    // their unmanaged, self-owned capacity — a fresh
    // trusted registration still succeeds (their own
    // creator/instance view is untouched by the Bot-bound
    // revocation).
    let (own_bot_id, _) =
        register_trusted_bot(&client, &bcs_url, WALT, "Walt Independent Bot")
            .await;
    assert_ne!(own_bot_id, bot_id);
    let (_, own_page) =
        read_managers(&client, &bcs_url, &human_token(WALT), &own_bot_id).await;
    assert_eq!(owner_user_of(&own_page), WALT);
    println!("[8] {WALT}'s independent rights intact (own registered bot)");

    // ── 9. Second transfer (new target) ──────────────────────────────
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!(
            "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/human_{CAROL}"
        ),
        &human_token(BOB),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 201, "second transfer to {CAROL}: {text}");
    let resp = collaboration(
        &client,
        &bcs_url,
        "POST",
        &format!(
            "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/human_{CAROL}/accept"
        ),
        &human_token(CAROL),
        None,
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "{CAROL} accepts: {text}");
    let (_, page) =
        read_managers(&client, &bcs_url, &human_token(CAROL), &bot_id).await;
    assert_eq!(owner_user_of(&page), CAROL);
    println!("[9] second transfer accepted; owner = {CAROL}");

    // ── 10. Normal retirement (cleanup-pending retry and
    //     the Provider three-branch stay pinned by
    //     `bcs-bot-store --test authority_lifecycle`).
    //     `DELETE /bots/{id}` requires a HUMAN caller: the
    //     non-owner Human is denied (current-edge policy),
    //     the CURRENT owner retires.
    let resp = client
        .delete(&format!("{bcs_url}/bots/{bot_id}"))
        .header("X-Mock-User-Id", WALT)
        .header("X-Mock-Nick-Name", WALT)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("non-owner retire request");
    assert_eq!(
        resp.status().as_u16(),
        403,
        "a Human with no current management edge cannot retire: \
         {} {}",
        resp.status(),
        resp.text().await.unwrap_or_default()
    );
    let resp = client
        .delete(&format!("{bcs_url}/bots/{bot_id}"))
        .header("X-Mock-User-Id", CAROL)
        .header("X-Mock-Nick-Name", CAROL)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("owner retire request");
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(status, 200, "normal retirement by the owner: {text}");
    let resp = client
        .delete(&format!("{bcs_url}/bots/{bot_id}"))
        .header("X-Mock-User-Id", CAROL)
        .header("X-Mock-Nick-Name", CAROL)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .expect("second retire request");
    let second = resp.status().as_u16();
    assert!(
        second == 404 || second == 409,
        "the second retirement is idempotent-negative: {second} {}",
        resp.text().await.unwrap_or_default()
    );
    println!("[10] normal retirement committed (second attempt → {second})");

    println!("\n========== E2E Bot Authority Full Chain Story PASSED ==========");
}
/// The trusted-app scope is deny-by-default: with NO
/// `[bot_authority]` configuration at all (the release
/// default), a fully verified trusted App identity is
/// denied every authority mutation — the feature stays
/// dormant until the config is added.
#[tokio::test]
async fn e2e_bot_authority_trusted_apps_default_deny() {
    let (_temp_dir, data_dir) = create_temp_dir();
    let mut pm = ProcessManager::new();
    let port = pm.start_bcs(&data_dir).await.expect("start server");
    let bcs_url = format!("http://127.0.0.1:{port}");
    let client = reqwest::Client::new();

    let (bot_id, _) =
        register_trusted_bot(&client, &bcs_url, WALT, "Dormant Bot").await;

    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!("/api/v1/collaboration/bots/{bot_id}/managers"),
        &app_token(APP_ID, Some(TENANT)),
        Some(json!({ "manager_user_ids": [WALT] })),
    )
    .await;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    assert_eq!(
        status, 403,
        "no trusted_apps config → deny-everything default: {text}"
    );
    let (status, _) =
        read_managers(&client, &bcs_url, &app_token(APP_ID, Some(TENANT)), &bot_id)
            .await;
    assert_eq!(
        status, 403,
        "no trusted_apps config → even reads are denied"
    );
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &format!("/api/v1/collaboration/bots/{bot_id}/ownership"),
        &app_token(APP_ID, Some(TENANT)),
        Some(json!({ "owner_user_id": WALT })),
    )
    .await;
    assert_eq!(
        resp.status(),
        403,
        "no trusted_apps config → ownership sync denied too"
    );

    println!("\n========== E2E Trusted-App Default Deny PASSED ==========");
}

/// Ownership sync story (设计 §9.1 E2E story): scoped
/// trusted App swaps the owner atomically; old owner is
/// FULLY removed; pending transfer closes as invalidated;
/// idempotent replay is a zero-diff; unknown target and
/// Human-only credentials are rejected with the pinned
/// codes. (The version-0 Bot 409 leg stays in the shared
/// conformance harnesses — trusted registration always
/// lands a bot at ownership_version 1, so a public-HTTP
/// e2e cannot produce one.)
#[tokio::test]
async fn e2e_bot_authority_ownership_sync_story() {
    let (_temp_dir, data_dir) = create_temp_dir();
    let mut pm = ProcessManager::new();
    let port = {
        let extra = json!({
            "bot_authority": { "trusted_apps": [trusted_apps_rule()] }
        });
        start_with_extra(&mut pm, &data_dir, Some(extra)).await
    };
    let bcs_url = format!("http://127.0.0.1:{port}");
    let client = reqwest::Client::new();

    // ── 1. The stage: owner WALT's bot; CAROL + DAVE become ──
    //    KNOWN Humans through their own trusted registrations
    //    (the same pre-seed convention as the full-chain story).
    let (bot_id, _) =
        register_trusted_bot(&client, &bcs_url, WALT, "Sync Origin Bot").await;
    let (_carol_bot, _) =
        register_trusted_bot(&client, &bcs_url, CAROL, "Carol Bot").await;
    let (_dave_bot, _) =
        register_trusted_bot(&client, &bcs_url, DAVE, "Dave Bot").await;

    let ownership_path =
        format!("/api/v1/collaboration/bots/{bot_id}/ownership");

    // ── 2. Human-only credential: denied with the pinned code ──
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &ownership_path,
        &human_token(WALT),
        Some(json!({ "owner_user_id": CAROL })),
    )
    .await;
    let status = resp.status();
    let body: Value = resp.json().await.unwrap_or_default();
    assert_eq!(status, 403, "Human-only ownership sync denied: {body}");
    assert_eq!(
        body["data"]["error_code"],
        "bot_authority_operator_forbidden",
        "the pinned Human-only denial code: {body}"
    );

    // ── 3. Trusted swap WALT → CAROL (设计 §2: atomic; the ──
    //    previous owner is reported back).
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &ownership_path,
        &app_token(APP_ID, Some(TENANT)),
        Some(json!({ "owner_user_id": CAROL })),
    )
    .await;
    let status = resp.status();
    let body: Value = resp.json().await.unwrap_or_default();
    assert_eq!(status, 200, "ownership swap: {status} {body}");
    assert_eq!(
        body["data"]["changed"], true,
        "the swap is a real change: {body}"
    );
    assert_eq!(body["data"]["owner_user_id"], CAROL, "the new owner: {body}");
    assert_eq!(
        body["data"]["previous_owner_user_id"], WALT,
        "the displaced owner is reported: {body}"
    );

    // ── 4. CAROL reads the ownership view; WALT is fully out ──
    //    (no manage — the same Human-rule read the full-chain
    //    story proves for revoked ex-managers).
    let resp = collaboration(
        &client,
        &bcs_url,
        "GET",
        &ownership_path,
        &human_token(CAROL),
        None,
    )
    .await;
    let status = resp.status();
    let body: Value = resp.json().await.unwrap_or_default();
    assert_eq!(status, 200, "new owner reads ownership: {body}");
    assert_eq!(body["data"]["owner_user_id"], CAROL, "view owner: {body}");
    let resp = collaboration(
        &client,
        &bcs_url,
        "GET",
        &ownership_path,
        &human_token(WALT),
        None,
    )
    .await;
    assert_eq!(
        resp.status(),
        403,
        "the old owner lost every Human rule (fully removed)"
    );

    // ── 5. Idempotent replay: zero diff (设计 §2 idempotent ──
    //    same-owner replay never writes state).
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &ownership_path,
        &app_token(APP_ID, Some(TENANT)),
        Some(json!({ "owner_user_id": CAROL })),
    )
    .await;
    let status = resp.status();
    let body: Value = resp.json().await.unwrap_or_default();
    assert_eq!(status, 200, "the replay stays a success: {body}");
    assert_eq!(
        body["data"]["changed"], false,
        "the replay is a zero diff: {body}"
    );

    // ── 6. Unknown target Human: the pinned 400 ─────────────
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &ownership_path,
        &app_token(APP_ID, Some(TENANT)),
        Some(json!({ "owner_user_id": "nobody-at-all" })),
    )
    .await;
    let status = resp.status();
    let body: Value = resp.json().await.unwrap_or_default();
    assert_eq!(status, 400, "unknown target rejected: {body}");
    assert_eq!(
        body["data"]["error_code"],
        "bot_authority_human_unknown",
        "the pinned human-unknown code: {body}"
    );

    // ── 7. Pending closure: CAROL initiates CAROL → DAVE, ────
    //    then the App syncs CAROL → DAVE directly; the transfer
    //    row closes invalidated with the frozen system
    //    marker/reason (设计 §5.1 step 6).
    let transfer_path = format!(
        "/api/v1/collaboration/bots/{bot_id}/ownership-transfers/human_{DAVE}"
    );
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &transfer_path,
        &human_token(CAROL),
        None,
    )
    .await;
    let status = resp.status();
    let body: Value = resp.json().await.unwrap_or_default();
    assert_eq!(status, 201, "transfer initiated: {status} {body}");
    let resp = collaboration(
        &client,
        &bcs_url,
        "PUT",
        &ownership_path,
        &app_token(APP_ID, Some(TENANT)),
        Some(json!({ "owner_user_id": DAVE })),
    )
    .await;
    let status = resp.status();
    let body: Value = resp.json().await.unwrap_or_default();
    assert_eq!(status, 200, "swap under pending: {status} {body}");

    // The closure is visible to the row's target Human (the
    // dual-admission read).
    let resp = collaboration(
        &client,
        &bcs_url,
        "GET",
        &transfer_path,
        &human_token(DAVE),
        None,
    )
    .await;
    let status = resp.status();
    let body: Value = resp.json().await.unwrap_or_default();
    assert_eq!(status, 200, "target reads the closed row: {body}");
    assert_eq!(body["data"]["status"], "invalidated", "closure state: {body}");
    assert_eq!(
        body["data"]["decided_by_kind"], "system",
        "the frozen system marker: {body}"
    );
    assert_eq!(
        body["data"]["reason"], "ownership_sync",
        "the frozen closure reason: {body}"
    );

    // ── 8. A stale late accept on the closed round can no ────
    //    longer move the owner (owner_changed; the swap result
    //    is final).
    let resp = collaboration(
        &client,
        &bcs_url,
        "POST",
        &format!("{transfer_path}/accept"),
        &human_token(DAVE),
        None,
    )
    .await;
    let status = resp.status();
    let body: Value = resp.json().await.unwrap_or_default();
    assert_eq!(status, 409, "stale accept conflicts: {status} {body}");
    assert_eq!(
        body["data"]["error_code"], "owner_changed",
        "the pinned owner-changed conflict code: {body}"
    );

    // Post-accept the owner stays DAVE (S05: the committed
    // swap is the durable fact).
    let resp = collaboration(
        &client,
        &bcs_url,
        "GET",
        &ownership_path,
        &human_token(DAVE),
        None,
    )
    .await;
    let status = resp.status();
    let body: Value = resp.json().await.unwrap_or_default();
    assert_eq!(status, 200, "the final owner keeps the read: {body}");
    assert_eq!(
        body["data"]["owner_user_id"], DAVE,
        "the owner stays DAVE after the stale accept: {body}"
    );

    println!("\n========== E2E Ownership Sync PASSED ==========");
}
