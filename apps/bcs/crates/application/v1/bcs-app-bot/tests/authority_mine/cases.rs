use super::*;

/// Spec §8.0: reachable/unreachable applies to ALL
/// candidates BEFORE total and pagination — A offline,
/// B reachable with limit 1 returns B with total 1.
#[tokio::test]
async fn mine_reachable_filter_precedes_pagination() {
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-a-offline", "mine-owner", ActorStatus::Online)
        .await;
    fixture
        .add_controlled_bot("bot-b-reachable", "mine-owner", ActorStatus::Online)
        .await;
    fixture.connect_ws("bot-b-reachable").await;

    let page = list_mine(&fixture.service, Some(BotReachability::Reachable))
        .await
        .expect("reachable page");
    assert_eq!(page.total, 1, "the filter runs BEFORE pagination");
    assert_eq!(page.items.len(), 1);
    assert_eq!(page.items[0].bot_id(), "bot-b-reachable");

    let offline = list_mine_at(
        &fixture.service,
        Some(BotReachability::Unreachable),
        None,
        0,
        20,
    )
    .await
    .expect("unreachable page");
    assert_eq!(offline.total, 1);
    assert_eq!(offline.items[0].bot_id(), "bot-a-offline");

    // No filter: total is the full controlled union
    // plus the Human self entry (kind None).
    let all = list_mine(&fixture.service, None).await.expect("all");
    assert_eq!(all.total, 3);
    let bots_only = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 20)
        .await
        .expect("bots only");
    assert_eq!(bots_only.total, 2);
}

/// Mixed WS + HTTP Provider sources keep the existing
/// `status=Online && (WS active || HTTP Provider
/// routable)` semantics; missing/broken provider
/// ingredients degrade to UNREACHABLE, not to errors.
#[tokio::test]
async fn mine_mixed_ws_and_provider_sources() {
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-ws", "mine-owner", ActorStatus::Online)
        .await;
    fixture
        .add_controlled_bot("bot-provider-ok", "mine-owner", ActorStatus::Online)
        .await;
    fixture
        .add_controlled_bot("bot-no-webhook", "mine-owner", ActorStatus::Online)
        .await;
    fixture
        .add_controlled_bot(
            "bot-no-credential",
            "mine-owner",
            ActorStatus::Online,
        )
        .await;
    fixture.connect_ws("bot-ws").await;
    fixture
        .seed_provider_routable("bot-provider-ok", "provider-ok", Some("https://gateway.example/bcs"), false)
        .await;
    fixture
        .seed_provider_routable("bot-no-webhook", "provider-2", None, false)
        .await;
    fixture
        .seed_provider_routable(
            "bot-no-credential",
            "provider-3",
            Some("https://gateway.example/bcs"),
            true,
        )
        .await;

    let page = list_mine(&fixture.service, Some(BotReachability::Reachable))
        .await
        .expect("reachable page");
    let mut items = physicals(&page);
    items.sort();
    assert_eq!(
        items,
        vec!["bot-provider-ok".to_string(), "bot-ws".to_string()],
        "WS active and routable provider bots are reachable; \
         provider bots without an endpoint or credential are not"
    );
}

/// Human self entry semantics (spec §8.0 point 3):
/// visible without a reachability filter (the Owner
/// display tag comes from the candidate relation; no
/// Human→Human owner edge exists), excluded when a
/// reachability filter is specified (existing
/// behavior).
#[tokio::test]
async fn mine_self_entry_semantics() {
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-controlled", "mine-owner", ActorStatus::Online)
        .await;

    let all = list_mine(&fixture.service, None).await.expect("self visible");
    assert_eq!(humans(&all), vec!["human_mine-owner".to_string()]);
    assert_eq!(physicals(&all), vec!["bot-controlled".to_string()]);
    assert_eq!(all.total, 2);
    fixture.assert_no_human_owner_edge("mine-owner").await;

    let reachable = list_mine(&fixture.service, Some(BotReachability::Reachable))
        .await
        .expect("self excluded");
    assert!(
        humans(&reachable).is_empty(),
        "a specified reachability excludes the Human self entry"
    );
    assert_eq!(reachable.total, 0);
}

