//! Real Connect → edge-store reads behind the v1 candidate list and search.
//!
//! The application entry points are driven through an actual `DbConnectService`
//! over SQLite. The shared harness counts friend pages, profile reads, control
//! plane reads, registry backfill batches and projection batches separately, so
//! a failed friend read can never become a successful empty or partial set and
//! a search can never re-read friends per hit.

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::collections::HashSet;
use std::sync::atomic::Ordering;
use std::sync::Arc;

use bcs_service_api::application::v1::{
    BotCandidatePurpose, BotCandidateSearchMode, BotService, ListBotCandidates,
};

#[path = "support/candidate_friend_harness.rs"]
mod harness;

use harness::*;

#[tokio::test]
async fn approved_default_profile_edges_are_the_only_friend_authority() {
    let env = bcs_config::resolve_env_str();
    let mut seeds = vec![
        approved_friend("friend-a", &env),
        approved_friend("friend-b", &env),
        SeedFriend {
            id: "pending-bot".to_string(),
            kind: bcs_service_api::ActorKind::Bot,
            edge_status: "pending",
            profile_active: true,
            env: env.clone(),
        },
        SeedFriend {
            id: "revoked-bot".to_string(),
            kind: bcs_service_api::ActorKind::Bot,
            edge_status: "revoked",
            profile_active: true,
            env: env.clone(),
        },
        SeedFriend {
            id: "inactive-bot".to_string(),
            kind: bcs_service_api::ActorKind::Bot,
            edge_status: "approved",
            profile_active: false,
            env: env.clone(),
        },
        SeedFriend {
            id: "other-env-bot".to_string(),
            kind: bcs_service_api::ActorKind::Bot,
            edge_status: "approved",
            profile_active: true,
            env: "other".to_string(),
        },
        SeedFriend {
            id: "human_friend".to_string(),
            kind: bcs_service_api::ActorKind::Human,
            edge_status: "approved",
            profile_active: true,
            env: env.clone(),
        },
    ];
    seeds.extend(
        (0..3).map(|index| approved_friend(&format!("extra-{index}"), &env)),
    );
    let harness = harness(seeds).await;

    let page = harness.list(0).await.expect("approved friend candidates");
    let ids = page
        .items
        .iter()
        .map(|item| item.bot.bot_id.clone())
        .collect::<HashSet<_>>();
    assert_eq!(
        ids,
        HashSet::from([
            "friend-a".to_string(),
            "friend-b".to_string(),
            "extra-0".to_string(),
            "extra-1".to_string(),
            "extra-2".to_string(),
        ])
    );
    assert_eq!(page.total, 5);
    assert!(page.items.iter().all(|item| item.is_friend));

    let queries = harness.candidate.queries.lock().expect("queries lock");
    assert_eq!(queries.len(), 1, "one candidate query per request");
    let query = queries.first().expect("recorded candidate query");
    assert_eq!(
        query.friend_ids,
        HashSet::from([
            "friend-a".to_string(),
            "friend-b".to_string(),
            "extra-0".to_string(),
            "extra-1".to_string(),
            "extra-2".to_string(),
        ]),
        "only approved active default-profile Bot edges are friends"
    );
    assert_eq!(query.viewer_kind, bcs_service_api::ActorKind::Bot);
    assert_eq!(query.env, harness.env);
    let reads = harness.reads();
    assert_eq!(reads.friend_pages, 1);
    assert_eq!(reads.profile_queries, 0, "no per-friend profile SELECT");
    drop(queries);
    assert_eq!(harness.candidate_queries(), 1);
    assert_eq!(
        harness.registry.avatar_batches.load(Ordering::SeqCst),
        1,
        "projection cost is counted separately from friend SQL"
    );
}

#[tokio::test]
async fn static_friend_sets_use_bound_friend_pages() {
    for count in [0_usize, 1, 100, 101, 501, 1_000] {
        let harness = friends_harness(count).await;
        let page = harness.list(0).await.expect("candidate page");
        assert_eq!(page.total, count as u64);
        let reads = harness.reads();
        let expected_pages = std::cmp::max(1, count.div_ceil(100));
        assert_eq!(
            reads.friend_pages, expected_pages,
            "F={count} must use max(1, ceil(F/100)) friend pages"
        );
        assert_eq!(
            reads.profile_queries, 0,
            "F={count} must not run per-friend profile SELECTs"
        );
        assert_eq!(harness.candidate_queries(), 1, "F={count} candidate query");
        assert_eq!(
            harness.registry.avatar_batches.load(Ordering::SeqCst),
            usize::from(count > 0),
            "F={count} projection batches"
        );
    }
}

