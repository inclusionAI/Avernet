//! `BotManagerService` production implementation (plan Task 13, spec
//! §6): the Human-only manager list/grant/revoke use cases.
//!
//! Decision boundaries kept here:
//! - the caller is ALWAYS the Gateway-verified Human
//!   ([`require_authenticated_user`]); a body value never names the actor;
//! - every use case FIRST asks the centralized [`BotAuthorityHook`]
//!   ("may this Human manage this Bot" — the fail-closed hook), then
//!   reaches business state exactly like the hook prescribes: through
//!   [`BotAuthorityCoreService`] only, never the repo port;
//! - grant/revoke run as DIRECT-lane mutations with
//!   `AuditActor::Human`: the store's single mutation transaction
//!   re-validates the actor's CURRENT owner/manager role inside the
//!   transaction (Gate 0), so a just-revoked manager can never exploit
//!   idempotency, and `team/*` sources are never touched by this lane;
//! - the lane's error vocabulary is fixed (spec §6/§11.2): owner-target
//!   mutations are 409 `owner_role_requires_transfer` conflicts (the
//!   owner moves only through the transfer flow), live-human subject
//!   failures are 400 `invalid_subject`, current-role failures are 403
//!   `forbidden`, uninitialized ownership is 409
//!   `ownership_not_initialized`, and corruption is 500.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_service_api::application::v1::{
    ApplicationError, BOT_MANAGER_ROLE_LABEL, BotAuthorityHook, BotManagerEntry,
    BotManagerGrantResult, BotManagerPage, BotManagerRevokeResult, BotManagerService,
    GrantBotManager, ListBotManagers, RevokeBotManager, require_authenticated_user,
};
use bcs_service_api::core::BotAuthorityCoreService;
use bcs_service_api::port::repo::bot_authority::human_actor_id;
use bcs_service_api::types::error::ServiceError;
use bcs_service_api::types::{AuditActor, BotManagerList, ManagerMutation};

/// Positional page size when enumerating one Bot's whole manager set for
/// a list request (control-plane volumes).
const MANAGER_LIST_ENUMERATION_PAGE: usize = 100;

/// Safety bound on the enumeration realized for one list request: a Bot
/// with more distinct managers than this is a data anomaly the
/// control plane refuses to page through blindly (fail closed, never an
/// unbounded read).
const MANAGER_LIST_ENUMERATION_BOUND: usize = 10_000;

/// Centralized manager-lane application facade.
pub struct BotManagerServiceImpl {
    core: Arc<dyn BotAuthorityCoreService>,
    authority: Arc<dyn BotAuthorityHook>,
}

impl BotManagerServiceImpl {
    pub fn new(
        core: Arc<dyn BotAuthorityCoreService>,
        authority: Arc<dyn BotAuthorityHook>,
    ) -> Self {
        Self { core, authority }
    }