/// Spec §8 mine row: 逐项
/// `access_relation=owner|manager`; the Human self
/// entry keeps `self`. The labels come from the
/// SAME sampling as the page and follow the seeded
/// edges — including through an owner transfer.
#[tokio::test]
async fn mine_items_carry_their_sampled_access_relation() {
    use bcs_service_api::application::v1::Page;
    use bcs_test_support::{grant_manager_edge, grant_owner_edge};

    let relation_of = |page: &Page<Bot>, id: &str| -> String {
        page.items
            .iter()
            .find_map(|bot| {
                let relation = match bot {
                    Bot::Physical(physical) => {
                        physical.access_relation.clone()
                    }
                    Bot::Human(human) => {
                        human.access_relation.clone()
                    }
                };
                (bot.bot_id() == id).then_some(relation).flatten()
            })
            .expect("mine item relation")
    };

    // One owned bot plus one managed bot (the
    // caller's relation differs per seed).
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-owned", "mine-owner", ActorStatus::Online)
        .await;
    fixture
        .add_controlled_bot("bot-managed", "someone-else", ActorStatus::Online)
        .await;
    grant_manager_edge(&fixture.repo, "bot-managed", "mine-owner").await;

    let page = list_mine(&fixture.service, None)
        .await
        .expect("mine page");
    assert_eq!(relation_of(&page, "bot-owned"), "owner");
    assert_eq!(relation_of(&page, "bot-managed"), "manager");
    assert_eq!(relation_of(&page, "human_mine-owner"), "self");

    // Post-transfer: the owner edge moves to bob,
    // and bob samples the SAME bot as `owner`.
    let transfer_fixture = Fixture::new_with_drift(None, None).await;
    transfer_fixture
        .add_controlled_bot("bot-transfer", "alice", ActorStatus::Online)
        .await;
    grant_owner_edge(&transfer_fixture.repo, "bot-transfer", "bob").await;
    let bob_page = transfer_fixture
        .service
        .list_mine(ListMyBots {
            caller: human_caller("bob"),
            kind: None,
            name: None,
            status: None,
            reachability: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect("bob mine page");
    assert_eq!(
        relation_of(&bob_page, "bot-transfer"),
        "owner",
        "the new owner samples its own bot as owner"
    );
}


/// >500 candidates: the runtime observation is ONE WS
/// read plus bounded ≤128-id provider batches — COUNTED,
/// not just results (spec §8.0 有界块数).
#[tokio::test]
async fn mine_over_500_candidates_observes_once_with_bounded_batches() {
    let fixture = Fixture::new_with_drift(None, None).await;
    // One WS-connected bot among 501 candidates → the
    // provider resolution covers 500 ids → 4 batches
    // (128/128/128/80) with AUTHORITY_QUERY_CHUNK = 128.
    for index in 0..501 {
        fixture
            .add_controlled_bot(
                &format!("bot-volume-{index:03}"),
                "mine-owner",
                ActorStatus::Online,
            )
            .await;
        if index < 37 {
            fixture
                .connect_ws(&format!("bot-volume-{index:03}"))
                .await;
        }
    }
    assert_eq!(fixture.counting_repo.observations(), 0);
    assert_eq!(fixture.bindings_probe.calls(), 0);

    let page = list_mine_at(
        &fixture.service,
        None,
        Some(BotKind::Bot),
        0,
        20,
    )
    .await
    .expect("volume page");
    assert_eq!(page.total, 501);
    {
        let batches = fixture.counting_repo.avatar_batches.lock().expect("avatar batches");
        assert_eq!(batches.len(), 1, "one avatar store batch for the returned page");
        let expected: std::collections::BTreeSet<_> = page.items.iter()
            .map(|bot| bot.bot_id().to_string()).collect();
        assert_eq!(batches[0].iter().cloned().collect::<std::collections::BTreeSet<_>>(), expected);
        assert_eq!(batches[0].len(), 20, "candidate-only IDs are never read for avatars");
    }


    // ONE WS observation…
    assert_eq!(
        fixture.counting_repo.observations(),
        1,
        "exactly ONE WS connection-set observation"
    );
    // …and ceil(500/128) provider bindings batches (no
    // per-bot resolution, the batches are real).
    assert_eq!(
        fixture.bindings_probe.calls(),
        4,
        "500 non-WS ids in bounded batches of ≤128"
    );

    // A SECOND request re-observes (the sampling is not
    // cached across mine calls) — still one observation
    // PER request.
    let _ = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 20)
        .await
        .expect("again");
    assert_eq!(fixture.counting_repo.observations(), 2);
}

/// Storage/read failures PROPAGATE — a failing
/// provider resolution is never a
/// fake-unreachable success (spec §8.0
/// 存储错误返回错误).
#[tokio::test]
async fn mine_storage_error_propagates_not_unreachable() {
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-propagate", "mine-owner", ActorStatus::Online)
        .await;
    fixture.bindings_probe.fail_reads.store(true, Ordering::SeqCst);

    let error = list_mine(&fixture.service, None)
        .await
        .expect_err("storage error propagates");
    assert!(
        error.to_string().contains("injected provider binding read failure"),
        "the real read failure surfaces: {error}"
    );
}

/// Empty page beyond the total: no backfill, no drift,
/// items stay empty with the REAL total (never a fake
/// exact total).
#[tokio::test]
async fn mine_offset_beyond_total_returns_empty_items_with_real_total() {
    let fixture = Fixture::new_with_drift(None, None).await;
    fixture
        .add_controlled_bot("bot-single", "mine-owner", ActorStatus::Online)
        .await;
    let page = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 5, 20)
        .await
        .expect("empty page");
    assert!(page.items.is_empty());
    assert_eq!(page.total, 1);
}

