//! S1.2 List snapshot + signed-cursor prototype tests (T1 model evidence).
//!
//! Model-level proof of the §5 List contract: an immutable, materialized cut
//! with fixed `(status_updated_at DESC, tid ASC)` order, filter, total and row
//! projection; later pages read only the retained snapshot; the cursor is a
//! keyed-MAC token bound to subject/group/filter/sort/cut/last-key/expiry;
//! continuation re-verifies read permission, rejects expiry, tampering,
//! cross-subject reuse and retention release, and never falls back to live
//! state. Real index/protocol work is T4/T8, not here.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "cursor assertions")]
#[path = "group_round_support/mod.rs"]
mod support;
use support::*;
use support::pagination::{CANDIDATE_PAGE_SIZE, CursorClaims, ListFilter, Page,
    PageProfile, parse_cursor_claims, reissue_token_with_mac};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::group_round::*;

fn reader() -> TaskScope {
    TaskScope { tenant: TenantId("tenant".into()), principal: PrincipalId("caller".into()) }
}
fn stranger(principal: &str) -> TaskScope {
    TaskScope { tenant: TenantId("tenant".into()), principal: PrincipalId(principal.into()) }
}
fn tid_of(session: &str) -> String { format!("s0042.{session}.m{session}") }

async fn admit_round(p: &Prototype, session: &str, generation: u64) -> RoundKey {
    // Message (hence request/tid) must be unique per round: message is the
    // stable request key, so a shared one replays/conflicts across sessions.
    let req = request(session, &format!("m{session}"), generation);
    match p.admit(&req).await.unwrap() {
        Admission::New { accepted } => accepted.input.key,
        other => panic!("unexpected admission: {other:?}"),
    }
}
async fn finish_round(p: &Prototype, key: &RoundKey) {
    p.archive(key, "answer").await.unwrap();
    p.finish(key).await.unwrap();
}
/// Sessions finished: `1`; sessions running: the rest. Projects every Sid so a
/// live cut is valid before pagination starts.
async fn landscape(p: &Prototype, finished: &[u8], running: &[u8]) -> Vec<RoundKey> {
    let mut keys = vec![];
    for session in finished {
        let key = admit_round(p, &session.to_string(), 1).await;
        finish_round(p, &key).await;
        keys.push(key);
    }
    for session in running {
        keys.push(admit_round(p, &session.to_string(), 1).await);
    }
    let mut sids = vec![];
    for session in finished.iter().chain(running.iter()) {
        let sid = Sid(format!("s0042.{session}"));
        if !sids.contains(&sid) { sids.push(sid); }
    }
    for sid in &sids { p.project(sid).await.unwrap(); }
    keys
}

fn page_tids(page: &Page) -> Vec<String> {
    page.rows.iter().map(|r| r.tid.0.clone()).collect()
}

#[tokio::test]
async fn first_page_sorts_by_status_stamp_desc_then_tid_asc_with_filter_and_total() {
    let p = Prototype::new().await;
    landscape(&p, &[1, 2], &[3, 4]).await;
    let page = p.list_page(&reader(), &PageProfile::default(), &ListFilter::All)
        .await.unwrap();
    // Finished rounds carry a later status stamp (terminal commit); running
    // rows keep their admission stamp. Ties break by tid ASC.
    assert_eq!(page_tids(&page), vec![tid_of("1"), tid_of("2"), tid_of("3"), tid_of("4")]);
    assert_eq!(page.total, 4);
    assert_eq!(page.rows[0].status, "completed");
    assert_eq!(page.rows[2].status, "running");
    assert_eq!(page.rows[0].status_updated_at, 2);
    assert_eq!(page.rows[2].status_updated_at, 1);
    // A single default-size page covers the whole cut: no continuation.
    assert!(page.next.is_none());
    let completed = p.list_page(&reader(), &PageProfile::default(), &ListFilter::Completed)
        .await.unwrap();
    assert_eq!(page_tids(&completed), vec![tid_of("1"), tid_of("2")]);
    assert_eq!(completed.total, 2);
    assert!(completed.next.is_none());
    let running = p.list_page(&reader(), &PageProfile::default(), &ListFilter::Running)
        .await.unwrap();
    assert_eq!(page_tids(&running), vec![tid_of("3"), tid_of("4")]);
    assert_eq!(running.total, 2);
}

