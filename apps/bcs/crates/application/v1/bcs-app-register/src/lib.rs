//! Versioned bot registration use case for the BCN V1 API.
//!
//! Unscoped v1 tokens retain the legacy payload, TTL and onboarding behavior
//! and are interchangeable with tokens from the legacy registration route.
//! Provider-scoped v2 tokens carry signed owner, Provider and mode restrictions;
//! both HTTP surfaces delegate scoped registration to this facade.
//! The injected core owns authorization and all scoped registration mutations.
//! `POST /register` is anonymous and the register token is its only credential.

use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use bcs_domain::{
    register_token_decode_and_verify, register_token_encode, RegisterTokenPayload, RegisterTokenError,
};
use bcs_domain::provider_registration_token::{
    self, ProviderRegisterTokenPayload, ProviderRegistrationMode,
};
use bcs_domain::BotCapabilities;
use bcs_service_api::{
    ServiceError,
    types::bot_authority::{
        AuthorityActor, AuthorityMutation, OwnershipInitialization,
    },
    core::registry::{BotRegistryCoreService, TrustedBotRegistration},
};
use bcs_service_api::application::v1::{
    ApplicationError, BotRegistration, BotRegistrationScope,
    IssueRegisterToken, RegisterBot, RegisterService, RegisterTokenScope, RegisterTokenView,
};
use bcs_service_api::core::provider_registration::{
    ProviderRegistrationCoreService, RegisterProviderBot,
};

/// Register-token lifetime, matching the legacy `GET /register/token` route.
pub const REGISTER_TOKEN_TTL_SECONDS: u64 = 21600; // 6 hours
const REGISTER_TOKEN_NOTE: &str = "Use this token for bot registration within 6 hours";

pub struct RegisterServiceImpl {
    /// Registry core of the trusted (ownership-initialized)
    /// registration chain. Its Bot Store performs the atomic
    /// lifecycle transaction; the application never touches
    /// a repo.
    registry: Option<Arc<dyn BotRegistryCoreService>>,
    token_secret: Vec<u8>,
    provider_registration: Option<Arc<dyn ProviderRegistrationCoreService>>,
}

impl RegisterServiceImpl {
    pub fn new(token_secret: Vec<u8>) -> Self {
        Self {
            registry: None,
            token_secret,
            provider_registration: None,
        }
    }

    /// Wire the trusted registration chain (required for
    /// v1/v2 trusted registration; fail-closed otherwise).
    pub fn with_registry(mut self, core: Arc<dyn BotRegistryCoreService>) -> Self {
        self.registry = Some(core);
        self
    }

    fn registry(&self) -> Result<&dyn BotRegistryCoreService, ApplicationError> {
        self.registry.as_deref().ok_or_else(|| {
            ApplicationError::internal("trusted bot registration is not configured")
        })
    }

    /// The real initiator is the verified registration-token
    /// Human; the mutation id is a fresh server-side
    /// idempotence key, never client data.
    fn ownership_initialization(staff_no: &str) -> OwnershipInitialization {
        OwnershipInitialization {
            owner_user_id: staff_no.to_string(),
            mutation: AuthorityMutation {
                mutation_id: uuid::Uuid::new_v4().to_string(),
                actor: AuthorityActor::Human {
                    user_id: staff_no.to_string(),
                },
            },
        }
    }

    /// Enable scoped registration; the legacy constructor leaves it disabled.
    pub fn with_provider_registration(
        mut self,
        core: Arc<dyn ProviderRegistrationCoreService>,
    ) -> Self {
        self.provider_registration = Some(core);
        self
    }

    fn provider_registration(&self) -> Result<&dyn ProviderRegistrationCoreService, ApplicationError> {
        self.provider_registration.as_deref().ok_or_else(|| {
            ApplicationError::invalid("invalid_request", "provider registration is not enabled")
        })
    }

