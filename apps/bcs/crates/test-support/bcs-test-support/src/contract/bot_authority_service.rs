//! `BotAuthorityService` (the application facade)
//! conformance harness (bot-authority spec §3.1/§5/§6).
//!
//! The facade is the ONLY route-facing surface for the
//! twelve `/api/v1/collaboration/` authority routes. This
//! harness runs the REAL composed fixture
//! (`crate::bot_authority::authority_service_fixture`:
//! real Core + real scope policy + invite gate stub) and
//! pins the facade's own decisions: admission (a Human rule
//! never falls back to the App identity), trusted-App scope
//! denials, owner-target protection, the transfer round
//! trip, and the revoked-edge reads.
//!
//! Conformance entry: any `tests/conformance_*.rs` driver.

#![allow(clippy::expect_used, reason = "test harness — panic on failure is the contract")]

use crate::bot_authority::AUTHORITY_FIXTURE_APP_NAME;
use bcs_service_api::application::v1::bot_authority::{
    ManagerEdit, ManagerEditAction, ManagerListQuery,
    ManagerSync, OwnershipSync, TransferAction, TransferCommand, TransferKey,
};
use bcs_service_api::application::v1::identity::{
    AuthenticatedAppIdentity, AuthenticatedCaller,
    AuthenticatedUserIdentity,
};

use crate::bot_authority::{authority_service_fixture, seed_bot, seed_humans};

const OWNER: &str = "svc_owner";
const STRANGER: &str = "svc_stranger";
const OUTSIDER: &str = "svc_outsider";
const TRUSTED_APP_ID: i64 = 7;
const DENIED_APP_ID: i64 = 99;

fn human(user_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: Some(AuthenticatedUserIdentity {
            id: user_id.into(),
            username: user_id.into(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn app(app_id: i64) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: None,
        bot: None,
        app: Some(AuthenticatedAppIdentity {
            app_id,
            app_name: AUTHORITY_FIXTURE_APP_NAME.into(),
            owners: "owner".into(),
            app_type: "THIRD_PARTY".into(),
        }),
        access_key: None,
    }
}

/// The untrusted-App denial caller (设计 §9.1): same tenant,
/// but an app NAME the fixture rule never covers — the scope
/// policy matches the verified app name, never the numeric id.
fn untrusted_app() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("tenant-a".into()),
        user: None,
        bot: None,
        app: Some(AuthenticatedAppIdentity {
            app_id: DENIED_APP_ID,
            app_name: "Untrusted App".into(),
            owners: "owner".into(),
            app_type: "THIRD_PARTY".into(),
        }),
        access_key: None,
    }
}

fn managers_query(bot_id: &str) -> ManagerListQuery {
    ManagerListQuery {
        bot_id: bot_id.to_string(),
        offset: 0,
        limit: 50,
    }
}

/// The application builds the real mutation from the
/// verified identity; the placeholder value passed by the
/// adapter is always overridden — the requests below
/// therefore carry the adapter's dead placeholder.
fn unused_mutation(
) -> bcs_service_api::types::bot_authority::AuthorityMutation {
    bcs_service_api::types::bot_authority::AuthorityMutation {
        mutation_id: String::new(),
        actor: bcs_service_api::types::bot_authority::AuthorityActor::System {
            name: "unused-by-harness".into(),
        },
    }
}

fn transfer_command(
    bot_id: &str,
    target: &str,
    action: TransferAction,
) -> TransferCommand {
    TransferCommand {
        key: TransferKey {
            bot_id: bot_id.to_string(),
            target_owner_actor_id: format!("human_{target}"),
        },
        action,
        mutation: unused_mutation(),
    }
}