#[tokio::test]
async fn later_pages_are_frozen_against_cold_transfer_new_round_and_status_update() {
    let p = Prototype::new().await;
    let keys = landscape(&p, &[1, 2], &[3, 4]).await;
    let profile = PageProfile { page_size: 2, ..PageProfile::default() };
    let page1 = p.list_page(&reader(), &profile, &ListFilter::All).await.unwrap();
    assert_eq!(page_tids(&page1), vec![tid_of("1"), tid_of("2")]);
    assert_eq!(page1.total, 4);
    let cursor = page1.next.clone().unwrap();

    // Interleave every mutation class before fetching later pages.
    p.compact(&keys[0]).await.unwrap();                 // 1: 热转冷
    finish_round(&p, &keys[2]).await;                   // 2: 另一 Task 状态更新
    let new_round = admit_round(&p, "5", 1).await;      // 3: 新增轮次 C
    p.project(&Sid("s0042.5".into())).await.unwrap();
    let _ = new_round;

    // Live state now diverges from the cut: no projections were refreshed for
    // the finished/compacted Sids, so a live re-collect must fail closed while
    // continuation still succeeds — later pages never read live state.
    assert!(p.list_cut().await.is_err());
    let live_view = p.get(&keys[2]).await.unwrap().unwrap();
    assert!(live_view.terminal.is_some(), "live view diverged: round 3 completed");

    let page2 = p.continue_page(&reader(), &cursor).await.unwrap();
    assert_eq!(page_tids(&page2), vec![tid_of("3"), tid_of("4")]);
    assert_eq!(page2.total, 4, "total frozen by the cut");
    assert!(page2.next.is_none());
    // Round 3 finished after page 1, but the cut says it was running.
    assert_eq!(page2.rows[0].status, "running");
    assert!(page2.rows.iter().all(|r| r.tid.0 != tid_of("5")),
        "row admitted after the cut never appears in later pages");
    // 跨页不重复、不漏项: collected tids across pages equal the cut rows.
    let mut seen = page_tids(&page1);
    seen.extend(page_tids(&page2));
    assert_eq!(seen, vec![tid_of("1"), tid_of("2"), tid_of("3"), tid_of("4")]);
}

#[tokio::test]
async fn cursor_chain_covers_all_rows_once_with_single_row_pages() {
    let p = Prototype::new().await;
    landscape(&p, &[1, 2], &[3, 4, 5]).await;
    let mut profile = PageProfile::default();
    profile.page_size = 1;
    let mut walked = vec![];
    let mut total_seen = vec![];
    let mut next: Option<String> = None;
    loop {
        let page = match next {
            Some(token) => p.continue_page(&reader(), &token).await.unwrap(),
            None => p.list_page(&reader(), &profile, &ListFilter::All).await.unwrap(),
        };
        assert!(page.rows.len() <= 1, "page read cost is bounded by page size");
        total_seen.push(page.total);
        walked.extend(page_tids(&page));
        match page.next {
            Some(token) => next = Some(token),
            None => break,
        }
    }
    assert_eq!(walked, vec![tid_of("1"), tid_of("2"), tid_of("3"), tid_of("4"), tid_of("5")]);
    assert!(total_seen.iter().all(|&t| t == 5), "total constant across pages");
    // Same continuation token reapplied: idempotent, no double consumption.
    let first = p.list_page(&reader(), &profile, &ListFilter::All).await.unwrap();
    let token = first.next.clone().unwrap();
    let twice = p.continue_page(&reader(), &token).await.unwrap().rows;
    let again = p.continue_page(&reader(), &token).await.unwrap().rows;
    assert_eq!(twice, again);
}

#[tokio::test]
async fn stale_projection_returns_dependency_error_never_a_stale_total() {
    let p = Prototype::new().await;
    admit_round(&p, "1", 1).await;
    admit_round(&p, "2", 1).await;
    p.project(&Sid("s0042.1".into())).await.unwrap();
    // Round 2 is admitted and checkpointed but its projection lags: List must
    // fail closed with a retryable dependency error, not a missing total.
    match p.list_page(&reader(), &PageProfile::default(), &ListFilter::All).await {
        Err(StoreError::NotApplied { code }) => assert_eq!(code, "projection_lag"),
        other => panic!("expected dependency error, got {other:?}"),
    }
    p.project(&Sid("s0042.2".into())).await.unwrap();
    let page = p.list_page(&reader(), &PageProfile::default(), &ListFilter::All).await.unwrap();
    assert_eq!(page.total, 2);
    assert_eq!(page_tids(&page), vec![tid_of("1"), tid_of("2")]);
}