#[tokio::test]
async fn consecutive_candidate_pages_each_pay_a_full_friend_read() {
    let harness = friends_harness(501).await;
    for offset in [0_u64, 1, 2] {
        harness.list(offset).await.expect("candidate page");
    }
    let reads = harness.reads();
    assert_eq!(reads.friend_pages, 3 * 6, "three requests x ceil(501/100)");
    assert_eq!(reads.profile_queries, 0);
    assert_eq!(harness.candidate_queries(), 3);
    assert_eq!(harness.registry.avatar_batches.load(Ordering::SeqCst), 3);
}

#[tokio::test]
async fn unauthorized_perspective_fails_before_any_friend_read() {
    let harness = friends_harness(3).await;
    let error = harness
        .service
        .list_candidates(ListBotCandidates {
            caller: human_caller("staff-2"),
            bot_id: ACTING_BOT.to_string(),
            purpose: BotCandidatePurpose::Collaboration,
            name: None,
            offset: 0,
            limit: 20,
        })
        .await
        .expect_err("non-owner must be rejected");
    assert_eq!(error.code(), "forbidden");
    assert_eq!(harness.candidate_queries(), 0);
    assert_eq!(harness.reads().friend_pages, 0);
}

#[tokio::test]
async fn first_friend_page_sql_failure_fails_the_whole_request() {
    let harness = friends_harness(3).await;
    harness.db.fail_page(1);
    assert_fails_without_candidates(harness).await;
}

#[tokio::test]
async fn later_friend_page_sql_failure_never_delivers_a_partial_set() {
    let harness = friends_harness(101).await;
    harness.db.fail_page(2);
    assert_fails_without_candidates(harness).await;
}

#[tokio::test]
async fn malformed_friend_row_fails_decoding() {
    let harness = friends_harness(3).await;
    harness.db.corrupt_page(1, MALFORMED_ROW, None);
    assert_fails_without_candidates(harness).await;
}

#[tokio::test]
async fn changed_friend_total_fails_instead_of_scanning_further() {
    let harness = friends_harness(101).await;
    harness.db.corrupt_page(2, DRIFT_TOTAL, None);
    assert_fails_without_candidates(harness).await;
}

#[tokio::test]
async fn missing_friend_page_fails_instead_of_returning_a_prefix() {
    let harness = friends_harness(101).await;
    harness.db.corrupt_page(2, MISSING_PAGE, None);
    assert_fails_without_candidates(harness).await;
}

#[tokio::test]
async fn duplicate_friend_id_fails_instead_of_deduplicating_silently() {
    let harness = friends_harness(101).await;
    harness
        .db
        .corrupt_page(2, DUPLICATE_ID, Some("friend-0000"));
    assert_fails_without_candidates(harness).await;
}

async fn assert_fails_without_candidates(harness: Harness) {
    let error = harness
        .list(0)
        .await
        .expect_err("friend read failure must fail the request");
    assert_eq!(error.code(), "internal_error");
    assert_eq!(
        harness.candidate_queries(),
        0,
        "no candidate read may consume a partial friend set"
    );
    assert_eq!(
        harness.reads().profile_queries,
        0,
        "the strict read never falls back to per-target profile SELECTs"
    );
}

#[tokio::test]
async fn search_empty_query_never_reads_friends_or_profiles() {
    let harness = friends_harness(3).await;
    for query in [None, Some(""), Some("  \t\n ")] {
        let result = harness.search(query).await.expect("empty query search");
        assert!(result.items.is_empty());
        assert_eq!(result.search_mode, BotCandidateSearchMode::EmptyQuery);
    }
    let reads = harness.reads();
    assert_eq!(reads.friend_pages, 0, "empty queries never read friends");
    assert_eq!(reads.profile_queries, 0);
    assert_eq!(harness.control_plane_searches(), 0);
    assert_eq!(harness.projection_batches(), 0);
}