/// Real facade-level semantics over the composed
/// fixture service.
pub async fn bot_authority_service_contract_tests() {
    let fixture = authority_service_fixture(true).await;
    seed_bot(&fixture.bot_repo, "bot_sync", OWNER, &[]).await;
    seed_humans(&fixture.bot_repo, &[OWNER, STRANGER, OUTSIDER]).await;

    // The owner reads the manager page (minimal
    // projection with the owner summary).
    let page = fixture
        .service
        .managers(&human(OWNER), managers_query("bot_sync"))
        .await
        .expect("owner manager read");
    assert_eq!(page.owner.owner_user_id, OWNER);

    // A PLAIN Human (no App in the credential set)
    // can never sync — the collection replace is
    // trusted-service-only (spec §5.2; no
    // Human-shaped fallback exists).
    assert!(
        fixture
            .service
            .sync_managers(
                &human(OWNER),
                ManagerSync {
                    bot_id: "bot_sync".into(),
                    user_ids: vec![STRANGER.to_string()],
                    mutation: unused_mutation(),
                },
            )
            .await
            .is_err(),
        "a plain Human cannot sync"
    );

    // Dual credentials SELECT the App identity for
    // the collection replace (spec §5.2: the App
    // rule applies when the caller carries it; the
    // Human leg only serves the Human-rule routes).
    let dual = {
        let mut caller = human(OWNER);
        caller.app = Some(AuthenticatedAppIdentity {
            app_id: TRUSTED_APP_ID,
            app_name: AUTHORITY_FIXTURE_APP_NAME.into(),
            owners: "owner".into(),
            app_type: "THIRD_PARTY".into(),
        });
        caller
    };
    let change = fixture
        .service
        .sync_managers(
            &dual,
            ManagerSync {
                bot_id: "bot_sync".into(),
                user_ids: vec![STRANGER.to_string()],
                mutation: unused_mutation(),
            },
        )
        .await
        .expect("dual credentials select the app for sync");
    assert_eq!(change.added, 1);
    let page = fixture
        .service
        .managers(&human(OWNER), managers_query("bot_sync"))
        .await
        .expect("page after app sync");
    assert_eq!(page.items, vec![STRANGER.to_string()]);

    // ── ownership sync (设计 §9.1 facade 面) ──
    seed_bot(&fixture.bot_repo, "bot_ownsvc", OWNER, &[]).await;
    seed_humans(&fixture.bot_repo, &["svc_next"]).await;

    // An App whose NAME the trusted rule never covers is denied.
    let error = fixture
        .service
        .sync_ownership(&untrusted_app(), OwnershipSync {
            bot_id: "bot_ownsvc".into(),
            owner_user_id: "svc_next".into(),
            mutation: unused_mutation(),
        })
        .await
        .expect_err("untrusted app denied");
    assert!(error.to_string().contains("bot_authority_operator_forbidden"), "{error}");

    // A PLAIN Human can never sync ownership.
    let error = fixture
        .service
        .sync_ownership(&human(OWNER), OwnershipSync {
            bot_id: "bot_ownsvc".into(),
            owner_user_id: "svc_next".into(),
            mutation: unused_mutation(),
        })
        .await
        .expect_err("human-only denied");
    assert!(error.to_string().contains("bot_authority_operator_forbidden"), "{error}");

    // The scoped App swaps atomically: old owner fully removed,
    // target owns the bot, manager set stripped bare (target was
    // not seeded as manager here — the strip is a no-op proof
    // covered by the store harness).
    let change = fixture
        .service
        .sync_ownership(&app(TRUSTED_APP_ID), OwnershipSync {
            bot_id: "bot_ownsvc".into(),
            owner_user_id: "svc_next".into(),
            mutation: unused_mutation(),
        })
        .await
        .expect("trusted app syncs owner");
    assert!(change.changed);
    assert_eq!(change.owner_user_id.as_deref(), Some("svc_next"));
    assert_eq!(change.previous_owner_user_id.as_deref(), Some(OWNER));
    // The new owner reads the ownership view (Human rule); the
    // old owner is denied every Human rule now.
    let view = fixture
        .service
        .ownership(&human("svc_next"), "bot_ownsvc")
        .await
        .expect("new owner reads ownership");
    assert_eq!(view.owner_user_id, "svc_next");
    assert!(fixture
        .service
        .ownership(&human(OWNER), "bot_ownsvc")
        .await
        .is_err());

    // Idempotent replay through the facade: zero diff.
    let replay = fixture
        .service
        .sync_ownership(&app(TRUSTED_APP_ID), OwnershipSync {
            bot_id: "bot_ownsvc".into(),
            owner_user_id: "svc_next".into(),
            mutation: unused_mutation(),
        })
        .await
        .expect("replay");
    assert!(!replay.changed);

    // An App OUTSIDE the trusted rule is denied even on the
    // covered bot.
    assert!(
        fixture
            .service
            .managers(&app(DENIED_APP_ID), managers_query("bot_sync"))
            .await
            .is_err(),
        "an untrusted app id is denied"
    );

    // Owner-target protection (spec §6): the owner can
    // never be a transfer target.
    assert!(
        fixture
            .service
            .change_transfer(
                &human(OWNER),
                transfer_command("bot_sync", OWNER, TransferAction::RequestOrRefresh),
            )
            .await
            .is_err(),
        "owner-target transfer must be rejected"
    );

    // Transfer round trip through the facade: the owner
    // requests, the target Human (dual-admitted) accepts,
    // and the owner rotates with the ex-owner restored as a
    // plain manager.
    fixture
        .service
        .change_transfer(
            &human(OWNER),
            transfer_command("bot_sync", STRANGER, TransferAction::RequestOrRefresh),
        )
        .await
        .expect("owner transfer request");
    fixture
        .service
        .change_transfer(
            &human(STRANGER),
            transfer_command("bot_sync", STRANGER, TransferAction::Accept),
        )
        .await
        .expect("target accept");
    let page = fixture
        .service
        .managers(&human(STRANGER), managers_query("bot_sync"))
        .await
        .expect("new owner read");
    assert_eq!(page.owner.owner_user_id, STRANGER);
    let page = fixture
        .service
        .managers(&human(OWNER), managers_query("bot_sync"))
        .await
        .expect("ex-owner keeps the manager read");
    assert!(
        page.items.contains(&OWNER.to_string()),
        "the ex-owner is restored as a plain manager"
    );

    // A Human with NO management edge is denied the
    // manager read (403-no-edge at the facade level) and
    // the single-item edit.
    assert!(
        fixture
            .service
            .managers(&human(OUTSIDER), managers_query("bot_sync"))
            .await
            .is_err(),
        "the no-edge Human cannot read"
    );
    assert!(
        fixture
            .service
            .edit_manager(
                &human(OUTSIDER),
                ManagerEdit {
                    bot_id: "bot_sync".into(),
                    user_id: OUTSIDER.into(),
                    action: ManagerEditAction::Grant,
                    mutation: unused_mutation(),
                },
            )
            .await
            .is_err(),
        "the no-edge Human cannot edit managers"
    );
}