/// Deletion drift discovered at the page backfill:
/// ONE whole retry reproduces the SAME page
/// composition — the recovered page is returned.
#[tokio::test]
async fn mine_page_drift_retries_once_and_returns_the_fresh_page() {
    let fixture = Fixture::new_with_drift(
        Some((1, vec!["bot-other".to_string()])),
        None,
    )
    .await;
    for bot_id in ["bot-page", "bot-other"] {
        fixture
            .add_controlled_bot(bot_id, "mine-owner", ActorStatus::Online)
            .await;
    }
    // Page (sorted ids): [bot-other]; its backfill
    // loses `bot-other` once -> drift; the ONE retry
    // re-samples, the page reproduces exactly and is
    // returned.
    let page = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 1)
        .await
        .expect("recovered page");
    assert_eq!(page.total, 2);
    assert_eq!(page.items[0].bot_id(), "bot-other");
}

/// Persistent drift: the retry STILL does not reproduce
/// the page — the response is the pinned 409
/// `bot_list_changed`, never a shifted page pretending
/// to be exact.
#[tokio::test]
async fn mine_persistent_drift_reports_bot_list_changed() {
    let fixture = Fixture::new_with_drift(
        Some((i64::MAX, vec!["bot-paged".to_string()])),
        None,
    )
    .await;
    for bot_id in ["bot-paged", "bot-spare"] {
        fixture
            .add_controlled_bot(bot_id, "mine-owner", ActorStatus::Online)
            .await;
    }

    let error = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 1)
        .await
        .expect_err("persistent drift is a 409");
    assert_eq!(
        error.code(),
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_LIST_CHANGED
    );
}