#[tokio::test]
async fn search_semantic_hits_share_one_friend_read() {
    let harness = friends_with_profile(
        3,
        Arc::new(StaticRecommend {
            ids: vec!["friend-0001".to_string()],
        }),
    )
    .await;

    let result = harness.search(Some(" planning ")).await.expect("semantic search");

    assert_eq!(result.search_mode, BotCandidateSearchMode::Semantic);
    assert_eq!(
        result
            .items
            .iter()
            .map(|item| item.bot.bot_id.as_str())
            .collect::<Vec<_>>(),
        vec!["friend-0001"]
    );
    assert!(result.items[0].is_friend);
    assert_eq!(result.items[0].score, Some(1.0));
    let reads = harness.reads();
    assert_eq!(reads.friend_pages, 1, "one friend read per request");
    assert_eq!(reads.profile_queries, 0);
    assert_eq!(harness.control_plane_searches(), 1);
    assert_eq!(harness.registry_batches(), 1, "one batched registry backfill");
    assert_eq!(harness.projection_batches(), 1);
}

#[tokio::test]
async fn semantic_miss_falls_back_to_names_without_another_friend_read() {
    let harness = friends_with_profile(
        3,
        Arc::new(StaticRecommend {
            ids: vec!["outside-visibility".to_string()],
        }),
    )
    .await;

    let result = harness.search(Some("friend")).await.expect("name fallback");

    assert_eq!(result.search_mode, BotCandidateSearchMode::NameFallback);
    assert!(!result.items.is_empty());
    assert!(result.items.iter().all(|item| item.score.is_none()));
    let reads = harness.reads();
    assert_eq!(
        reads.friend_pages, 1,
        "the name fallback reuses the request's one friend read"
    );
    assert_eq!(reads.profile_queries, 0);
    assert_eq!(
        harness.control_plane_searches(),
        2,
        "one recall validation plus one name fallback query"
    );
    assert_eq!(harness.registry_batches(), 1);
    assert_eq!(harness.projection_batches(), 1);
}

#[tokio::test]
async fn search_first_friend_page_sql_failure_never_falls_back_to_names() {
    let harness = friends_harness(3).await;
    harness.db.fail_page(1);
    assert_search_fails_without_core(harness).await;
}

#[tokio::test]
async fn search_later_friend_page_sql_failure_never_falls_back_to_names() {
    let harness = friends_harness(101).await;
    harness.db.fail_page(2);
    assert_search_fails_without_core(harness).await;
}

async fn assert_search_fails_without_core(harness: Harness) {
    let error = harness
        .search(Some("friend"))
        .await
        .expect_err("friend read failure must fail the search");
    assert_eq!(error.code(), "internal_error");
    assert_eq!(
        harness.control_plane_searches(),
        0,
        "a friend failure must not reach the Core (no name fallback)"
    );
    assert_eq!(harness.registry_batches(), 0);
    assert_eq!(harness.projection_batches(), 0);
    assert_eq!(
        harness.reads().profile_queries,
        0,
        "the strict read never falls back to per-target profile SELECTs"
    );
}

#[tokio::test]
async fn search_stage_budget_stays_bounded_as_friend_pages_grow() {
    for count in [0_usize, 1, 100, 101, 501, 1_000] {
        let harness = friends_harness(count).await;
        let result = harness.search(Some("friend")).await.expect("name fallback");
        assert_eq!(
            result.search_mode,
            BotCandidateSearchMode::NameFallback,
            "F={count} empty worker profile falls back to name search"
        );
        assert_eq!(
            result.items.len(),
            count.min(20),
            "F={count} display cap is 20"
        );
        let reads = harness.reads();
        let expected_pages = std::cmp::max(1, count.div_ceil(100));
        assert_eq!(
            reads.friend_pages, expected_pages,
            "F={count} must use max(1, ceil(F/100)) friend pages"
        );
        assert_eq!(
            reads.profile_queries, 0,
            "F={count} must not run per-friend profile SELECTs"
        );
        assert_eq!(
            harness.control_plane_searches(),
            1,
            "F={count} control-plane reads do not scale with friends"
        );
        assert_eq!(
            harness.registry_batches(),
            usize::from(count > 0),
            "F={count} registry backfill is one batch, not per friend"
        );
        assert_eq!(
            harness.projection_batches(),
            1,
            "F={count} projection uses one batch read"
        );
        assert_eq!(
            harness.avatar_batches(),
            usize::from(count > 0),
            "F={count} avatar projection is batched"
        );
        assert_eq!(
            harness.runtime_batches(),
            1,
            "F={count} runtime sampling is one batch"
        );
    }
}
