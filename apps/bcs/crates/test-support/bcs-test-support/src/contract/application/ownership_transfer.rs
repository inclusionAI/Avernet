//! `OwnershipTransferService` shared conformance harness (plan Task 18
//! R25, plan 公共命名: `ownership_transfer_service_contract_tests`).
//!
//! The driver constructs the PRODUCTION transfer facade over the shared
//! recording double hook + [`CountingBotAuthorityCore`]; this suite pins
//! the store-independent application contract:
//!
//! - EVERY use case is Human-only (Bot-only / AccessKey-only principals
//!   are Forbidden and never trigger an authority question);
//! - the ownership read consults the centralized hook FIRST (a denying
//!   hook answers Forbidden with zero core reads);
//! - create-shape defects (self-transfer, blank recipient, zero version)
//!   are `invalid_transfer_recipient` 400s BEFORE any store write —
//!   same-key replay semantics stay in the production crate's suites
//!   (plan Task 14) over the real stores;
//! - the listing rejects out-of-range windows (0 or >100) without
//!   querying the store.

use std::sync::Arc;

use bcs_service_api::application::v1::{
    ApplicationError, AuthenticatedAccessKeyIdentity, AuthenticatedBotIdentity,
    AuthenticatedCaller, AuthenticatedUserIdentity, BotOwnershipTransferPage,
    BotOwnership, BotOwnershipTransferCreation, BotOwnershipTransferReceipt,
    CreateBotOwnershipTransfer, DecideBotOwnershipTransfer, GetBotOwnership,
    GetBotOwnershipTransfer, ListBotOwnershipTransfers, OwnershipTransferService,
};

use crate::{CountingBotAuthorityCore, RecordingBotAuthorityHook};

/// Driver-supplied wiring bundle: the production facade over the shared
/// recording doubles.
pub struct OwnershipTransferServiceHarness {
    /// The driver's `OwnershipTransferServiceImpl` (or substitutable equivalent).
    pub service: Arc<dyn OwnershipTransferService>,
    /// The recording hook the driver constructed the facade with.
    pub hook: Arc<RecordingBotAuthorityHook>,
    /// The counting authority core the driver constructed the facade with.
    pub core: Arc<CountingBotAuthorityCore>,
}

fn human_caller(user_id: &str) -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: Some("contract-tenant".into()),
        user: Some(AuthenticatedUserIdentity {
            id: user_id.to_string(),
            username: user_id.to_string(),
            display_name: None,
            full_name: None,
        }),
        bot: None,
        app: None,
        access_key: None,
    }
}

fn bot_only_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: None,
        user: None,
        bot: Some(AuthenticatedBotIdentity {
            bot_uuid: "contract-bot".into(),
            owner_id: "contract-owner".into(),
            app_id: 1,
            agent_code: "contract-agent".into(),
        }),
        app: None,
        access_key: None,
    }
}

fn access_key_only_caller() -> AuthenticatedCaller {
    AuthenticatedCaller {
        tenant: None,
        user: None,
        bot: None,
        app: None,
        access_key: Some(AuthenticatedAccessKeyIdentity {
            access_key: "contract-access-key".into(),
            expire_at: time::OffsetDateTime::from_unix_timestamp(4_102_444_800)
                .expect("far-future epoch"),
        }),
    }
}

fn is_forbidden(error: &ApplicationError) -> bool {
    matches!(error, ApplicationError::Forbidden(_) | ApplicationError::ForbiddenCode { .. })
}