/// PURE role drift in the sampling→backfill window
/// (no deletion): the page's role re-derivation finds
/// `bot-revoked` revoked → drift → ONE whole retry;
/// the retry's page cannot reproduce the first
/// composition → `bot_list_changed`.
#[tokio::test]
async fn mine_role_drift_between_samplings_reports_bot_list_changed() {
    let fixture = Fixture::new_with_role_flip(
        None,
        Some((2, "bot-revoked".to_string())),
        None,
    )
    .await;
    for bot_id in ["bot-stable", "bot-revoked", "bot-replacement"] {
        fixture
            .add_controlled_bot(bot_id, "mine-owner", ActorStatus::Online)
            .await;
    }

    // Sorted ids: `bot-replacement` < `bot-revoked`
    // < `bot-stable`. The FIRST sampling's page
    // (offset 0, limit 2) is
    // [bot-replacement, bot-revoked]; its page-backfill
    // ROLE re-derivation (the second candidate read)
    // finds `bot-revoked` revoked — deletion-free
    // role drift → ONE whole retry. The retry's page
    // [bot-replacement, bot-stable] cannot reproduce
    // the first composition — `bot_list_changed`, never
    // the stale page.
    let error = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 2)
        .await
        .expect_err("role drift is a 409");
    assert_eq!(
        error.code(),
        bcs_service_api::application::v1::bot_authority::ERROR_BOT_LIST_CHANGED
    );
}

/// Transient role drift: the page's role
/// re-derivation finds the edge revoked (drift), but
/// the edge is RESTORED before the retry's sampling —
/// the retry reproduces the SAME page composition and
/// serves it (delete-free recovery).
#[tokio::test]
async fn mine_transient_role_drift_stabilizes_and_serves_the_page() {
    let fixture = Fixture::new_with_role_flip(
        None,
        Some((2, "bot-revoked".to_string())),
        Some((3, "bot-revoked".to_string())),
    )
    .await;
    for bot_id in ["bot-stable", "bot-revoked", "bot-replacement"] {
        fixture
            .add_controlled_bot(bot_id, "mine-owner", ActorStatus::Online)
            .await;
    }

    // Attempt 1: page [bot-replacement, bot-revoked];
    // the role re-derivation sees `bot-revoked`
    // revoked → drift. Attempt 2: the edge is back,
    // the page reproduces exactly and is served.
    let page = list_mine_at(&fixture.service, None, Some(BotKind::Bot), 0, 2)
        .await
        .expect("recovered role drift");
    assert_eq!(page.total, 3);
    let items = physicals(&page);
    assert_eq!(
        items,
        vec![
            "bot-replacement".to_string(),
            "bot-revoked".to_string()
        ]
    );
}

/// Review finding (mine steady-state N+1): a
/// fully-migrated owner must not pay a per-Bot governance
/// rescan on every `/bots/mine` — the candidate list is
/// store-side prefiltered (`live physical Bot`, byte-exact
/// creator, `ownership_version = 0`, classify conflict
/// prefilter), so steady state costs ONE bounded candidate
/// lookup and ZERO governance reads.
#[tokio::test]
async fn steady_state_mine_does_not_rescan_initialized_bots() {
    let fixture = Fixture::new_with_drift(None, None).await;
    // 120 fully-initialized owned bots — past the per-request
    // attempt cap so a stale whole-list rescan cannot hide in
    // the counters.
    for i in 0..120 {
        fixture
            .add_controlled_bot(
                &format!("steady-{i:03}"),
                "mine-owner",
                ActorStatus::Online,
            )
            .await;
    }
    // Warm-up listing (settles ensure/token paths and drains
    // any one-time migration work before the measured call).
    list_mine_at(&fixture.service, None, None, 0, 1)
        .await
        .expect("warm-up listing");
    let sweeps_before = fixture
        .counting_repo
        .migration_sweep_calls
        .load(Ordering::SeqCst);
    let reads_before = fixture
        .counting_repo
        .governance_reads
        .load(Ordering::SeqCst);

    let page = list_mine_at(&fixture.service, None, None, 0, 1)
        .await
        .expect("steady-state page");
    assert_eq!(page.total, 121, "120 bots + Human self");
    assert_eq!(
        fixture
            .counting_repo
            .migration_sweep_calls
            .load(Ordering::SeqCst)
            - sweeps_before,
        1,
        "one bounded candidate lookup per listing"
    );
    assert_eq!(
        fixture.counting_repo.governance_reads.load(Ordering::SeqCst) - reads_before,
        0,
        "no per-Bot governance rescan once everything is migrated"
    );
}