#[tokio::test]
async fn cursor_expires_at_its_bound_and_continuation_is_rejected() {
    let p = Prototype::new().await;
    landscape(&p, &[1, 2], &[3, 4]).await;
    let profile = PageProfile { page_size: 2, cursor_ttl_ms: 1_000 };
    let page1 = p.list_page(&reader(), &profile, &ListFilter::All).await.unwrap();
    let cursor = page1.next.clone().unwrap();
    p.advance_clock(999);
    let still_valid = p.continue_page(&reader(), &cursor).await.unwrap();
    assert_eq!(still_valid.total, 4);
    p.advance_clock(1); // exactly at the bound: expired
    match p.continue_page(&reader(), &cursor).await {
        Err(StoreError::Invalid { code }) => assert_eq!(code, "cursor_expired"),
        other => panic!("expected explicit expiry rejection, got {other:?}"),
    }
    // A fresh query mints a new, valid cursor against the same rows.
    let fresh = p.list_page(&reader(), &profile, &ListFilter::All).await.unwrap();
    assert_eq!(page_tids(&fresh), page_tids(&page1));
}

#[tokio::test]
async fn retention_release_rejects_continuation_without_live_fallback() {
    let p = Prototype::new().await;
    landscape(&p, &[1, 2], &[3, 4]).await;
    let profile = PageProfile { page_size: 2, ..PageProfile::default() };
    let page1 = p.list_page(&reader(), &profile, &ListFilter::All).await.unwrap();
    let cursor = page1.next.clone().unwrap();
    p.release_snapshot(&page1.cut).await.unwrap();
    // Released retention: continuation fails explicitly, never silently
    // re-reads live state even though a live re-collect would still succeed.
    match p.continue_page(&reader(), &cursor).await {
        Err(StoreError::NotApplied { code }) => assert_eq!(code, "snapshot_released"),
        other => panic!("expected retention rejection, got {other:?}"),
    }
    // Restart: a fresh query re-materializes and completes a full new walk.
    let restarted = p.list_page(&reader(), &profile, &ListFilter::All).await.unwrap();
    assert_eq!(page_tids(&restarted), page_tids(&page1));
    let token = restarted.next.clone().unwrap();
    let rest = p.continue_page(&reader(), &token).await.unwrap();
    assert_eq!(page_tids(&rest), vec![tid_of("3"), tid_of("4")]);
    assert_eq!(rest.total, 4);
}

#[tokio::test]
async fn cursor_is_bound_to_its_subject_and_reuse_across_subjects_is_rejected() {
    let p = Prototype::new().await;
    landscape(&p, &[1, 2], &[3, 4]).await;
    let profile = PageProfile { page_size: 2, ..PageProfile::default() };
    let caller_page = p.list_page(&reader(), &profile, &ListFilter::All).await.unwrap();
    let caller_cursor = caller_page.next.clone().unwrap();
    p.grant_read("mallory").await.unwrap();
    let mallory_page = p.list_page(&stranger("mallory"), &profile, &ListFilter::All).await.unwrap();
    // Equal rows, but the materialized cut binds the subject.
    assert_eq!(page_tids(&mallory_page), page_tids(&caller_page));
    assert_ne!(mallory_page.cut, caller_page.cut);
    // Mallory cannot continue Caller's cursor, even while authorized to read.
    match p.continue_page(&stranger("mallory"), &caller_cursor).await {
        Err(StoreError::Invalid { code }) => assert_eq!(code, "cursor_subject"),
        other => panic!("expected subject rejection, got {other:?}"),
    }
    // The rightful subject still continues.
    assert_eq!(p.continue_page(&reader(), &caller_cursor).await.unwrap().total, 4);
}