/// The shared `OwnershipTransferService` conformance suite.
pub async fn ownership_transfer_service_contract_tests(h: &OwnershipTransferServiceHarness) {
    use bcs_service_api::application::v1::TransferListDirection;

    let bot_id = "transfer-contract-bot";
    let transfer_id = "transfer-contract-transfer";
    let user_id = "transfer-contract-user";

    // HumanOnly on all paths: Bot-only and AccessKey-only principals are
    // Forbidden everywhere and never consult the hook.
    let hook_before = h.hook.calls();
    let bot_only_list: Result<BotOwnershipTransferPage, ApplicationError> = h
        .service
        .list_transfers(ListBotOwnershipTransfers {
            caller: bot_only_caller(),
            direction: TransferListDirection::Sent,
            status: None,
            offset: 0,
            limit: 20,
        })
        .await;
    for error in [
        h.service
            .get_ownership(GetBotOwnership {
                caller: bot_only_caller(),
                bot_id: bot_id.into(),
            })
            .await
            .expect_err("bot-only ownership read must be refused"),
        h.service
            .create_transfer(CreateBotOwnershipTransfer {
                caller: access_key_only_caller(),
                bot_id: bot_id.into(),
                to_user_id: "someone-else".into(),
                expected_owner_version: 1,
                client_request_id: "contract-request".into(),
            })
            .await
            .expect_err("access-key-only create must be refused"),
        bot_only_list.expect_err("bot-only listing must be refused"),
        h.service
            .get_transfer(GetBotOwnershipTransfer {
                caller: access_key_only_caller(),
                transfer_id: transfer_id.into(),
            })
            .await
            .expect_err("access-key-only read must be refused"),
        h.service
            .accept_transfer(DecideBotOwnershipTransfer {
                caller: bot_only_caller(),
                transfer_id: transfer_id.into(),
            })
            .await
            .expect_err("bot-only accept must be refused"),
        h.service
            .reject_transfer(DecideBotOwnershipTransfer {
                caller: access_key_only_caller(),
                transfer_id: transfer_id.into(),
            })
            .await
            .expect_err("access-key-only reject must be refused"),
        h.service
            .cancel_transfer(DecideBotOwnershipTransfer {
                caller: bot_only_caller(),
                transfer_id: transfer_id.into(),
            })
            .await
            .expect_err("bot-only cancel must be refused"),
    ] {
        assert!(is_forbidden(&error), "the transfer lane is Human-only: {error:?}");
    }
    assert_eq!(
        h.hook.calls(),
        hook_before,
        "a non-Human principal never advances to an authority question"
    );

    // The ownership read asks the centralized hook first: a denying hook
    // answers Forbidden and the authority core reads nothing.
    let reads_before = h.core.reads();
    let hook_before = h.hook.calls();
    let error = h
        .service
        .get_ownership(GetBotOwnership {
            caller: human_caller(user_id),
            bot_id: bot_id.into(),
        })
        .await
        .expect_err("denying hook must refuse the read");
    assert!(is_forbidden(&error), "denying hook answers Forbidden: {error:?}");
    assert_eq!(h.hook.calls(), hook_before + 1, "exactly one hook question");
    assert_eq!(h.core.reads(), reads_before, "a denied read never probes the store");

    // Create-shape defects answer the fixed recipient 400s BEFORE the
    // store (zero core writes).
    let writes_before = h.core.writes();
    for error in [
        h.service
            .create_transfer(CreateBotOwnershipTransfer {
                caller: human_caller(user_id),
                bot_id: bot_id.into(),
                to_user_id: user_id.into(),
                expected_owner_version: 1,
                client_request_id: "contract-request".into(),
            })
            .await
            .expect_err("self-transfer is a shape defect"),
        h.service
            .create_transfer(CreateBotOwnershipTransfer {
                caller: human_caller(user_id),
                bot_id: bot_id.into(),
                to_user_id: "  ".into(),
                expected_owner_version: 1,
                client_request_id: "contract-request".into(),
            })
            .await
            .expect_err("blank recipient is a shape defect"),
        h.service
            .create_transfer(CreateBotOwnershipTransfer {
                caller: human_caller(user_id),
                bot_id: bot_id.into(),
                to_user_id: "someone-else".into(),
                expected_owner_version: 0,
                client_request_id: "contract-request".into(),
            })
            .await
            .expect_err("zero versions are shape defects"),
    ] {
        assert!(
            matches!(
                &error,
                ApplicationError::InvalidInput { code, .. }
                    if code == "invalid_transfer_recipient" || code == "invalid_request"
            ),
            "shape defects answer the fixed 400 family before the store: {error:?}"
        );
    }
    assert_eq!(
        h.core.writes(),
        writes_before,
        "shape-defective creates never reach the store"
    );

    // The listing window contract: 0 and >100 never query the store.
    let reads_before = h.core.reads();
    for error in [
        h.service
            .list_transfers(ListBotOwnershipTransfers {
                caller: human_caller(user_id),
                direction: TransferListDirection::Sent,
                status: None,
                offset: 0,
                limit: 0,
            })
            .await
            .expect_err("zero limit must be rejected"),
        h.service
            .list_transfers(ListBotOwnershipTransfers {
                caller: human_caller(user_id),
                direction: TransferListDirection::Sent,
                status: None,
                offset: 0,
                limit: 101,
            })
            .await
            .expect_err("over-limit windows must be rejected"),
    ] {
        assert!(
            matches!(&error, ApplicationError::InvalidInput { code, .. } if code == "invalid_request"),
            "window defects answer invalid_request: {error:?}"
        );
    }
    assert_eq!(h.core.reads(), reads_before, "rejected windows never query the store");

    // Type-binding proof: the happy-path value types stay transport-neutral
    // (compile-time; the object-safe values the drivers assert on).
    let _ = std::any::type_name::<(BotOwnership, BotOwnershipTransferCreation,
        BotOwnershipTransferPage, BotOwnershipTransferReceipt)>();
}