    async fn register_provider_bot(
        &self,
        command: &RegisterBot,
        bot_name: &str,
        payload: ProviderRegisterTokenPayload,
    ) -> Result<BotRegistration, ApplicationError> {
        let mode = command.mode.unwrap_or(ProviderRegistrationMode::Plugin);
        if !payload.allowed_modes.contains(&mode) {
            return Err(ApplicationError::forbidden("registration mode is not authorized"));
        }
        let provider_bot_ref = command.provider_bot_ref.as_deref()
            .filter(|reference| !reference.trim().is_empty())
            .ok_or_else(|| ApplicationError::invalid(
                "invalid_request", "provider_bot_ref is required for provider registration",
            ))?;
        let owner = payload.id.strip_prefix("human_")
            .ok_or(ApplicationError::Unauthenticated)?;
        let initialization = Self::ownership_initialization(owner);
        let result = self.provider_registration()?.register_with_ownership(
            RegisterProviderBot {
                token_auth_mode: payload.auth_mode,
                provider_id: payload.provider_id,
                provider_bot_ref: provider_bot_ref.to_string(),
                owner: owner.to_string(),
                mode,
                bot_name: bot_name.to_string(),
                webhook_url: command.webhook_url.clone(),
            },
            initialization,
        ).await.map_err(provider_registration_error)?;
        let record = result.record;
        Ok(BotRegistration {
            bot_name: record.bot_name,
            bot_uuid: record.bot_uuid,
            bot_token: record.bot_token,
            registration: Some(BotRegistrationScope {
                auth_mode: result.auth_mode,
                agent_code: result.identity.agent_code,
                bot_slug: result.identity.bot_slug,
                provider_id: record.provider_id,
                provider_bot_ref: record.provider_bot_ref,
                mode: record.mode,
                webhook_url: record.webhook_url,
                effective_webhook_url: result.effective_webhook_url,
            }),
        })
    }
}

/// Public error mapping of the trusted chain: storage
/// failures map to Internal; business conflicts map to
/// their typed application errors; no SQL text and no
/// credential content is echoed.
fn core_registration_error(error: ServiceError) -> ApplicationError {
    match &error {
        ServiceError::Conflict(message) => {
            ApplicationError::conflict("registration_conflict", message.clone())
        }
        ServiceError::InvalidOperation { message, .. } => {
            ApplicationError::invalid("invalid_request", message.clone())
        }
        ServiceError::Forbidden(message) => {
            ApplicationError::forbidden(message.clone())
        }
        _ => ApplicationError::internal("trusted registration failed"),
    }
}

fn provider_registration_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::Forbidden(_) => ApplicationError::forbidden("provider registration is forbidden"),
        ServiceError::ProviderNotFound(_) => ApplicationError::not_found(
            "provider_not_found", "provider not found",
        ),
        ServiceError::Conflict(_) => ApplicationError::conflict(
            "registration_conflict", "registration conflicts with an existing registration",
        ),
        ServiceError::InvalidOperation { .. } => ApplicationError::invalid(
            "invalid_request", "invalid provider registration request",
        ),
        ServiceError::ProviderNotReadyForDownlink { .. } => ApplicationError::invalid(
            "invalid_request", "provider is not ready for gateway registration",
        ),
        _ => ApplicationError::internal("provider registration failed"),
    }
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[async_trait]
impl RegisterService for RegisterServiceImpl {
    async fn issue_register_token(
        &self,
        command: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        let user = command.caller.user.as_ref().ok_or_else(|| {
            ApplicationError::forbidden(
                "register token issuance requires an authenticated Human principal",
            )
        })?;
        if let Some(provider_id) = command.provider_id {
            if provider_id.trim().is_empty() {
                return Err(ApplicationError::invalid("invalid_request", "provider_id must not be blank"));
            }
            let authorization = self.provider_registration()?
                .authorize(&provider_id, &user.id).await.map_err(provider_registration_error)?;
            let payload = ProviderRegisterTokenPayload {
                v: 2,
                purpose: "provider_bot_registration".to_string(),
                id: format!("human_{}", user.id),
                provider_id: provider_id.clone(),
                allowed_modes: authorization.allowed_modes.clone(),
                auth_mode: Some(authorization.auth_mode),
                exp: now_secs() + REGISTER_TOKEN_TTL_SECONDS,
            };
            return Ok(RegisterTokenView {
                token: provider_registration_token::encode(&payload, &self.token_secret),
                expires_at: payload.exp * 1000,
                note: REGISTER_TOKEN_NOTE.to_string(),
                registration: Some(RegisterTokenScope {
                    token_version: 2,
                    provider_id,
                    allowed_modes: authorization.allowed_modes,
                }),
            });
        }
        let payload = RegisterTokenPayload {
            v: 1,
            id: format!("human_{}", user.id),
            exp: now_secs() + REGISTER_TOKEN_TTL_SECONDS,
        };
        Ok(RegisterTokenView {
            token: register_token_encode(&payload, &self.token_secret),
            expires_at: payload.exp * 1000,
            note: REGISTER_TOKEN_NOTE.to_string(),
            registration: None,
        })
    }