    /// The hook-answered question every manager use case asks first.
    async fn require_live_current_manager(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> Result<(), ApplicationError> {
        let allowed = self
            .authority
            .can_manage(user_id, bot_id)
            .await
            .map_err(map_manager_error)?;
        if allowed {
            Ok(())
        } else {
            Err(ApplicationError::forbidden(format!(
                "user '{user_id}' holds no current owner/manager role on bot '{bot_id}'"
            )))
        }
    }
}

fn validate_identity(field: &'static str, value: &str) -> Result<(), ApplicationError> {
    if value.trim().is_empty() {
        return Err(ApplicationError::invalid(
            "invalid_request",
            format!("manager use case field '{field}' must be a non-empty identity"),
        ));
    }
    Ok(())
}

/// Fixed application-error vocabulary of the manager lane:
/// - the store's mutation-transaction `Conflict` branch IS the
///   owner-target guard, so the lane surfaces 409
///   `owner_role_requires_transfer` (its bounded optimistic-retry
///   exhaustion shares the same 409 family);
/// - every other authority branch keeps its shared spec code (§11.2);
/// - non-authority storage/internal failures stay 500 `internal_error`,
///   never a mapped business code and never SQL leakage.
fn map_manager_error(error: ServiceError) -> ApplicationError {
    match &error {
        ServiceError::Authority(authority) => {
            if matches!(
                authority,
                bcs_service_api::types::error::AuthorityError::Conflict(_)
            ) {
                ApplicationError::owner_role_requires_transfer(error.to_string())
            } else {
                ApplicationError::authority(authority.clone())
            }
        }
        ServiceError::BotNotFound(bot_id) => ApplicationError::not_found(
            "bot_not_found",
            format!("Bot '{bot_id}' was not found"),
        ),
        other => ApplicationError::internal(other.to_string()),
    }
}

#[async_trait]
impl BotManagerService for BotManagerServiceImpl {
    async fn list_managers(
        &self,
        command: ListBotManagers,
    ) -> Result<BotManagerPage, ApplicationError> {
        let caller = require_authenticated_user(&command.caller)?;
        let user_id = caller.id.clone();
        validate_identity("bot_id", &command.bot_id)?;
        // API contract (spec §6): limit 1..=100, default 20 at the
        // transport; the offset is positional. Out-of-range pages never
        // query the store.
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "manager list limit must be within 1..=100",
            ));
        }
        self.require_live_current_manager(&user_id, &command.bot_id)
            .await?;

        // The store's read is positional without totals: enumerate
        // bounded pages, then project the requested window. The owner
        // never mixes into the page (she lives in `owner_user_id`).
        let mut all: Vec<BotManagerEntry> = Vec::new();
        let mut scanned: usize = 0;
        let mut owner = String::new();
        loop {
            let page: BotManagerList = self
                .core
                .list_managers(
                    &command.bot_id,
                    scanned as u64,
                    MANAGER_LIST_ENUMERATION_PAGE as u64,
                )
                .await
                .map_err(map_manager_error)?;
            // Every page repeats the same validated owner slot; keep the
            // first read (the read also keeps the assignment honest).
            if owner.is_empty() {
                owner = page.owner_user_id;
            }
            let len = page.managers.len();
            for manager in page.managers {
                let user_id = manager.user_id;
                all.push(BotManagerEntry {
                    actor_id: human_actor_id(&user_id),
                    user_id,
                    role: BOT_MANAGER_ROLE_LABEL.to_string(),
                });
            }
            if len < MANAGER_LIST_ENUMERATION_PAGE {
                break;
            }
            scanned += len;
            if scanned > MANAGER_LIST_ENUMERATION_BOUND {
                return Err(ApplicationError::payload_too_large(
                    "invalid_request",
                    "manager set exceeds the list enumeration bound",
                ));
            }
        }
        let total = all.len() as u64;
        let items = all
            .into_iter()
            .skip(usize::try_from(command.offset).unwrap_or(usize::MAX))
            .take(usize::try_from(command.limit).unwrap_or(0))
            .collect();
        Ok(BotManagerPage {
            bot_id: command.bot_id,
            owner_user_id: owner,
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn grant_manager(
        &self,
        command: GrantBotManager,
    ) -> Result<BotManagerGrantResult, ApplicationError> {
        let caller = require_authenticated_user(&command.caller)?;
        validate_identity("bot_id", &command.bot_id)?;
        validate_identity("user_id", &command.user_id)?;
        self.require_live_current_manager(&caller.id, &command.bot_id)
            .await?;
        let result = self
            .core
            .mutate_manager(
                AuditActor::Human {
                    user_id: caller.id.clone(),
                },
                &command.bot_id,
                ManagerMutation::GrantDirect {
                    user_id: command.user_id.clone(),
                },
            )
            .await
            .map_err(map_manager_error)?;
        Ok(BotManagerGrantResult {
            bot_id: command.bot_id,
            user_id: command.user_id,
            role: BOT_MANAGER_ROLE_LABEL.to_string(),
            changed: result.changed,
        })
    }

    async fn revoke_manager(
        &self,
        command: RevokeBotManager,
    ) -> Result<BotManagerRevokeResult, ApplicationError> {
        let caller = require_authenticated_user(&command.caller)?;
        validate_identity("bot_id", &command.bot_id)?;
        validate_identity("user_id", &command.user_id)?;
        self.require_live_current_manager(&caller.id, &command.bot_id)
            .await?;
        let result = self
            .core
            .mutate_manager(
                AuditActor::Human {
                    user_id: caller.id.clone(),
                },
                &command.bot_id,
                ManagerMutation::RevokeNonTeam {
                    user_id: command.user_id.clone(),
                },
            )
            .await
            .map_err(map_manager_error)?;
        Ok(BotManagerRevokeResult {
            bot_id: command.bot_id,
            user_id: command.user_id,
            // `changed` states whether THIS request actually revoked a
            // non-team edge; the untouched team sources report separately
            // (`revoked=true` is NOT total loss of management rights).
            revoked: result.changed,
            remaining_team_sources: result.remaining_team_sources,
        })
    }
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::sync::{Arc, Mutex};

    use async_trait::async_trait;
    use bcs_service_api::application::v1::ApplicationError;
    use bcs_service_api::application::v1::BotAuthorityHook;
    use bcs_service_api::core::BotAuthorityCoreService;
    use bcs_service_api::types::error::{AuthorityError, ServiceResult};
    use bcs_service_api::types::ownership_transfer::{
        CommittedTransferOutcome, CreateOwnershipTransfer, CreateTransferResult,
        ListOwnershipTransfers, OwnershipTransfer, OwnershipTransferPage,
    };
    use bcs_service_api::types::team_manager_sync::{TeamManagerSync, TeamSyncReceipt};
    use bcs_service_api::types::{
        AuditActor, BotAccessRelation, BotManagerList, BotManagerSummary, ManagerMutation,
        ManagerMutationResult, ManagementSource, OwnershipState, TransferAction,
    };
    use bcs_service_api::ServiceError;

    use super::*;

    /// Recording Core: captures the manager-lane calls; every other Core
    /// method is a hard failure (this facade never calls them).
    #[derive(Default)]
    struct RecordingCore {
        mutations: Mutex<Vec<(AuditActor, String, ManagerMutation)>>,
        lists: Mutex<Vec<(String, u64, u64)>>,
        next_error: Mutex<Option<ServiceError>>,
    }

    const PAGE: usize = 3;

    #[async_trait]
    impl BotAuthorityCoreService for RecordingCore {
        async fn ownership(&self, _bot_id: &str) -> ServiceResult<OwnershipState> {
            unreachable!("manager lane never reads ownership directly")
        }

        async fn role(
            &self,
            _user_id: &str,
            _bot_id: &str,
        ) -> ServiceResult<Option<BotAccessRelation>> {
            unreachable!("manager lane routes role questions through the hook")
        }

        async fn roles_for(
            &self,
            _pairs: &[(String, String)],
        ) -> ServiceResult<Vec<Option<BotAccessRelation>>> {
            unreachable!("manager lane never batch-reads roles")
        }

        async fn mutate_manager(
            &self,
            actor: AuditActor,
            bot_id: &str,
            mutation: ManagerMutation,
        ) -> ServiceResult<ManagerMutationResult> {
            self.mutations
                .lock()
                .expect("mutations lock")
                .push((actor, bot_id.to_string(), mutation.clone()));
            if let Some(error) = self.next_error.lock().expect("error lock").take() {
                return Err(error);
            }
            Ok(ManagerMutationResult {
                changed: true,
                remaining_team_sources: vec!["team-a".to_string()],
            })
        }

        async fn list_managers(
            &self,
            bot_id: &str,
            offset: u64,
            limit: u64,
        ) -> ServiceResult<BotManagerList> {
            self.lists
                .lock()
                .expect("lists lock")
                .push((bot_id.to_string(), offset, limit));
            if let Some(error) = self.next_error.lock().expect("error lock").take() {
                return Err(error);
            }
            // Serve a two-page manager set: PAGE users per full page.
            let managers = (0..PAGE)
                .map(|index| BotManagerSummary {
                    user_id: format!("user-{}", offset as usize + index),
                    sources: vec![ManagementSource::Direct],
                })
                .collect();
            Ok(BotManagerList {
                owner_user_id: "user-a".to_string(),
                managers,
            })
        }

        async fn sync_team(&self, _command: TeamManagerSync) -> ServiceResult<TeamSyncReceipt> {
            unreachable!("manager lane never runs team syncs")
        }

        async fn create_transfer(
            &self,
            _command: CreateOwnershipTransfer,
        ) -> ServiceResult<CreateTransferResult> {
            unreachable!("manager lane never transfers ownership")
        }

        async fn decide_transfer(
            &self,
            _actor_user_id: &str,
            _transfer_id: &str,
            _action: TransferAction,
        ) -> ServiceResult<CommittedTransferOutcome> {
            unreachable!("manager lane never decides transfers")
        }

        async fn get_transfer(
            &self,
            _viewer_user_id: &str,
            _transfer_id: &str,
        ) -> ServiceResult<OwnershipTransfer> {
            unreachable!("manager lane never reads transfers")
        }

        async fn list_transfers(
            &self,
            _query: ListOwnershipTransfers,
        ) -> ServiceResult<OwnershipTransferPage> {
            unreachable!("manager lane never lists transfers")
        }
    }

    struct FixedHook {
        allowed: BTreeMap<String, bool>,
    }

    #[async_trait]
    impl BotAuthorityHook for FixedHook {
        async fn can_manage(&self, user_id: &str, _bot_id: &str) -> ServiceResult<bool> {
            Ok(self.allowed.get(user_id).copied().unwrap_or(false))
        }

        async fn require_owner(&self, _user_id: &str, _bot_id: &str) -> ServiceResult<()> {
            unreachable!("manager lane never asks owner questions")
        }
    }

    fn caller() -> bcs_service_api::application::v1::AuthenticatedCaller {
        bcs_service_api::application::v1::AuthenticatedCaller {
            tenant: None,
            user: Some(bcs_service_api::application::v1::AuthenticatedUserIdentity {
                id: "user-a".to_string(),
                username: "user-a".to_string(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        }
    }

    fn service(allowed: bool) -> (Arc<RecordingCore>, BotManagerServiceImpl) {
        let core = Arc::new(RecordingCore::default());
        let hook: Arc<dyn BotAuthorityHook> = Arc::new(FixedHook {
            allowed: BTreeMap::from([("user-a".to_string(), allowed)]),
        });
        let impl_ = BotManagerServiceImpl::new(core.clone(), hook);
        (core, impl_)
    }

    #[tokio::test]
    async fn list_enumerates_pages_and_projects_the_requested_window() {
        let (core, facade) = service(true);
        let page = facade
            .list_managers(ListBotManagers {
                caller: caller(),
                bot_id: "bot-a".to_string(),
                offset: 0,
                limit: 20,
            })
            .await
            .expect("list succeeds");
        assert_eq!(page.bot_id, "bot-a");
        assert_eq!(page.owner_user_id, "user-a");
        // The test double's short page (fewer users than the 100-user
        // enumeration page) ends the enumeration: total is exact.
        assert_eq!(page.total, PAGE as u64);
        assert_eq!(page.items.len(), PAGE);
        assert_eq!(page.items[0].user_id, "user-0");
        assert_eq!(page.items[0].actor_id, "human_user-0");
        assert_eq!(page.items[0].role, "manager");
        // The owner never enters the page.
        assert!(page.items.iter().all(|item| item.user_id != "user-a"));
        // The lane read through the Core only (one bounded page needed).
        let lists = core.lists.lock().expect("lists lock");
        assert_eq!(lists.len(), 1);
        assert_eq!(lists[0], ("bot-a".to_string(), 0, 100));
    }

    #[tokio::test]
    async fn list_denies_callers_without_a_current_role() {
        let (_, facade) = service(false);
        let err = facade
            .list_managers(ListBotManagers {
                caller: caller(),
                bot_id: "bot-a".to_string(),
                offset: 0,
                limit: 20,
            })
            .await
            .expect_err("hook denial surfaces");
        assert!(matches!(err, ApplicationError::Forbidden(_)));
    }

    #[tokio::test]
    async fn list_rejects_out_of_range_pagination_and_requires_a_human() {
        let (_, facade) = service(true);
        for limit in [0, 101] {
            let err = facade
                .list_managers(ListBotManagers {
                    caller: caller(),
                    bot_id: "bot-a".to_string(),
                    offset: 0,
                    limit,
                })
                .await
                .expect_err("limit bounds enforced");
            assert_eq!(err.code(), "invalid_request");
        }
        let err = facade
            .list_managers(ListBotManagers {
                caller: bcs_service_api::application::v1::AuthenticatedCaller {
                    tenant: None,
                    user: None,
                    bot: None,
                    app: None,
                    access_key: None,
                },
                bot_id: "bot-a".to_string(),
                offset: 0,
                limit: 20,
            })
            .await
            .expect_err("human caller required");
        assert!(matches!(err, ApplicationError::Forbidden(_)));
    }

    #[tokio::test]
    async fn grant_revoke_forward_verified_human_and_direct_semantics() {
        let (core, facade) = service(true);
        let granted = facade
            .grant_manager(GrantBotManager {
                caller: caller(),
                bot_id: "bot-a".to_string(),
                user_id: "user-b".to_string(),
            })
            .await
            .expect("grant succeeds");
        assert_eq!(granted.role, "manager");
        assert!(granted.changed);
        let revoked = facade
            .revoke_manager(RevokeBotManager {
                caller: caller(),
                bot_id: "bot-a".to_string(),
                user_id: "user-b".to_string(),
            })
            .await
            .expect("revoke succeeds");
        assert!(revoked.revoked);
        assert_eq!(revoked.remaining_team_sources, vec!["team-a".to_string()]);

        let mutations = core.mutations.lock().expect("mutations lock");
        assert_eq!(mutations.len(), 2);
        // The VERIFIED caller (never a body value) is the audit actor.
        assert_eq!(mutations[0].0, AuditActor::Human { user_id: "user-a".into() });
        assert_eq!(
            mutations[0].2,
            ManagerMutation::GrantDirect { user_id: "user-b".into() }
        );
        assert_eq!(mutations[1].2, ManagerMutation::RevokeNonTeam { user_id: "user-b".into() });
    }

    #[tokio::test]
    async fn blank_identities_are_rejected_before_any_store_call() {
        let (core, facade) = service(true);
        let err = facade
            .grant_manager(GrantBotManager {
                caller: caller(),
                bot_id: "  ".to_string(),
                user_id: "user-b".to_string(),
            })
            .await
            .expect_err("blank bot id");
        assert_eq!(err.code(), "invalid_request");
        let err = facade
            .revoke_manager(RevokeBotManager {
                caller: caller(),
                bot_id: "bot-a".to_string(),
                user_id: " ".to_string(),
            })
            .await
            .expect_err("blank user id");
        assert_eq!(err.code(), "invalid_request");
        assert!(core.mutations.lock().expect("mutations lock").is_empty());
    }

    #[tokio::test]
    async fn owner_target_conflict_maps_to_the_transfer_lane_code() {
        let (core, facade) = service(true);
        *core.next_error.lock().expect("error lock") = Some(ServiceError::Authority(
            AuthorityError::Conflict("owner target".to_string()),
        ));
        let err = facade
            .grant_manager(GrantBotManager {
                caller: caller(),
                bot_id: "bot-a".to_string(),
                user_id: "user-owner".to_string(),
            })
            .await
            .expect_err("owner target is a transfer-lane conflict");
        assert!(matches!(err, ApplicationError::Conflict { .. }));
        assert_eq!(err.code(), "owner_role_requires_transfer");
    }

    #[tokio::test]
    async fn authority_branches_keep_their_shared_fixed_codes() {
        let (core, facade) = service(true);
        *core.next_error.lock().expect("error lock") = Some(ServiceError::Authority(
            AuthorityError::Forbidden("actor role".to_string()),
        ));
        let err = facade
            .revoke_manager(RevokeBotManager {
                caller: caller(),
                bot_id: "bot-a".to_string(),
                user_id: "user-b".to_string(),
            })
            .await
            .expect_err("forbidden");
        assert_eq!(err.code(), "forbidden");

        *core.next_error.lock().expect("error lock") = Some(ServiceError::Authority(
            AuthorityError::InvalidSubject("not live".to_string()),
        ));
        let err = facade
            .revoke_manager(RevokeBotManager {
                caller: caller(),
                bot_id: "bot-a".to_string(),
                user_id: "user-gone".to_string(),
            })
            .await
            .expect_err("invalid subject");
        assert_eq!(err.code(), "invalid_subject");

        *core.next_error.lock().expect("error lock") = Some(ServiceError::Authority(
            AuthorityError::OwnershipNotInitialized {
                bot_id: "bot-a".to_string(),
                env: "test".to_string(),
            },
        ));
        let err = facade
            .revoke_manager(RevokeBotManager {
                caller: caller(),
                bot_id: "bot-a".to_string(),
                user_id: "user-b".to_string(),
            })
            .await
            .expect_err("uninitialized");
        assert_eq!(err.code(), "ownership_not_initialized");

        *core.next_error.lock().expect("error lock") = Some(ServiceError::InternalError(
            "storage".to_string(),
        ));
        let err = facade
            .revoke_manager(RevokeBotManager {
                caller: caller(),
                bot_id: "bot-a".to_string(),
                user_id: "user-b".to_string(),
            })
            .await
            .expect_err("internal");
        assert_eq!(err.code(), "internal_error");
    }
}