#[tokio::test]
async fn revoked_read_blocks_new_lists_and_old_cursor_continuation() {
    let p = Prototype::new().await;
    landscape(&p, &[1, 2], &[3, 4]).await;
    let profile = PageProfile { page_size: 2, ..PageProfile::default() };
    let page1 = p.list_page(&reader(), &profile, &ListFilter::All).await.unwrap();
    let cursor = page1.next.clone().unwrap();
    p.revoke_read("caller").await.unwrap();
    // 翻页重验: the old cut cannot bypass a fresh read revocation.
    match p.list_page(&reader(), &profile, &ListFilter::All).await {
        Err(StoreError::Invalid { code }) => assert_eq!(code, "unauthorized"),
        other => panic!("expected unauthorized for new list, got {other:?}"),
    }
    match p.continue_page(&reader(), &cursor).await {
        Err(StoreError::Invalid { code }) => assert_eq!(code, "unauthorized"),
        other => panic!("expected unauthorized for old cursor, got {other:?}"),
    }
    // Re-granting read reopens the same retained cursor: the closed window is
    // the revoked one, the cursor itself stays valid until its own expiry.
    p.grant_read("caller").await.unwrap();
    assert_eq!(p.continue_page(&reader(), &cursor).await.unwrap().rows.len(), 2);
}

#[tokio::test]
async fn tampered_cursors_fail_signature_verification_for_every_bound_field() {
    let p = Prototype::new().await;
    landscape(&p, &[1, 2], &[3, 4]).await;
    let profile = PageProfile { page_size: 2, ..PageProfile::default() };
    let cursor = p.list_page(&reader(), &profile, &ListFilter::All)
        .await.unwrap().next.clone().unwrap();
    let (claims, mac) = parse_cursor_claims(&cursor).unwrap();
    fn mutate(claims: &CursorClaims, mac: &str, f: impl FnOnce(&mut CursorClaims)) -> String {
        let mut forged = claims.clone();
        f(&mut forged);
        reissue_token_with_mac(&forged, mac).unwrap()
    }
    let forgeries = [
        mutate(&claims, &mac, |c| c.tenant = "other-tenant".into()),
        mutate(&claims, &mac, |c| c.principal = "mallory".into()),
        mutate(&claims, &mac, |c| c.group = "other-group".into()),
        mutate(&claims, &mac, |c| c.filter = ListFilter::Completed.as_str().into()),
        mutate(&claims, &mac, |c| c.sort = "status_updated_at:asc".into()),
        mutate(&claims, &mac, |c| c.cut = "deadbeef".into()),
        mutate(&claims, &mac, |c| c.last_status_at += 1),
        mutate(&claims, &mac, |c| c.last_tid = tid_of("4")),
        // Validity extension is exactly as forgeable as any other field.
        mutate(&claims, &mac, |c| c.expires_at_ms += 60_000),
    ];
    for forged in forgeries {
        match p.continue_page(&reader(), &forged).await {
            Err(StoreError::Invalid { code }) => assert_eq!(code, "cursor_signature"),
            other => panic!("expected signature rejection, got {other:?}"),
        }
    }
    // The untampered token still works after all these attempts.
    assert_eq!(p.continue_page(&reader(), &cursor).await.unwrap().total, 4);
    // Malformed tokens never reach any claim field.
    for malformed in ["", "not-a-token", "zz.zz", "abcd"] {
        match p.continue_page(&reader(), malformed).await {
            Err(StoreError::Invalid { code }) =>
                assert_eq!(code, "cursor_format", "token {malformed:?}"),
            other => panic!("expected format rejection for {malformed}, got {other:?}"),
        }
    }
}

#[tokio::test]
async fn page_profile_must_request_a_positive_page_size() {
    let p = Prototype::new().await;
    landscape(&p, &[1], &[]).await;
    let profile = PageProfile { page_size: 0, ..PageProfile::default() };
    match p.list_page(&reader(), &profile, &ListFilter::All).await {
        Err(StoreError::Invalid { code }) => assert_eq!(code, "page_size"),
        other => panic!("expected page-size rejection, got {other:?}"),
    }
}

#[tokio::test]
async fn page_profile_cannot_exceed_the_candidate_hundred_row_page_cap() {
    let p = Prototype::new().await;
    landscape(&p, &[1], &[]).await;
    // "分页最多 100 行" is a maximum, not a default: at the cap is accepted...
    let at_cap = PageProfile { page_size: CANDIDATE_PAGE_SIZE, ..PageProfile::default() };
    let page = p.list_page(&reader(), &at_cap, &ListFilter::All).await.unwrap();
    assert_eq!(page.total, 1);
    // ...and one above the cap is rejected with the same page-size error.
    let over_cap = PageProfile { page_size: CANDIDATE_PAGE_SIZE + 1, ..PageProfile::default() };
    match p.list_page(&reader(), &over_cap, &ListFilter::All).await {
        Err(StoreError::Invalid { code }) => assert_eq!(code, "page_size"),
        other => panic!("expected page-size rejection, got {other:?}"),
    }
}