    async fn register_bot(
        &self,
        command: RegisterBot,
    ) -> Result<BotRegistration, ApplicationError> {
        let bot_name = command.bot_name.trim();
        let name_len = bot_name.chars().count();
        if name_len < 2 || name_len > 64 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                format!("bot-name must be 2-64 characters, got {name_len}"),
            ));
        }
        let payload = match register_token_decode_and_verify(&command.token, &self.token_secret) {
            Ok(payload) => payload,
            Err(RegisterTokenError::UnsupportedVersion) => {
                let payload = provider_registration_token::decode_and_verify(&command.token, &self.token_secret)
                    .map_err(|_| ApplicationError::Unauthenticated)?;
                return self.register_provider_bot(&command, bot_name, payload).await;
            }
            Err(_) => return Err(ApplicationError::Unauthenticated),
        };
        // OpenAPI contract (spec §10.1): strict parsing stays;
        // a v1 token never carries Provider/scoped fields.
        if command.mode.unwrap_or(ProviderRegistrationMode::Plugin) != ProviderRegistrationMode::Plugin
            || command.provider_bot_ref.is_some()
            || command.webhook_url.is_some()
        {
            return Err(ApplicationError::invalid(
                "invalid_request", "legacy register tokens only support ordinary upstream registration",
            ));
        }
        let staff_no = payload.id.trim_start_matches("human_").to_string();
        // Trusted v1 registration (spec §10.1/§10.2): no
        // connect→onboard orchestration and no warn-and-succeed
        // any more. The Bot Store commits Bot, Human, owner,
        // version, default profile and the initialization audit
        // in ONE transaction; persistence failure returns no
        // credential (removed legacy behavior: swallowed onboard
        // failure followed by success).
        let committed = self
            .registry()?
            .register_with_ownership(TrustedBotRegistration {
                capabilities: BotCapabilities {
                    name: Some(bot_name.to_string()),
                    visibility: "protected".to_string(),
                    ..BotCapabilities::default()
                },
                initialization: Self::ownership_initialization(&staff_no),
            })
            .await
            .map_err(core_registration_error)?;
        tracing::info!(
            bot_uuid = %committed.bot_uuid,
            "register: trusted registration committed"
        );
        Ok(BotRegistration {
            bot_name: committed.bot_name,
            bot_uuid: committed.bot_uuid,
            bot_token: committed.bot_token,
            registration: None,
        })
    }
}
// ---------------------------------------------------------------------------
// Tests.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    mod http;
    mod provider_registration;
    use super::*;

    use bcs_bot::BotCore;
    use bcs_bot_store::MemoryBotRepo;
    use bcs_service_api::application::v1::{
        AuthenticatedCaller, AuthenticatedUserIdentity,
    };
    use bcs_service_api::port::repo::{BotAuthorityRepoPort, BotRepoPort};
    use bcs_service_api::core::provider_registration::{
        ProviderRegistrationCoreService, ProviderRegistrationRecord,
        ProviderRegistrationResult, RegisterProviderBot,
    };
    use bcs_service_api::types::bot_authority::{
        AuthorityActor, AuthorityMutation, BotAccessRelation,
        OwnershipInitialization,
    };

    const SECRET: &[u8] = b"test-secret-key-32-bytes-long!!!";

    fn human_caller(id: &str) -> AuthenticatedCaller {
        AuthenticatedCaller {
            tenant: None,
            user: Some(AuthenticatedUserIdentity {
                id: id.to_string(),
                username: id.to_string(),
                display_name: None,
                full_name: None,
            }),
            bot: None,
            app: None,
            access_key: None,
        }
    }

    /// The REAL trusted chain (memory projection): one
    /// bot repo behind the real `BotCore`; fixtures seed
    /// the Human registry the way the Backend-synced SQL
    /// paths provide it.
    fn memory_chain() -> (RegisterServiceImpl, Arc<MemoryBotRepo>) {
        // Isolated durability directory: the memory repo
        // persists bot-info files and must not leak into
        // the crate working directory.
        let dir: &'static tempfile::TempDir =
            Box::leak(Box::new(
                tempfile::tempdir().expect("tempdir"),
            ));
        let repo = Arc::new(MemoryBotRepo::with_base_dir(
            dir.path().to_path_buf(),
        ));
        let core: Arc<BotCore> = Arc::new(BotCore::with_repo(repo.clone()));
        let svc =
            RegisterServiceImpl::new(SECRET.to_vec()).with_registry(core);
        (svc, repo)
    }

    async fn seed_human(repo: &MemoryBotRepo, user_id: &str) {
        // A live Human ACTOR row on the shared registry —
        // the authority guards' Human existence source.
        use bcs_service_api::port::repo::BotRepoPort;

        repo.ensure_human_actor(user_id, user_id)
            .await
            .expect("ensure fixture Human actor row");
    }

    /// Whether the user's Human ACTOR row exists and is a
    /// live Human on the shared registry.
    async fn known_human_actor_row(
        repo: &MemoryBotRepo,
        user_id: &str,
    ) -> bool {
        use bcs_service_api::ActorKind;
        use bcs_service_api::port::repo::BotRepoPort;

        repo.get(&format!("human_{user_id}"))
            .await
            .map(|row| row.actor_kind == ActorKind::Human)
            .unwrap_or(false)
    }

    async fn owner_relation(
        repo: &MemoryBotRepo,
        bot_id: &str,
    ) -> Option<BotAccessRelation> {
        repo.role("human_staff-9", bot_id).await.expect("role read")
    }

    pub(super) struct FakeProviderRegistration {
        pub authorized: std::sync::Mutex<Vec<(String, String)>>,
        pub registered: std::sync::Mutex<Vec<RegisterProviderBot>>,
        pub initializations:
            std::sync::Mutex<Vec<OwnershipInitialization>>,
        pub issue_error: std::sync::Mutex<Option<ServiceError>>,
        pub register_error: std::sync::Mutex<Option<ServiceError>>,
    }

    impl FakeProviderRegistration {
        pub(super) fn new() -> Self {
            Self {
                authorized: std::sync::Mutex::new(Vec::new()),
                registered: std::sync::Mutex::new(Vec::new()),
                initializations: std::sync::Mutex::new(Vec::new()),
                issue_error: std::sync::Mutex::new(None),
                register_error: std::sync::Mutex::new(None),
            }
        }
    }

    #[async_trait]
    impl ProviderRegistrationCoreService for FakeProviderRegistration {
        async fn authorize(
            &self,
            provider: &str,
            owner: &str,
        ) -> Result<
            bcs_service_api::core::provider_registration::ProviderRegistrationAuthorization,
            ServiceError,
        > {
            self.authorized
                .lock()
                .unwrap()
                .push((provider.into(), owner.into()));
            if let Some(error) = self.issue_error.lock().unwrap().take() {
                return Err(error);
            }
            Ok(
                bcs_service_api::core::provider_registration::ProviderRegistrationAuthorization {
                    allowed_modes: vec![
                        ProviderRegistrationMode::Plugin,
                        ProviderRegistrationMode::Gateway,
                    ],
                    auth_mode: bcs_domain::ProviderAuthMode::StaticBearer,
                },
            )
        }

        /// The legacy `register` surface must no longer be a
        /// facade path; trusted registration always carries a
        /// real initialization.
        async fn register(
            &self,
            _command: RegisterProviderBot,
        ) -> Result<ProviderRegistrationResult, ServiceError> {
            Err(ServiceError::InternalError(
                "legacy provider register surface must not be used \
                 by trusted registration"
                    .into(),
            ))
        }

        async fn register_with_ownership(
            &self,
            command: RegisterProviderBot,
            initialization: OwnershipInitialization,
        ) -> Result<ProviderRegistrationResult, ServiceError> {
            self.registered.lock().unwrap().push(command.clone());
            self.initializations.lock().unwrap().push(initialization);
            if let Some(error) = self.register_error.lock().unwrap().take()
            {
                return Err(error);
            }
            let identity =
                bcs_service_api::types::provider_identity::ProviderBotIdentity {
                    agent_code: None,
                    provider_bot_ref: command.provider_bot_ref.clone(),
                    bot_slug: None,
                };
            Ok(ProviderRegistrationResult {
                effective_webhook_url: if command.mode
                    == ProviderRegistrationMode::Gateway
                {
                    Some(
                        command
                            .webhook_url
                            .clone()
                            .unwrap_or_else(|| "https://example.test/default".into()),
                    )
                } else {
                    None
                },
                record: ProviderRegistrationRecord {
                    provider_id: command.provider_id.clone(),
                    provider_bot_ref: command.provider_bot_ref.clone(),
                    owner: command.owner,
                    mode: command.mode,
                    bot_name: command.bot_name,
                    bot_uuid: "scoped-bot".into(),
                    bot_token: "runtime-token".into(),
                    webhook_url: command.webhook_url,
                },
                identity,
                auth_mode: bcs_domain::ProviderAuthMode::StaticBearer,
            })
        }
    }

    #[tokio::test]
    async fn issuance_round_trips_through_verification() {
        let (svc, _) = memory_chain();
        let view = svc
            .issue_register_token(IssueRegisterToken {
                provider_id: None,
                caller: human_caller("staff-1"),
            })
            .await
            .expect("issue");
        assert_eq!(view.note, REGISTER_TOKEN_NOTE);
        let payload =
            register_token_decode_and_verify(&view.token, SECRET)
                .expect("verify");
        assert_eq!(payload.id, "human_staff-1");
        assert_eq!(payload.v, 1);
        assert!(payload.exp >= now_secs() + REGISTER_TOKEN_TTL_SECONDS - 5);
        assert_eq!(view.expires_at, payload.exp * 1000);
    }

    #[tokio::test]
    async fn issuance_rejects_non_human_principals() {
        let (svc, _) = memory_chain();
        let error = svc
            .issue_register_token(IssueRegisterToken {
                provider_id: None,
                caller: AuthenticatedCaller {
                    tenant: None,
                    user: None,
                    bot: None,
                    app: None,
                    access_key: None,
                },
            })
            .await
            .expect_err("bot-only principal is rejected");
        assert!(matches!(error, ApplicationError::Forbidden(_)));
    }

    /// The v1 trusted path: the verified token owner is the
    /// true initiator, the chain commits through the real
    /// core, and NO connect→onboard orchestration exists
    /// any more (the legacy warn-and-succeed is removed).
    #[tokio::test]
    async fn trusted_registration_commits_owner_with_verified_token() {
        let (svc, repo) = memory_chain();
        seed_human(&repo, "staff-9").await;
        let token_view = svc
            .issue_register_token(IssueRegisterToken {
                provider_id: None,
                caller: human_caller("staff-9"),
            })
            .await
            .expect("issue");
        let registration = svc
            .register_bot(RegisterBot {
                mode: None,
                provider_bot_ref: None,
                webhook_url: None,
                token: token_view.token,
                bot_name: "  测试机器人  ".to_string(),
            })
            .await
            .expect("register");
        assert_eq!(registration.bot_name, "测试机器人");
        assert!(matches!(
            owner_relation(&repo, &registration.bot_uuid).await,
            Some(BotAccessRelation::Owner)
        ));
        let bot = repo
            .get(&registration.bot_uuid)
            .await
            .expect("committed bot");
        assert_eq!(bot.created_by.as_deref(), Some("staff-9"));
        assert_eq!(bot.bot_uuid, registration.bot_uuid);
        // The returned credential is exactly the persisted
        // runtime token of the committed Bot.
        assert_eq!(
            repo.load_token(&registration.bot_uuid).await.as_deref(),
            Some(registration.bot_token.as_str())
        );
    }

    #[tokio::test]
    async fn trusted_registration_fails_closed_without_wired_registry() {
        let svc = RegisterServiceImpl::new(SECRET.to_vec());
        let token = register_token_encode(
            &RegisterTokenPayload {
                v: 1,
                id: "human_staff-1".to_string(),
                exp: now_secs() + 60,
            },
            SECRET,
        );
        let error = svc
            .register_bot(RegisterBot {
                mode: None,
                provider_bot_ref: None,
                webhook_url: None,
                token,
                bot_name: "ok-name".to_string(),
            })
            .await
            .expect_err("unset chain is fail-closed");
        assert!(matches!(error, ApplicationError::Internal(_)));
    }

    #[tokio::test]
    async fn registration_creates_the_owner_actor_row() {
        // Spec §10.2 literal (ruling T4-1): the trusted
        // registration ensures the owner's Human ACTOR row
        // in the SAME commit (memory mirror: the shared
        // bots registry — no OAuth identity row involved).
        let (svc, repo) = memory_chain();
        assert!(
            !known_human_actor_row(&repo, "staff-1").await,
            "no Human actor row before the registration"
        );
        let token_view = svc
            .issue_register_token(IssueRegisterToken {
                provider_id: None,
                caller: human_caller("staff-1"),
            })
            .await
            .expect("issue");
        let registration = svc
            .register_bot(RegisterBot {
                mode: None,
                provider_bot_ref: None,
                webhook_url: None,
                token: token_view.token,
                bot_name: "ok-name".to_string(),
            })
            .await
            .expect("token-verified human registers");
        assert!(
            repo.get(&registration.bot_uuid).await.is_some()
        );
        assert!(
            known_human_actor_row(&repo, "staff-1").await,
            "the registration must leave a live Human actor row"
        );
    }

    #[tokio::test]
    async fn token_verification_failures_map_to_unauthenticated() {
        let (svc, _) = memory_chain();
        let _ignored = svc
            .issue_register_token(IssueRegisterToken {
                provider_id: None,
                caller: human_caller("staff-1"),
            })
            .await
            .expect("issue");
        for bad in ["not-a-token"] {
            let error = svc
                .register_bot(RegisterBot {
                    mode: None,
                    provider_bot_ref: None,
                    webhook_url: None,
                    token: bad.to_string(),
                    bot_name: "ok-name".to_string(),
                })
                .await
                .expect_err("malformed token is rejected");
            assert!(matches!(error, ApplicationError::Unauthenticated));
        }
        // Wrong-secret token (well-formed, different HMAC).
        let other = register_token_encode(
            &RegisterTokenPayload {
                v: 1,
                id: "human_staff-1".to_string(),
                exp: now_secs() + 60,
            },
            b"other-secret",
        );
        let error = svc
            .register_bot(RegisterBot {
                mode: None,
                provider_bot_ref: None,
                webhook_url: None,
                token: other,
                bot_name: "ok-name".to_string(),
            })
            .await
            .expect_err("foreign-secret token is rejected");
        assert!(matches!(error, ApplicationError::Unauthenticated));
    }

    #[tokio::test]
    async fn registration_rejects_invalid_name_lengths() {
        let (svc, _) = memory_chain();
        let too_long = "x".repeat(65);
        for bad in ["a", too_long.as_str()] {
            let error = svc
                .register_bot(RegisterBot {
                    mode: None,
                    provider_bot_ref: None,
                    webhook_url: None,
                    token: "unused".to_string(),
                    bot_name: bad.to_string(),
                })
                .await
                .expect_err("bad name length is rejected");
            assert!(matches!(error, ApplicationError::InvalidInput { .. }));
        }
    }
}
